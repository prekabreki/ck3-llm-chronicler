"""Cross-DB aggregator for the Hall of Fame (ck3_chronicler-467k).

Walks every registered campaign, groups them by their playing dynasty,
and emits one :class:`DynastyRollup` per group with headline counts +
a heraldry seed pulled from the most recent player's character row.

The Hall of Fame UI (467k.1) consumed in-module fixtures up to this
point; this aggregator wires the page to real registry data.

Identity reconciliation
-----------------------

Group key is ``(playthrough_id, dynasty_name)``:

- ``playthrough_id`` from the registry's ``ck3_playthrough_id`` column
  (cqo). Empty/missing maps to ``None`` so save-format era gaps don't
  alias every mystery campaign together.
- ``dynasty_name`` falls through three sources: the registry's
  ``founding_dynasty_name`` (preferred — set at create time and
  immutable), then ``current_house_name`` (cqo identity, save-tail
  refreshed), then a literal ``"Unknown dynasty"`` so empty groups
  never crash the renderer.

The brief flagged a v1.0+ "merge cross-playthrough" UI option that
would coalesce two rollups whose dynasty_name matches but whose
playthrough_id differs. Skipped here — the wire format keys both
fields so a follow-up patch can render the merge affordance without
a second BE pass.

Data sources per group
----------------------

- ``campaigns_count``: number of campaigns aggregated into the group.
- ``tracked_count``: ``len(list_tracked_characters(campaign_id))``
  summed across all the group's campaigns.
- ``biographies_count``: per-campaign DB ``aggregate_campaign_counts``
  ``biographies`` field summed.
- ``coa_json``: the player's ``coa_json`` from the most recent
  campaign in the group (most recent by last_event_at; falls back to
  created_at). Same lookup pattern as the Library card's
  ``current_player_coa_json``.
- ``blurb``: first paragraph of the most-recently-sealed campaign's
  ``closing_chronicle``, or None when no campaign in the group has
  been sealed.
- Span: earliest ``bookmark_date`` to latest ``current_in_game_date``
  (or ``closing_chronicle_generated_at`` for sealed campaigns), parsed
  via :func:`parse_ck3_short_date` so we can compute span_days.

Performance
-----------

One open-engine pass per campaign; engines are cached on the shared
``EngineCache`` from app state so subsequent calls are warm. The
brief flagged pagination + caching as v1.0+ — skipped here. Real-world
chronicler users have <30 campaigns at this stage, well below any
cliff that would warrant either.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from chronicler.db import make_engine_for_path, make_session_factory
from chronicler.db.registry import (
    Campaign,
    list_campaigns,
    list_tracked_characters,
)
from chronicler.db.repository import (
    aggregate_campaign_counts,
    get_character_coa_json,
)
from chronicler.util.dates import parse_ck3_short_date

log = logging.getLogger(__name__)

_UNKNOWN_DYNASTY = "Unknown dynasty"


@dataclass(frozen=True)
class DynastyRollup:
    """One row in the Hall of Fame — every campaign for a given dynasty
    rolled into a single card-shaped payload.

    Field shape mirrors the FE's :class:`DynastyRollup` type
    (frontend/src/pages/hall/useHallOfFame.ts) so the wire format is
    1:1 with what the UI already consumes from fixtures.
    """

    id: str
    playthrough_id: str | None
    dynasty_name: str
    span_label: str
    span_end_label: str | None
    span_days: int | None
    last_event_iso: str | None
    campaigns_count: int
    tracked_count: int
    biographies_count: int
    blurb: str | None
    is_active: bool
    sealed_at_label: str | None
    primary_campaign_name: str
    coa_json: str | None
    heraldry_seed: str


def aggregate_hall_of_fame(
    *,
    registry: Path | None = None,
    factory_for: Callable[[Campaign], sessionmaker[Session]] | None = None,
) -> list[DynastyRollup]:
    """Walk the registry and emit one :class:`DynastyRollup` per dynasty.

    ``factory_for`` is the EngineCache hook so the FastAPI route can
    pass its process-wide engine cache straight through. When ``None``
    (CLI / test invocation), each per-campaign engine is opened fresh
    and disposed inside the function.
    """
    campaigns = list_campaigns(include_archived=True, registry=registry)

    # Group by (playthrough_id, dynasty_name). Order each bucket
    # most-recent first so picks like coa_json / primary_campaign / blurb
    # are deterministic without re-sorting later.
    groups: dict[tuple[str | None, str], list[Campaign]] = {}
    for c in campaigns:
        key = _group_key(c)
        groups.setdefault(key, []).append(c)
    for camps in groups.values():
        camps.sort(key=_recency_key, reverse=True)

    out: list[DynastyRollup] = []
    for (playthrough_id, dynasty_name), camps in groups.items():
        rollup = _rollup_for_group(
            playthrough_id=playthrough_id,
            dynasty_name=dynasty_name,
            campaigns=camps,
            factory_for=factory_for,
        )
        out.append(rollup)
    return out


def _group_key(c: Campaign) -> tuple[str | None, str]:
    """Build the (playthrough_id, dynasty_name) bucket key for ``c``.

    See module docstring on the three-source dynasty_name fallback.
    Empty/whitespace ``ck3_playthrough_id`` becomes ``None`` so we
    don't string-compare-equal save-format-era gaps.
    """
    pt = (c.ck3_playthrough_id or "").strip() or None
    dynasty = c.founding_dynasty_name or c.current_house_name or _UNKNOWN_DYNASTY
    return (pt, dynasty)


def _recency_key(c: Campaign) -> str:
    """Sort key for picking the most-recent campaign in a group.

    Prefers ``last_event_at`` (save-tail wall-clock) when populated;
    falls back to ``created_at`` so even un-tailed campaigns sort
    sensibly. Both are ISO 8601, so a string compare is a chronological
    compare.
    """
    return c.last_event_at or c.created_at


def _rollup_for_group(
    *,
    playthrough_id: str | None,
    dynasty_name: str,
    campaigns: list[Campaign],
    factory_for: Callable[[Campaign], sessionmaker[Session]] | None,
) -> DynastyRollup:
    most_recent = campaigns[0]

    # Per-campaign DB fan-out: open each campaign's session once, pull
    # biographies count + the player's coa_json (only meaningful on the
    # most-recent campaign so the Hall card shows the live shield).
    biographies_total = 0
    coa_json: str | None = None
    for c in campaigns:
        try:
            session_factory = factory_for(c) if factory_for is not None else _make_local_factory(c)
        except Exception:
            log.exception(
                "hall-of-fame: could not open session for campaign %s; skipping counts",
                c.id,
            )
            continue
        with session_factory() as session:
            try:
                counts = aggregate_campaign_counts(session)
                biographies_total += int(counts.get("biographies", 0))
            except Exception:
                log.exception(
                    "hall-of-fame: aggregate_campaign_counts failed for %s",
                    c.id,
                )
            if coa_json is None and c is most_recent and c.current_player_character_id:
                try:
                    raw = get_character_coa_json(session, c.current_player_character_id)
                    if raw and _is_valid_coa_json(raw):
                        coa_json = raw
                except Exception:
                    log.exception(
                        "hall-of-fame: coa_json lookup failed for campaign %s",
                        c.id,
                    )

    # Tracked count: registry-side, no per-campaign-DB hit needed.
    tracked_total = 0
    for c in campaigns:
        try:
            tracked_total += len(list_tracked_characters(c.id))
        except Exception:
            log.exception("hall-of-fame: list_tracked_characters failed for %s", c.id)

    # Span: earliest bookmark_date (or created_at year fallback) →
    # latest current_in_game_date (or closing_chronicle date for
    # sealed campaigns).
    starts = [c.bookmark_date for c in campaigns if c.bookmark_date]
    ends = [c.current_in_game_date for c in campaigns if c.current_in_game_date]
    span_label, span_end_label, span_days = _compute_span(starts, ends)

    # Sort key for "most-recent" — the registry's last_event_at is the
    # save-tail wall-clock. Fall back to created_at so even un-tailed
    # rollups sort sensibly.
    last_event_iso = max(
        (c.last_event_at for c in campaigns if c.last_event_at),
        default=None,
    )

    is_active = any(not c.archived for c in campaigns)

    # Sealed-at label + blurb: pick the most-recently-sealed campaign in
    # the group. Active rollups have no seal date but may still carry an
    # archived sibling whose chronicle reads as the rollup's blurb.
    sealed = sorted(
        [c for c in campaigns if c.archived and c.closing_chronicle],
        key=lambda c: c.closing_chronicle_generated_at or c.last_event_at or "",
        reverse=True,
    )
    blurb = None
    sealed_at_label: str | None = None
    if sealed:
        body = sealed[0].closing_chronicle
        blurb = _first_paragraph(body)
        ts = sealed[0].closing_chronicle_generated_at
        sealed_at_label = (ts or "").split("T")[0] or None

    # Primary campaign: most recent active by last_event_at, else most
    # recent overall (already first thanks to the recency sort).
    active_camps = [c for c in campaigns if not c.archived]
    primary = active_camps[0] if active_camps else most_recent

    rollup_id = f"{playthrough_id or ''}|{dynasty_name}"

    return DynastyRollup(
        id=rollup_id,
        playthrough_id=playthrough_id,
        dynasty_name=dynasty_name,
        span_label=span_label,
        span_end_label=span_end_label,
        span_days=span_days,
        last_event_iso=last_event_iso,
        campaigns_count=len(campaigns),
        tracked_count=tracked_total,
        biographies_count=biographies_total,
        blurb=blurb,
        is_active=is_active,
        sealed_at_label=sealed_at_label,
        primary_campaign_name=primary.name,
        coa_json=coa_json,
        heraldry_seed=rollup_id,
    )


def _make_local_factory(c: Campaign) -> sessionmaker[Session]:
    """Open a fresh per-campaign engine for a single aggregation pass.

    Used when no shared EngineCache is available (CLI / tests). The
    engine is allocated fresh per campaign and the session-scope context
    manager closes the underlying connection on exit; engine reuse is
    out of scope for the local fallback.
    """
    engine = make_engine_for_path(Path(c.db_path))
    return make_session_factory(engine)


def _compute_span(
    starts: list[str],
    ends: list[str],
) -> tuple[str, str | None, int | None]:
    """Compute ``(span_label, span_end_label, span_days)`` from raw CK3
    short-date strings.

    Returns:
    - ``span_label``: ``"<start_year> — <end_year>"`` when both bounds
      parse, ``"<start_year> —"`` when only the start does, ``"—"`` as
      the universal fallback.
    - ``span_end_label``: the most recent CK3 short-date string
      (verbatim, e.g. ``"1184.7.4"``) so the FE Active/Archived pill
      can read the in-game date directly.
    - ``span_days``: days between earliest start and latest end as ISO
      dates. ``None`` when either bound failed to parse.
    """
    parsed_starts = [(s, parse_ck3_short_date(s)) for s in starts]
    parsed_ends = [(e, parse_ck3_short_date(e)) for e in ends]
    valid_starts = [(s, iso) for s, iso in parsed_starts if iso]
    valid_ends = [(e, iso) for e, iso in parsed_ends if iso]

    earliest = min(valid_starts, key=lambda pair: pair[1])[0] if valid_starts else None
    latest = max(valid_ends, key=lambda pair: pair[1])[0] if valid_ends else None

    start_year = _year_of(earliest) if earliest else None
    end_year = _year_of(latest) if latest else None

    if start_year and end_year:
        span_label = f"{start_year} — {end_year}"
    elif start_year:
        span_label = f"{start_year} —"
    else:
        span_label = "—"

    span_days: int | None = None
    if earliest and latest:
        try:
            d_start = _date.fromisoformat(parse_ck3_short_date(earliest) or "")
            d_end = _date.fromisoformat(parse_ck3_short_date(latest) or "")
            span_days = max(0, (d_end - d_start).days)
        except (TypeError, ValueError):
            span_days = None

    return span_label, latest, span_days


def _year_of(ck3_short_date: str) -> str | None:
    """Pull the year off a CK3 short-date string (``"1066.9.15"`` →
    ``"1066"``). Returns None on malformed input."""
    head = ck3_short_date.split(".", 1)[0]
    return head if head.isdigit() else None


def _first_paragraph(body: str | None) -> str | None:
    """First paragraph of a closing-chronicle body, mirroring the
    Library card's ``_blurb_from_chronicle`` semantics so the Hall card
    shows the same prose the user already saw on the campaign card."""
    if not body:
        return None
    first = body.split("\n\n", 1)[0].strip()
    return first or None


def _is_valid_coa_json(raw: str) -> bool:
    """Defence-in-depth: malformed JSON on a Character row shouldn't
    poison the rollup — fall through to the procedural-shield seed."""
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict)
