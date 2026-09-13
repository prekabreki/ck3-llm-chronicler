"""ck3_chronicler-2cc.1: curated, human-readable event roll for the
chronicle export. Reuses the narrative event-rendering registry
(render_event_body) for per-event text — no second renderer — and drops
high-frequency low-signal events so the exported roll reads like a life's
milestones rather than a monthly travel log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chronicler.narrative.event_rendering import (
    NamesMap,
    TitlesMap,
    render_event_body,
)

# High-frequency, low-individual-signal event types dropped from the roll
# regardless of count. lifestyle_committed already captures the milestone
# perk_acquired churns toward; the rest are pure churn (monthly travel,
# mood-mod flicker, raw decision ids, adventurer province hops).
_NOISE_EVENT_TYPES: frozenset[str] = frozenset(
    {"travel", "modifier_acquired", "perk_acquired", "decision_taken", "domicile_moved"}
)

# Vanilla memories are CK3's own "memorable" tags but mixed: life milestones
# vs social churn. Allowlist (not denylist) so the long tail defaults to
# dropped. Keeps battle/war OUTCOME memories (the win/lose verdict the diff
# layer's war_concluded doesn't carry); excludes memory_types redundant with
# first-class diff events (vanilla epidemic/miscarriage) and the social /
# education long tail.
_MILESTONE_MEMORY_TYPES: frozenset[str] = frozenset(
    {
        "married",
        "ascended_throne_memory",
        "battle_won_memory",
        "battle_lost_memory",
        "war_won",
        "war_lost",
        "child_born",
        "first_born",
        "twins_born",
        "child_stillborn",
        "spouse_died",
        "imprisoned",
        "released_from_prison_memory",
        "lost_title_memory",
    }
)

_VANILLA_PREFIX = "vanilla memory: "


@dataclass(frozen=True, slots=True)
class EventRollRow:
    """One rendered, presentation-ready event-roll line."""

    date: str
    description: str


def is_significant(event_type: str, payload: dict[str, Any]) -> bool:
    """True if this event belongs in the curated highlights roll."""
    if event_type in _NOISE_EVENT_TYPES:
        return False
    if event_type == "vanilla_memory":
        return payload.get("memory_type") in _MILESTONE_MEMORY_TYPES
    return True


def render_roll_entry(
    event_type: str,
    payload: dict[str, Any],
    *,
    names_map: NamesMap,
    titles_map: TitlesMap | None = None,
) -> str:
    """Render one event to a presentation-polished sentence: delegate to
    the narrative registry, strip its prompt-voiced ``vanilla memory: ``
    prefix, and capitalize the first letter for table presentation."""
    body = render_event_body(event_type, payload, names_map=names_map, titles_map=titles_map)
    if body.startswith(_VANILLA_PREFIX):
        body = body[len(_VANILLA_PREFIX) :]
    return body[:1].upper() + body[1:] if body else body


def build_event_roll(
    events: list[dict[str, Any]],
    *,
    names_map: NamesMap,
    titles_map: TitlesMap | None = None,
) -> list[EventRollRow]:
    """Filter to significant events and render each. Input dicts are the
    export's ``{date, type, payload}`` shape (``payload`` is the inner
    ``p`` dict from the persisted event)."""
    rows: list[EventRollRow] = []
    for e in events:
        payload = e.get("payload") or {}
        if not is_significant(e["type"], payload):
            continue
        rows.append(
            EventRollRow(
                date=e["date"],
                description=render_roll_entry(
                    e["type"], payload, names_map=names_map, titles_map=titles_map
                ),
            )
        )
    return rows
