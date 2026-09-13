"""Markdown bundle renderer for ck3_chronicler-441.

Pure renderers (``render_chronicle_md``, ``render_overview_md``,
``render_character_md``) plus an orchestrator (``build_export_bundle``)
that walks a tracked roster, hits the per-campaign session for bios +
events, composes per-character SVG shields via
:mod:`chronicler.export.coa_svg`, and writes everything into a caller-
supplied ``zipfile.ZipFile``.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chronicler.db.repository import (
    character_name_map,
    get_character,
    get_latest_biography_for_character,
    list_events_for_character,
)
from chronicler.export.coa_svg import render_coa_svg
from chronicler.export.event_roll import EventRollRow, build_event_roll
from chronicler.narrative.event_rendering import build_titles_map_from_primary_title
from chronicler.util.slug import slugify

log = logging.getLogger(__name__)


@dataclass
class BundleResult:
    """Returned from build_export_bundle for the route to log + tests
    to assert against."""

    files_written: int
    warnings: list[str] = field(default_factory=list)
    skipped_character_ids: list[int] = field(default_factory=list)


# ---------- pure renderers ----------


def render_chronicle_md(closing_chronicle_body: str) -> str:
    """The closing chronicle prose verbatim."""
    return closing_chronicle_body or ""


def render_character_md(
    *,
    ck3_id: int,
    first_name: str | None,
    nickname: str | None,
    dynasty_name: str | None,
    house_name: str | None,
    birth_date: str | None,
    death_date: str | None,
    culture: str | None,
    faith: str | None,
    role: str | None,
    biography_body: str | None,
    events: list[EventRollRow],
    had_events: bool = False,
    coa_svg_relpath: str | None,
) -> str:
    """Compose a character's markdown page. See spec §4 for layout."""
    name = first_name or f"Character {ck3_id}"
    title_parts = [name]
    if house_name:
        title_parts.append(house_name)
    if dynasty_name:
        title_parts.append(dynasty_name)
    lines: list[str] = [f"# {' · '.join(title_parts)}"]
    if nickname:
        lines.append(f"*{nickname}*")
    lines.append("")
    if coa_svg_relpath:
        lines.append(f"![Arms of {name}]({coa_svg_relpath})")
        lines.append("")
    vital_pairs: list[str] = []
    if birth_date:
        vital_pairs.append(f"**Born** {birth_date}")
    if death_date:
        vital_pairs.append(f"**Died** {death_date}")
    if vital_pairs:
        lines.append(" · ".join(vital_pairs))
    extras: list[str] = []
    if culture:
        extras.append(f"**Culture** {culture}")
    if faith:
        extras.append(f"**Faith** {faith}")
    if role:
        extras.append(f"**Role** {role}")
    if extras:
        lines.append(" · ".join(extras))
    lines.append("")

    if biography_body:
        lines.append("## Vita")
        lines.append("")
        lines.append(biography_body.strip())
        lines.append("")

    if events:
        lines.append("## Event roll")
        lines.append("")
        lines.append("| Date | Event |")
        lines.append("| --- | --- |")
        for row in events:
            lines.append(f"| {row.date} | {row.description} |")
        lines.append("")
    elif had_events:
        # The character had events, but all of them curated out as noise.
        lines.append("## Event roll")
        lines.append("")
        lines.append("_No notable events recorded._")
        lines.append("")

    return "\n".join(lines)


def render_overview_md(
    *,
    campaign_name: str,
    ck3_version: str | None,
    sealed_at: str,
    bookmark_date: str | None,
    current_in_game_date: str | None,
    tracked: list[dict[str, Any]],
    biography_count: int,
    event_count: int,
    warnings: list[str],
) -> str:
    lines: list[str] = [f"# {campaign_name}", ""]
    meta_bits: list[str] = []
    if ck3_version:
        meta_bits.append(f"CK3 version {ck3_version}")
    meta_bits.append(f"sealed {sealed_at[:10]}")
    if bookmark_date and current_in_game_date:
        meta_bits.append(f"bookmark {bookmark_date} → {current_in_game_date}")
    elif bookmark_date:
        meta_bits.append(f"bookmark {bookmark_date}")
    lines.append(" · ".join(meta_bits))
    lines.append("")
    lines.append(
        f"**Tracked souls:** {len(tracked)} · "
        f"**Vitae:** {biography_count} · "
        f"**Events:** {event_count}"
    )
    lines.append("")
    lines.append("## Tracked roster")
    lines.append("")
    if not tracked:
        lines.append("_No tracked souls._")
    else:
        for t in tracked:
            name = t.get("first_name") or f"Character {t['character_id']}"
            role = (t.get("role") or "").capitalize() or "(no role)"
            since = (t.get("added_at") or "")[:10]
            lines.append(f"- {name} (#{t['character_id']}) · {role} · since {since}")
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


# ---------- bundle assembly ----------


def build_export_bundle(
    *,
    zf: zipfile.ZipFile,
    factory,
    campaign_name: str,
    campaign_slug: str,
    ck3_version: str | None,
    closing_chronicle_body: str,
    closing_chronicle_generated_at: str,
    bookmark_date: str | None,
    current_in_game_date: str | None,
    tracked_character_ids: list[int],
    tracked_metadata: dict[int, dict[str, Any]],
    heraldry_dir: Path,
    palette: dict[str, list[int]],
) -> BundleResult:
    """Walk the tracked roster, render markdown + SVG, write into ``zf``.

    ``zf`` is owned by the caller (typically the route handler with an
    ``io.BytesIO``). All paths inside the zip are prefixed with
    ``<campaign_slug>-chronicle/`` so unzipping creates a single
    top-level directory. Returns counts + warnings the route can log.
    """
    root = f"{campaign_slug}-chronicle"
    warnings: list[str] = []
    skipped: list[int] = []
    biography_count = 0
    event_count = 0
    tracked_summaries: list[dict[str, Any]] = []

    # ck3_chronicler-2cc.1: one names map for the whole bundle so event-roll
    # participants (allies, spouses, employers, hosts) resolve to names.
    with factory() as session:
        names_map = character_name_map(session)

    # Walk tracked characters in stable order so the bundle is reproducible.
    for cid in sorted(tracked_character_ids):
        with factory() as session:
            char = get_character(session, cid)
            if char is None:
                warnings.append(f"Character #{cid} — tracked but not in per-campaign DB")
                skipped.append(cid)
                meta = tracked_metadata.get(cid, {})
                tracked_summaries.append(
                    {
                        "character_id": cid,
                        "first_name": None,
                        "role": meta.get("role"),
                        "added_at": meta.get("added_at"),
                    }
                )
                continue

            bio = get_latest_biography_for_character(session, cid)
            evts = list_events_for_character(session, cid)

            if bio is None:
                warnings.append(f"Character #{cid} ({char.first_name or 'unnamed'}) — no biography")
            else:
                biography_count += 1
            event_count += len(evts)

            # Compose the SVG shield. Skip + warn on missing CoA / asset.
            slug = slugify(char.first_name or "") or f"character-{cid}"
            char_filename = f"characters/{cid}-{slug}.md"
            svg_relpath: str | None = None
            if char.coa_json:
                try:
                    coa = json.loads(char.coa_json)
                except (TypeError, ValueError):
                    coa = None
                if isinstance(coa, dict) and palette and heraldry_dir.is_dir():
                    svg = render_coa_svg(
                        coa,
                        palette=palette,
                        heraldry_dir=heraldry_dir,
                        size=80,
                        label=f"Arms of {char.first_name or cid}",
                    )
                    if "data:image/png" in svg:
                        # At least one layer rendered — write the SVG.
                        zf.writestr(f"{root}/assets/heraldry/{cid}.svg", svg)
                        svg_relpath = f"../assets/heraldry/{cid}.svg"
                    else:
                        warnings.append(
                            f"Character #{cid} — CoA references missing texture(s); shield omitted"
                        )
                else:
                    warnings.append(
                        f"Character #{cid} — heraldry not available (palette/dir missing)"
                    )
            else:
                warnings.append(
                    f"Character #{cid} — no resolved CoA (save-tail hasn't refreshed yet?)"
                )

            event_dicts = [
                {
                    "date": e.event_date,
                    "type": e.event_type,
                    "payload": json.loads(e.payload_json) if e.payload_json else {},
                }
                for e in evts
            ]
            titles_map = build_titles_map_from_primary_title(char.primary_title_json)
            roll = build_event_roll(event_dicts, names_map=names_map, titles_map=titles_map)

            md = render_character_md(
                ck3_id=cid,
                first_name=char.first_name,
                nickname=char.nickname,
                dynasty_name=char.dynasty_name,
                house_name=char.house_name,
                birth_date=char.birth_date,
                death_date=char.death_date,
                culture=char.culture,
                faith=char.faith,
                role=tracked_metadata.get(cid, {}).get("role"),
                biography_body=bio.body if bio else None,
                events=roll,
                had_events=bool(evts),
                coa_svg_relpath=svg_relpath,
            )
            zf.writestr(f"{root}/{char_filename}", md)
            tracked_summaries.append(
                {
                    "character_id": cid,
                    "first_name": char.first_name,
                    "role": tracked_metadata.get(cid, {}).get("role"),
                    "added_at": tracked_metadata.get(cid, {}).get("added_at"),
                }
            )

    # Top-level files.
    zf.writestr(f"{root}/chronicle.md", render_chronicle_md(closing_chronicle_body))
    overview = render_overview_md(
        campaign_name=campaign_name,
        ck3_version=ck3_version,
        sealed_at=closing_chronicle_generated_at,
        bookmark_date=bookmark_date,
        current_in_game_date=current_in_game_date,
        tracked=tracked_summaries,
        biography_count=biography_count,
        event_count=event_count,
        warnings=warnings,
    )
    zf.writestr(f"{root}/overview.md", overview)

    return BundleResult(
        files_written=len(zf.namelist()),
        warnings=warnings,
        skipped_character_ids=skipped,
    )
