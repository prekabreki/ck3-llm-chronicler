"""Shared glue between the two ingest transports (debug_log + save-diff).

Both :mod:`chronicler.tailer.ingest` and :mod:`chronicler.save.ingest`
need to:

1. Serialise an :class:`EventPayload` into the canonical JSON form that
   the ``events.payload_json`` UNIQUE-index dedupes on.
2. Extract payload-level participants (today only ``DeathEvent.killer``,
   but the contract is open for future events that carry IDs in their
   payload).
3. Merge payload-level + transport-supplied participants into a single
   deduplicated list of ``(character_id, role)`` pairs in append order.

Keeping these here means a fix in the canonical-JSON formatting (or
participant extraction) lands in both transports automatically. The
dual-transport design itself is intentional per docs/architecture.md — only
the glue is shared, not the pipelines.

Promoted from per-module copies per F003 / 435.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable

from chronicler.db.repository import insert_event_idempotent, upsert_character
from chronicler.schema import DeathEvent, EventPayload

log = logging.getLogger(__name__)


def payload_canonical_json(event: EventPayload) -> str:
    """Stable JSON form of an event for the ``events.payload_json`` UNIQUE.

    ``exclude_none=True`` + ``sort_keys=True`` + tight separators are
    load-bearing: the events table dedupes on
    ``(event_type, event_date, primary_character_id, payload_json)``,
    so any whitespace or field-order drift would defeat the invariant.
    """
    return json.dumps(
        event.model_dump(exclude_none=True),
        separators=(",", ":"),
        sort_keys=True,
    )


def payload_participants(event: EventPayload) -> list[tuple[int, str]]:
    """Participants present in the event payload itself (not the transport scope).

    Returns ``(character_id, role)`` pairs. Today this is just
    ``DeathEvent.killer`` when set; v0.2+ event types may add more as
    they land. Most relations come from the transport's scope dump
    (debug_log: :class:`IncrementalParser` participants;
    save-diff: :class:`DiffEvent.participants`) instead.
    """
    if isinstance(event, DeathEvent) and event.p.killer is not None:
        return [(event.p.killer, "killer")]
    return []


def merge_participants(
    *sources: Iterable[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Concatenate ``(character_id, role)`` pairs across sources, deduped in append order.

    Each source contributes pairs in its own argument order; later
    duplicates are dropped. The caller is responsible for normalising
    each source to the ``(character_id, role)`` shape before calling —
    e.g. the debug_log scope dump is ``(role, character_id)`` natively
    and must be flipped first.
    """
    out: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for source in sources:
        for pair in source:
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


def ingest_event(
    event: EventPayload,
    *,
    session,
    event_date_iso: str | None,
    wall_clock_at: str,
    raw_line: str,
    upsert_character_fn: Callable[[int], None],
    scope_participants: Iterable[tuple[int, str]] = (),
    payload_participants_first: bool = False,
    scheduler: object | None = None,
    should_schedule_death: Callable[[], bool] = lambda: True,
    on_death_schedule_skipped: Callable[[int], None] | None = None,
    on_ingested: Callable[[int], None] | None = None,
) -> int | None:
    """Persist one event + its participants; schedule a biography on death.

    The shared core of both ingest transports (ck3_chronicler-ejfa). It
    owns the steps that must not drift between the save-diff and
    debug_log paths: upsert the primary + every participant, merge
    payload- and scope-level participants, idempotent-insert the event,
    stamp ``last_seen_event_id``, and schedule a biography when the event
    is a death. Returns the new ``event_id``, or ``None`` when the event
    was a duplicate (``events.payload_json`` UNIQUE already held it).

    The transports vary only in their *inputs* and a couple of optional
    hooks, all passed in:

    * ``upsert_character_fn(char_id)`` — how a character row is hydrated
      (save-diff: snapshot-aware; debug_log: bare upsert). Called for the
      primary and every participant.
    * ``event_date_iso`` / ``wall_clock_at`` / ``raw_line`` — already
      computed by the caller (the two paths use different date parsers
      and wall-clock sources).
    * ``scope_participants`` — transport scope pairs, already normalised
      to ``(character_id, role)``. ``payload_participants_first`` controls
      merge order so each path keeps its prior participant ordering.
    * ``should_schedule_death`` — gate the death-triggered schedule (the
      save path skips it while LLM generation is paused);
      ``on_death_schedule_skipped(char_id)`` fires when it's gated off.
    * ``on_ingested(event_id)`` — post-insert hook (the save path fans
      out to the SSE bus here; the debug_log path is intentionally
      bus-less per docs/architecture.md, so it passes nothing).
    """
    upsert_character_fn(event.c)

    payload = payload_participants(event)
    scope = list(scope_participants)
    if payload_participants_first:
        participants = merge_participants(payload, scope)
    else:
        participants = merge_participants(scope, payload)
    for participant_id, _role in participants:
        upsert_character_fn(participant_id)

    event_id = insert_event_idempotent(
        session,
        schema_version=event.v,
        event_type=event.t,
        event_date=event.d,
        event_date_iso=event_date_iso,
        wall_clock_at=wall_clock_at,
        primary_character_id=event.c,
        payload_json=payload_canonical_json(event),
        raw_line=raw_line,
        participants=participants,
    )
    if event_id is None:
        return None

    upsert_character(session, ck3_id=event.c, last_seen_event_id=event_id)

    if scheduler is not None and isinstance(event, DeathEvent):
        if should_schedule_death():
            scheduler.schedule(event.c)
        elif on_death_schedule_skipped is not None:
            on_death_schedule_skipped(event.c)

    if on_ingested is not None:
        on_ingested(event_id)

    return event_id
