"""Closing-ceremony orchestrator (ck3_chronicler-z7l).

When a user marks a campaign complete, this module synthesises every
tracked character's latest biography into a single meta-narrative
"dynasty chronicle" via the configured :class:`NarrativeProvider`.

Three-phase shape mirrors :mod:`chronicler.narrative.pipeline` — the
session is never held during the LLM call — so the closing call doesn't
block other API requests using the same connection pool. The phases
are:

1. **Read** — open per-campaign session; snapshot tracked-character
   biographies + names; close.
2. **Generate** — call ``provider.generate(request)`` with no DB held.
3. **Persist** — open the registry, write the chronicle to the
   campaign row, return the body.

The route layer (``routes/closing.py``) handles the archive-flag flip
after this returns successfully. Generating without archiving is fine —
re-running the closing on a still-active campaign overwrites the prior
chronicle, useful for "preview the closing then commit".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

from chronicler.db.engine import session_scope
from chronicler.db.models import Character
from chronicler.db.registry import (
    get_campaign_by_id,
    list_tracked_characters,
    set_campaign_closing_chronicle,
)
from chronicler.db.repository import get_latest_biography_for_character
from chronicler.narrative.prose_io import assemble_system_prompt
from chronicler.narrative.provider import NarrativeProvider, NarrativeRequest

log = logging.getLogger(__name__)

PROMPT_TEMPLATE_VERSION = "campaign_chronicle_v1"
# Issue #19: the register now comes from the prose dir, so this template
# is no longer loaded — the constant stays as the provenance record for
# the version tag persisted on each chronicle. The file itself is
# reconciled into the prose template by the init-prose work (#20).
# Issue #20: the campaign_chronicle_v1.md template this pointed at is
# gone. Its body was byte-identical to the prose dir's
# voice/chronicle-export.md (the 0224 migration had already copied it),
# and issue #19 stopped every transport from reading the in-repo copy.
# Only the version tag above survives — it is persisted on the campaign
# row. The rules now live in the user's prose directory.


@dataclass(frozen=True, slots=True)
class ClosingOutcome:
    """Result of an attempted closing-chronicle generation."""

    body: str | None
    error: str | None
    generated_at: str | None


@dataclass(frozen=True, slots=True)
class _BiographySnapshot:
    """Detached view of one tracked character's latest biography.

    ck3_chronicler-2wv: enriched with house/dynasty + family relations
    so the closing-chronicle prompt can hand the model resolved
    relationship facts (Erik+Sigrid are spouses; their children are X,
    Y) instead of a list of biographies the model has to reverse-
    engineer connections from. Without this the v0.7 smoke produced
    "granddaughter" hallucinations across spouse pairs.
    """

    character_id: int
    label: str  # "first_name" or "first_name, called nickname"
    birth_date: str | None
    death_date: str | None
    culture: str | None
    faith: str | None
    body: str | None  # None if no biography yet
    house_name: str | None = None
    dynasty_name: str | None = None
    # ck3_chronicler-60m {id, name}-resolved family_data, parsed from the
    # character's save_snapshot_json. None when the character has no
    # persisted save record (untracked-then-tracked, no save-tail tick yet).
    family_resolved: dict | None = None
    # Convenience flag derived from death_date — saves the prompt
    # builder from re-checking "is None" everywhere. True means alive at
    # the latest persisted state.
    is_alive: bool = True


def _format_label(c: Character) -> str:
    name = c.first_name or "(unknown)"
    if c.nickname:
        return f"{name}, called {c.nickname}"
    return name


def _parse_family_resolved(snapshot_json: str | None) -> dict | None:
    """Pull the ck3_chronicler-60m {id, name}-resolved family_data out of
    Character.save_snapshot_json, or None if the JSON is missing /
    malformed / lacks family_data."""
    if not snapshot_json:
        return None
    try:
        record = json.loads(snapshot_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    fd = record.get("family_data")
    return fd if isinstance(fd, dict) else None


def _snapshot_tracked_biographies(
    *,
    factory: sessionmaker,
    tracked_ids: list[int],
) -> list[_BiographySnapshot]:
    """Phase 1: read tracked chars + their latest biographies, release session.

    Tracked chars without biographies are still surfaced (with
    ``body=None``) so the LLM knows the dynasty had members in
    those slots even when no narrative was generated for them.
    """
    if not tracked_ids:
        return []
    out: list[_BiographySnapshot] = []
    with session_scope(factory) as session:
        from sqlalchemy import select

        rows = (
            session.execute(select(Character).where(Character.ck3_id.in_(tracked_ids)))
            .scalars()
            .all()
        )
        by_id = {r.ck3_id: r for r in rows}
        for cid in tracked_ids:
            char = by_id.get(cid)
            bio = get_latest_biography_for_character(session, cid)
            if char is None:
                # Tracked but never landed in the DB — keep a placeholder
                # so the chronicle prompt sees the slot.
                out.append(
                    _BiographySnapshot(
                        character_id=cid,
                        label=f"(unknown character {cid})",
                        birth_date=None,
                        death_date=None,
                        culture=None,
                        faith=None,
                        body=bio.body if bio else None,
                    )
                )
                continue
            out.append(
                _BiographySnapshot(
                    character_id=cid,
                    label=_format_label(char),
                    birth_date=char.birth_date,
                    death_date=char.death_date,
                    culture=char.culture,
                    faith=char.faith,
                    body=bio.body if bio else None,
                    house_name=char.house_name,
                    dynasty_name=char.dynasty_name,
                    family_resolved=_parse_family_resolved(char.save_snapshot_json),
                    is_alive=char.death_date is None,
                )
            )
    return out


_RELATION_KEYS_LIST: tuple[str, ...] = ("spouse", "former_spouses", "child")
_RELATION_LABEL_PLURAL: dict[str, str] = {
    "spouse": "spouses",
    "former_spouses": "former spouses",
    "child": "children",
}


def _resolve_entry(entry: Any, tracked_set: set[int]) -> tuple[int | None, str] | None:
    """Convert a {id, name} dict (or bare int) into (id, label).

    Label appends "(also a tracked life)" when the referenced ID is one
    of the campaign's tracked characters — that flag is what tells the
    LLM 'this person also has a biography in this prompt; cross-
    reference, don't fabricate'."""
    cid: int | None = None
    name: str | None = None
    if isinstance(entry, dict):
        c = entry.get("id")
        n = entry.get("name")
        if isinstance(c, int):
            cid = c
        if isinstance(n, str) and n:
            name = n
    elif isinstance(entry, int):
        cid = entry

    if cid is None and name is None:
        return None
    if name is None:
        label = f"id {cid}"
    elif cid is None:
        label = name
    else:
        label = f"{name} (id {cid})"
    if cid is not None and cid in tracked_set:
        label = f"{label} [also a tracked life]"
    return cid, label


def _format_relations(family_resolved: dict | None, tracked_set: set[int]) -> list[str]:
    """One line per non-empty relation, deduped across primary_spouse +
    spouse[]. Single-value keys (mother/father/primary_spouse) collapse
    to "Father: …"; list-valued keys (children, spouse, former_spouses)
    collapse to "Children: a, b, c"."""
    if not isinstance(family_resolved, dict):
        return []
    lines: list[str] = []
    seen_ids: set[int] = set()

    primary_resolved = _resolve_entry(family_resolved.get("primary_spouse"), tracked_set)
    if primary_resolved is not None:
        cid, label = primary_resolved
        lines.append(f"Spouse: {label}")
        if cid is not None:
            seen_ids.add(cid)

    for key, display in (("father", "Father"), ("mother", "Mother")):
        resolved = _resolve_entry(family_resolved.get(key), tracked_set)
        if resolved is None:
            continue
        cid, label = resolved
        lines.append(f"{display}: {label}")
        if cid is not None:
            seen_ids.add(cid)

    for key in _RELATION_KEYS_LIST:
        value = family_resolved.get(key)
        if not isinstance(value, list):
            continue
        labels: list[str] = []
        for entry in value:
            resolved = _resolve_entry(entry, tracked_set)
            if resolved is None:
                continue
            cid, label = resolved
            if cid is not None and cid in seen_ids:
                continue  # deduplicate spouse already shown via primary_spouse
            labels.append(label)
            if cid is not None:
                seen_ids.add(cid)
        if labels:
            lines.append(f"{_RELATION_LABEL_PLURAL[key].capitalize()}: {', '.join(labels)}")

    return lines


def _life_span_line(s: _BiographySnapshot, end_of_campaign_date: str | None) -> str:
    """A literal 'born X; died Y' or 'born X; alive at campaign end (Z)' so
    the model can't fabricate a death like the v0.7 smoke 'rise of Sigrid'.

    Whichever date is missing falls through as 'unknown' rather than '?'
    — no glyph the LLM might mis-read as a stylistic choice."""
    born = s.birth_date or "unknown"
    if s.is_alive:
        if end_of_campaign_date:
            return f"born {born}; alive at campaign end ({end_of_campaign_date})"
        return f"born {born}; alive at campaign end"
    return f"born {born}; died {s.death_date or 'unknown'}"


def _build_user_prompt(
    campaign_name: str,
    founding_dynasty_name: str | None,
    snapshots: list[_BiographySnapshot],
    end_of_campaign_date: str | None = None,
) -> str:
    """Assemble the user-content block.

    ck3_chronicler-2wv: replaces the prior shape (campaign header +
    flat biography list with " - ?" date placeholders) with explicit
    sections for (a) life status, (b) per-character relationships
    resolved through the {id, name} family_data so the LLM never has
    to guess whether two tracked lives are spouses or generations
    apart, and (c) the actual biographies. The smoke session 2026-05-03
    showed the model defaulting to a generational-lineage template
    when given only IDs and biographies; surfacing the relations
    inline forecloses that failure mode.
    """
    header_lines = [f"Campaign: {campaign_name}"]
    if founding_dynasty_name:
        header_lines.append(f"Founding dynasty: {founding_dynasty_name}")
    if end_of_campaign_date:
        header_lines.append(f"Campaign end date: {end_of_campaign_date}")
    header_lines.append(f"Tracked lives: {len(snapshots)}")
    header = "\n".join(header_lines)

    if not snapshots:
        return f"{header}\n\nThe tracked-character set was empty — no biographies to synthesise."

    tracked_set = {s.character_id for s in snapshots}

    # Living vs departed roster — gives the LLM the alive/dead truth in
    # one glance so it can't decide a tracked character died.
    living = [s for s in snapshots if s.is_alive]
    departed = [s for s in snapshots if not s.is_alive]
    roster_lines: list[str] = ["Roster of tracked lives:"]
    if living:
        roster_lines.append("  Alive at campaign end:")
        for s in living:
            roster_lines.append(f"    - {s.label} (id {s.character_id})")
    if departed:
        roster_lines.append("  Departed before campaign end:")
        for s in departed:
            roster_lines.append(
                f"    - {s.label} (id {s.character_id}, died {s.death_date or 'unknown'})"
            )
    roster = "\n".join(roster_lines)

    bio_blocks: list[str] = []
    for s in snapshots:
        meta_parts: list[str] = [_life_span_line(s, end_of_campaign_date)]
        if s.dynasty_name or s.house_name:
            id_bits: list[str] = []
            if s.dynasty_name:
                id_bits.append(f"dynasty {s.dynasty_name}")
            if s.house_name:
                id_bits.append(f"house {s.house_name}")
            meta_parts.append("; ".join(id_bits))
        if s.culture:
            meta_parts.append(f"culture {s.culture}")
        if s.faith:
            meta_parts.append(f"faith {s.faith}")
        meta_line = "  (" + " | ".join(meta_parts) + ")"

        relation_lines = _format_relations(s.family_resolved, tracked_set)
        if relation_lines:
            relations_block = "\nFamily ties:\n  " + "\n  ".join(relation_lines)
        else:
            relations_block = ""

        body = s.body or "(no biography was generated for this life)"
        bio_blocks.append(
            f"## {s.label} (id {s.character_id}){meta_line}{relations_block}\n\n{body}"
        )

    return f"{header}\n\n{roster}\n\nTracked-character biographies:\n\n" + "\n\n---\n\n".join(
        bio_blocks
    )


async def generate_closing_chronicle(
    campaign_id: str,
    *,
    factory: sessionmaker,
    provider: NarrativeProvider,
    registry: Path | None = None,
) -> ClosingOutcome:
    """Generate and persist a closing chronicle for a campaign.

    Takes a per-campaign session **factory**, not a session — the function
    opens its own short-lived sessions for the read phase and never
    holds one during the LLM call.

    Returns :class:`ClosingOutcome`. On any failure (unknown campaign,
    provider exception) returns with ``body=None`` and the failure
    message in ``error`` — never raises.

    Caller (typically ``routes/closing.py``) is responsible for archiving
    the campaign separately after a successful generation.
    """
    campaign = get_campaign_by_id(campaign_id, registry=registry)
    if campaign is None:
        return ClosingOutcome(
            body=None,
            error=f"unknown campaign: {campaign_id}",
            generated_at=None,
        )

    tracked = list_tracked_characters(campaign_id, registry=registry)
    tracked_ids = [t.character_id for t in tracked]

    # Phase 1: read.
    snapshots = _snapshot_tracked_biographies(factory=factory, tracked_ids=tracked_ids)

    # Phase 2: generate. No DB session held.
    # Issue #19: the request — including the provider-neutral system
    # prompt assembled from the prose dir (register +
    # voice/chronicle-export.md, replacing the in-repo
    # campaign_chronicle_v1.md template every transport discarded) — is
    # built inside the error net, so a missing prose dir becomes a
    # ClosingOutcome error (HTTP 502, campaign NOT archived, safe to
    # retry) rather than an exception escaping the route.
    try:
        request = NarrativeRequest(
            kind="chronicle_export",
            prompt_version=PROMPT_TEMPLATE_VERSION,
            system_prompt=assemble_system_prompt(
                prose_repo=provider.prose_repo_path, kind="chronicle_export"
            ),
            user_prompt=_build_user_prompt(
                campaign.name,
                campaign.founding_dynasty_name,
                snapshots,
                end_of_campaign_date=campaign.last_event_at,
            ),
            # ck3_chronicler-g1y5: thread campaign_uuid + character_id so
            # ClaudeCodeProvider files at prose_repo/biographies/<uuid>/
            # closing-chronicle-vN.md instead of falling through to the
            # _unscoped/_unknown bucket. character_id is a fixed sentinel
            # because the closing chronicle is one document per campaign,
            # not per character — but the path-builder expects a string in
            # that slot. campaign_id is the UUID throughout the chronicler
            # (see registry.campaigns.create_campaign).
            metadata={
                "campaign_uuid": campaign_id,
                "character_id": "closing-chronicle",
                "campaign_id": campaign_id,
                "campaign_name": campaign.name,
                "tracked_count": str(len(tracked_ids)),
            },
        )
        response = await provider.generate(request)
    except Exception as e:
        log.exception("closing-chronicle generation failed for campaign %s", campaign_id)
        return ClosingOutcome(
            body=None,
            error=f"{type(e).__name__}: {e}",
            generated_at=None,
        )

    # Phase 3: persist.
    generated_at = datetime.now(UTC).isoformat()
    set_campaign_closing_chronicle(
        campaign_id,
        response.text,
        generated_at=generated_at,
        # ck3_chronicler-li0z: persist the closing chronicle's token spend so
        # cost-summary counts it (it isn't a Biography row).
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        # ck3_chronicler-cs1o: attribution + cache/cost breakdown so the
        # chronicle's spend buckets under the transport that ran it.
        provider=provider.name_for_kind("chronicle_export"),
        cache_read_tokens=response.cache_read_tokens,
        cache_write_tokens=response.cache_write_tokens,
        cost_usd=response.cost_usd,
        registry=registry,
    )
    log.info(
        "closing chronicle generated for campaign %s: %d tracked lives",
        campaign_id,
        len(tracked_ids),
    )
    return ClosingOutcome(body=response.text, error=None, generated_at=generated_at)
