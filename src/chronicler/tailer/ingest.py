"""Wire the watcher → parser → repository.

The parser is stateful (CHRONICLER lines must be paired with the engine
scope dump that follows), so :func:`process_line` takes an
:class:`IncrementalParser` instance threaded through the loop. One line in
can produce 0, 1, or 2 results: a new CHRONICLER line can both finalise a
stranded pending event (as quarantine) and start a new pending. Other lines
either advance an in-progress pending or are skipped.

The async :func:`run_ingest` loop combines :func:`tail` from the watcher
with this state machine and persists the tail offset to the registry after
each line so a crash never causes us to re-ingest a duplicate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from sqlalchemy.orm import Session

from chronicler.db.engine import make_engine_for_path, make_session_factory, session_scope
from chronicler.db.registry import set_tail_offset, touch_last_event_at
from chronicler.db.repository import (
    insert_quarantine,
    upsert_character,
)
from chronicler.ingest_common import ingest_event
from chronicler.narrative.scheduler import BiographyScheduler
from chronicler.tailer.parser import (
    IncrementalParser,
    ParsedEvent,
    ParseFailure,
    ParseResult,
)
from chronicler.tailer.watcher import tail
from chronicler.util.dates import parse_ck3_long_date

_T = TypeVar("_T")

# F008 / 81v: how long to trust a cached registry-predicate result before
# re-querying. Two seconds: the user adds tracked characters / suppressions
# on a human timescale via `chronicler track` from another terminal; a 2s
# lag before the tail picks up the change is imperceptible. A typical
# debug_log tail fires hundreds of CHRONICLER lines per minute, so even
# this short TTL collapses ~99% of the registry-DB open/close churn.
_REGISTRY_PREDICATE_TTL_SECONDS = 2.0


log = logging.getLogger(__name__)

ProcessOutcome = Literal["skipped", "ingested", "duplicate", "quarantined", "suppressed"]

# ck3_chronicler-fkw: callback shape for suppression checks. Receives a
# ParseFailure's event_kind (None when unknown) and returns True iff the
# tailer should drop the failure rather than insert it into quarantine.
SuppressionCheck = Callable[[str | None], bool]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    outcome: ProcessOutcome
    event_id: int | None = None
    quarantine_id: int | None = None
    suppressed_kind: str | None = None


def _ttl_cached(fn: Callable[..., _T], *, ttl_seconds: float) -> Callable[..., _T]:
    """Wrap ``fn`` so identical-argument calls within ``ttl_seconds`` reuse the
    last result. Uses :func:`time.monotonic` so wall-clock changes can't
    expire the cache early. Argument tuples must be hashable.
    """
    cache: dict[tuple[Any, ...], tuple[float, _T]] = {}

    def _check(*args: Any) -> _T:
        now = time.monotonic()
        cached = cache.get(args)
        if cached is not None and now - cached[0] < ttl_seconds:
            return cached[1]
        result = fn(*args)
        cache[args] = (now, result)
        return result

    return _check


def _ingest_parsed(
    parsed: ParsedEvent,
    *,
    session: Session,
    scheduler: BiographyScheduler | None = None,
) -> ProcessResult:
    event = parsed.event
    # Tailer's scope_dump arrives as (role, id); flip to (id, role).
    # payload_participants_first=True preserves the debug_log path's prior
    # ordering (payload killer before scope relations).
    scope_pairs = [(char_id, role) for role, char_id in parsed.participants]

    def _upsert(char_id: int) -> None:
        upsert_character(session, ck3_id=char_id)

    # The scheduler schedules a biography on death; it runs on the same
    # event loop in its own session so the ingest loop never blocks. The
    # tailer is the diagnostic path and intentionally has no event bus
    # (docs/architecture.md), so no on_ingested hook is passed.
    event_id = ingest_event(
        event,
        session=session,
        event_date_iso=parse_ck3_long_date(event.d),
        wall_clock_at=parsed.wall_clock_at,
        raw_line=parsed.raw_line,
        upsert_character_fn=_upsert,
        scope_participants=scope_pairs,
        payload_participants_first=True,
        scheduler=scheduler,
    )
    if event_id is None:
        log.debug("duplicate event for character %d on %s", event.c, event.d)
        return ProcessResult(outcome="duplicate")

    log.info("ingested %s for character %d on %s", event.t, event.c, event.d)
    return ProcessResult(outcome="ingested", event_id=event_id)


def _apply_result(
    result: ParseResult,
    *,
    session: Session,
    scheduler: BiographyScheduler | None = None,
    is_kind_suppressed: SuppressionCheck | None = None,
) -> ProcessResult:
    if isinstance(result, ParseFailure):
        # ck3_chronicler-fkw: silently drop failures whose event_kind has
        # been suppressed for this campaign. Visible only as the
        # "suppressed" outcome in caller results so test fixtures can
        # assert it; no quarantine row, no log noise.
        if is_kind_suppressed is not None and is_kind_suppressed(result.event_kind):
            return ProcessResult(outcome="suppressed", suppressed_kind=result.event_kind)
        qid = insert_quarantine(
            session,
            raw_line=result.raw_line,
            error=result.error,
            ts=result.wall_clock_at,
            event_kind=result.event_kind,
        )
        log.warning(
            "quarantined: kind=%s reason=%s error=%s",
            result.event_kind,
            result.reason,
            result.error,
        )
        return ProcessResult(outcome="quarantined", quarantine_id=qid)
    return _ingest_parsed(result, session=session, scheduler=scheduler)


def process_line(
    line: str,
    *,
    session: Session,
    parser: IncrementalParser,
    scheduler: BiographyScheduler | None = None,
    is_kind_suppressed: SuppressionCheck | None = None,
) -> list[ProcessResult]:
    """Feed one line through the parser; apply each completed result.

    Returns a list because a single new CHRONICLER line can both finalise a
    stranded pending and start a fresh one. An empty list means the line
    was either uninteresting or advanced an in-progress pending without
    completing it; either way the caller can advance the tail offset.
    """
    results = parser.feed(line)
    if not results:
        return [ProcessResult(outcome="skipped")]
    return [
        _apply_result(
            r, session=session, scheduler=scheduler, is_kind_suppressed=is_kind_suppressed
        )
        for r in results
    ]


def process_lines(
    lines: Iterable[str],
    *,
    session: Session,
    scheduler: BiographyScheduler | None = None,
    is_kind_suppressed: SuppressionCheck | None = None,
) -> list[ProcessResult]:
    """Convenience: process a batch of lines against one session."""
    parser = IncrementalParser()
    results: list[ProcessResult] = []
    for line in lines:
        results.extend(
            process_line(
                line,
                session=session,
                parser=parser,
                scheduler=scheduler,
                is_kind_suppressed=is_kind_suppressed,
            )
        )
    for r in parser.flush():
        results.append(
            _apply_result(
                r, session=session, scheduler=scheduler, is_kind_suppressed=is_kind_suppressed
            )
        )
    return results


async def run_ingest(
    *,
    log_path: Path,
    db_path: Path,
    campaign_id: str,
    registry_path: Path | None = None,
    start_offset: int = 0,
    stop_event: asyncio.Event | None = None,
    biography_provider=None,  # NarrativeProvider | None
) -> None:
    """Long-running async loop: tail ``log_path`` and ingest into ``db_path``.

    When ``biography_provider`` is non-None, each ingested death event
    for a **tracked** character (see ``chronicler track``) schedules an
    asynchronous biography generation against that provider. Untracked
    characters are skipped — the v0.2 stopgap for the "1,144 deaths in
    9 in-game months" volume problem until v0.4's mod-side relevance
    gate lands. Pass ``None`` (the ``--no-biography`` CLI flag does
    this) to skip biography generation entirely.
    """
    from chronicler.db.registry import (
        get_tracked_status,
        is_character_tracked,
        is_kind_suppressed,
    )

    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    parser = IncrementalParser()

    # F008 / 81v: cache predicate results for a short TTL. Each call
    # without caching opens + initialises a fresh sqlite3 connection on
    # registry.db, which on a busy debug_log tail is hundreds of opens
    # per minute. The cache still re-reads every TTL seconds so live
    # `chronicler track` / `chronicler suppress-quarantine` from another
    # terminal still take effect within ~2s.
    _tracked_cached = _ttl_cached(
        lambda character_id: is_character_tracked(
            campaign_id, character_id, registry=registry_path
        ),
        ttl_seconds=_REGISTRY_PREDICATE_TTL_SECONDS,
    )
    _suppressed_cached = _ttl_cached(
        lambda event_kind: is_kind_suppressed(campaign_id, event_kind, registry=registry_path),
        ttl_seconds=_REGISTRY_PREDICATE_TTL_SECONDS,
    )

    def _is_tracked(character_id: int) -> bool:
        return _tracked_cached(character_id)

    def _is_suppressed(event_kind: str | None) -> bool:
        return _suppressed_cached(event_kind)

    # ck3_chronicler-t2v5: paused/bumped lookup. Not TTL-cached because
    # the user expects Pause/Resume/Bump from the Tracked page to take
    # effect on the next event tick — a 2s cache would feel laggy.
    def _is_paused(character_id: int) -> bool:
        paused, _ = get_tracked_status(campaign_id, character_id, registry=registry_path)
        return paused

    def _get_bumped_at(character_id: int) -> str | None:
        _, bumped = get_tracked_status(campaign_id, character_id, registry=registry_path)
        return bumped

    scheduler: BiographyScheduler | None = (
        BiographyScheduler(
            factory,
            biography_provider,
            is_tracked=_is_tracked,
            is_paused=_is_paused,
            get_bumped_at=_get_bumped_at,
        )
        if biography_provider is not None
        else None
    )
    log.info(
        "starting ingest loop: campaign=%s log=%s db=%s offset=%d biography=%s",
        campaign_id,
        log_path,
        db_path,
        start_offset,
        biography_provider.name if biography_provider is not None else "disabled",
    )
    try:
        async for tailed in tail(log_path, start_offset=start_offset, stop_event=stop_event):
            with session_scope(factory) as session:
                process_line(
                    tailed.text,
                    session=session,
                    parser=parser,
                    scheduler=scheduler,
                    is_kind_suppressed=_is_suppressed,
                )
            set_tail_offset(campaign_id, tailed.offset, registry=registry_path)
            touch_last_event_at(campaign_id, registry=registry_path)
    finally:
        # Drain any pending event before tearing down.
        pending = parser.flush()
        if pending:
            with session_scope(factory) as session:
                for r in pending:
                    _apply_result(
                        r,
                        session=session,
                        scheduler=scheduler,
                        is_kind_suppressed=_is_suppressed,
                    )
        if scheduler is not None:
            await scheduler.drain()
        if biography_provider is not None and hasattr(biography_provider, "aclose"):
            await biography_provider.aclose()
        engine.dispose()
        log.info("ingest loop stopped: campaign=%s", campaign_id)
