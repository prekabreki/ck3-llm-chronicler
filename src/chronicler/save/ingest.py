"""Save-file ingest pipeline — the v0.6 architectural primary.

Long-running async loop that watches a CK3 save directory, parses each
new save via rakaly, diffs it against the previous snapshot, and
ingests the resulting events into the per-campaign DB. Drop-in
replacement for :func:`chronicler.tailer.ingest.run_ingest` as the
production happy path post-v0.6.

Architecture (top-down):

::

    save_dir watcher ─→ for each new .ck3:
        rakaly subprocess ─→ JSON dict
        parse_save        ─→ SaveSnapshot
        diff_snapshots    ─→ list[DiffEvent]   (vs previous snapshot)
        per-event:
            insert into events + event_participants
            if DeathEvent: scheduler.schedule(char_id)

State held across the loop:

- ``last_snapshot``: the snapshot we'll diff the next save against.
  Established on startup by parsing the latest existing save without
  emitting events (a baseline). On each subsequent save, advance.
- ``BiographyScheduler``: same as v0.2's debug_log path.

Errors anywhere in the chain (rakaly subprocess fail, parse fail,
diff fail) are logged and the loop continues — chronicler's
"never stop on bad data" rule still applies.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import monotonic as time_monotonic
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Runtime import would be a save → api circular; type-check only.
    from chronicler.api.events import EventBus
    from chronicler.narrative.queue_state import NarrativeQueueState

from chronicler.db.engine import (
    make_engine_for_path,
    make_session_factory,
    session_scope,
)
from chronicler.db.registry import (
    get_data_dir,
    get_tracked_character_ids,
    get_tracked_status,
    is_character_tracked,
    touch_last_event_at,
)
from chronicler.db.repository import (
    PlaythroughMismatchError,
    assert_playthrough_or_pin,
    count_events,
)
from chronicler.narrative.pause import (
    DrainReport,
    _is_llm_paused,
    _queue_size,
    _reset_llm_paused_cache,
    drain_for_campaign,
)
from chronicler.narrative.scheduler import NarrativeScheduler
from chronicler.save.baseline import (
    baseline_path_for,
    load_baseline,
)
from chronicler.save.cache import (
    DEFAULT_MAX_CACHE_BYTES,
    CachedSave,
    SaveCache,
    cache_dir_for,
)
from chronicler.save.importer import import_save
from chronicler.save.overview import apply_campaign_overview_from_snap
from chronicler.save.parse import (
    SaveSnapshot,
    parse_save,
)
from chronicler.save.parse_worker import decode_snapshot, parse_save_in_worker
from chronicler.save.rakaly import (
    RakalyError,
    convert_save_to_json,
    convert_save_to_json_async,
)
from chronicler.save.tick import (
    IngestResult,
    ProcessOutcome,
    _advance_baseline,
    _auto_track_new_candidates,
    _handle_empty_tracked_tick,
    _ingest_diff_event,
    _ingest_diff_events,
    _is_advance_candidate,
    _maybe_notify_foreign_playthrough,
    _now_iso,
    _persist_baseline_safely,
    _persist_per_save_state,
    _publish_save_dropped_foreign,
    _publish_save_pair_completed,
    _publish_tick_complete,
    _refresh_tracked_characters,
    _safe_diff,
    _tally_ingested_events,
    _upsert_character_from_snapshot,
    process_save_pair,
)
from chronicler.save.watcher import watch_saves
from chronicler.util.async_pipeline import map_ordered_bounded

# ck3_chronicler-r8l1: how many rakaly parses to run concurrently in the
# save-tail consumer. rakaly (the .ck3 -> JSON melt) is ~80% of every
# ~11.8s ingest cycle and each run is an independent, side-effect-free
# subprocess, so overlapping a few drains a backlog far faster while the
# diff/baseline tail stays strictly serial + in-order. Capped low: parses
# are CPU-bound (bounded by cores) and each holds a ~70MB JSON blob in
# memory, so 3 is a deliberate ceiling, not a target to raise blindly.
DEFAULT_PARSE_CONCURRENCY = 3

log = logging.getLogger(__name__)


def _resolve_parse_pool_workers(parse_concurrency: int) -> int:
    """ck3_chronicler-j86v: bound the parse pool size by cores (leave two
    for the uvicorn loop + the diff/baseline consumer tail) and by
    ``parse_concurrency``.

    A RAM-based clamp (~1.1 GB/worker for a populous save's decode) can be
    layered on once measured on the target box; cores-minus-two is the safe
    default. The chosen value is logged at loop start.
    """
    cores = os.cpu_count() or 4
    return max(1, min(parse_concurrency, cores - 2))


__all__ = [
    "run_save_ingest",
    "process_save_pair",
    "IngestResult",
    "ProcessOutcome",
    "DrainReport",
    "drain_for_campaign",
    "latest_save",
    "_advance_baseline",
    "_auto_track_new_candidates",
    "_handle_empty_tracked_tick",
    "_ingest_diff_event",
    "_ingest_diff_events",
    "_is_advance_candidate",
    "_maybe_notify_foreign_playthrough",
    "_now_iso",
    "_persist_baseline_safely",
    "_persist_per_save_state",
    "_refresh_tracked_characters",
    "_publish_save_dropped_foreign",
    "_publish_save_pair_completed",
    "_publish_tick_complete",
    "_safe_diff",
    "_tally_ingested_events",
    "_upsert_character_from_snapshot",
    "_is_llm_paused",
    "_reset_llm_paused_cache",
    "_queue_size",
]

# Match the *current* autosave + the session-close save CK3 writes on
# orderly exit ("autosave_exit.ck3", ck3_chronicler-fi7). Crucially we
# avoid the rolling backups CK3 keeps as autosave_1.ck3 / autosave_2.ck3
# / etc. — each autosave write shuffles those backups, generating
# watcher events for older states that the save-tail loop then diffs
# out of chronological order. Verified live: matching the glob
# "autosave*.ck3" produced thousands of phantom events as the watcher
# saw older backup files appear. Users who want to handle manual saves
# can pass --pattern "*.ck3" — but that defeats the assumption that
# each new file is monotonically newer in time, so they're on their
# own for ordering.
#
# Tuple form so the watcher / _latest_save scan multiple distinct
# patterns; each pattern is matched verbatim against save_dir contents
# (no fallthrough to a single combined glob, which would risk picking
# up the rotation backups).
DEFAULT_SAVE_PATTERN: tuple[str, ...] = ("*.ck3",)
# ck3_chronicler 2026-05-09: default expanded from autosave-only to all
# .ck3 files. The autosave-only pattern silently ignored manual saves
# (e.g. user-named '_start.ck3' or numbered quicksaves), which made
# save-tail look broken to anyone who didn't rely on CK3 autosaves.
# The watcher already dedupes on (path, mtime_ns) and the diff layer
# drops out-of-playthrough saves, so widening the pattern is safe.


def _expand_patterns(pattern: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """ck3_chronicler-fi7: normalize the pattern argument to a tuple.

    Accepts a tuple/list verbatim, splits a comma-separated string for
    CLI ergonomics (typer can't carry a tuple through ``--pattern``),
    and treats a single comma-less string as a 1-element tuple. Empty
    strings are filtered so ``"autosave.ck3,"`` doesn't add an empty
    glob.
    """
    if isinstance(pattern, str):
        parts = tuple(part.strip() for part in pattern.split(","))
        return tuple(p for p in parts if p)
    return tuple(pattern)


def _startup_catchup_scan(
    *,
    factory,
    scheduler: NarrativeScheduler,
    campaign_id: str,
    registry_path: Path | None,
) -> None:
    """ck3_chronicler-xy69: re-fire any death-biography work lost when
    chronicler last shut down.

    The narrative queue is in-process state, so a crash / clean exit
    while tasks were queued leaves them gone forever. The on-death
    biography schedule fires exactly once at ingest time, so a death
    whose bio task didn't complete before shutdown never retries.

    Live signal 2026-05-10: two tracked-character deaths (62648, 64449)
    had their auto-bio tasks dropped on the floor.

    Thin wrapper around :func:`drain_for_campaign`. Consolidation
    scheduling removed (plan: cozy-coalescing-shannon).
    """
    drain_for_campaign(
        factory=factory,
        scheduler=scheduler,
        campaign_id=campaign_id,
        registry_path=registry_path,
    )


def _parse_save_at(path: Path) -> SaveSnapshot | None:
    """Parse a save file via rakaly. Returns None on failure (logged).

    Convenience wrapper for callers that don't need the raw rakaly dict
    (most of the loop). Use :func:`_parse_save_at_with_raw` when you
    need to extract raw per-character records (ck3_chronicler-6ui)."""
    result = _parse_save_at_with_raw(path)
    return result[1] if result is not None else None


def _parse_save_at_with_raw(
    path: Path,
) -> tuple[dict[str, Any], SaveSnapshot] | None:
    """Parse a save and return both the raw rakaly dict and the structured
    snapshot. Returns None on rakaly / parse failure (logged).

    The raw dict is needed to populate ``Character.save_snapshot_json``
    via :func:`extract_character_record` for tracked characters
    (ck3_chronicler-6ui). Most callers want :func:`_parse_save_at` and
    can ignore the raw dict; only the persistence step in
    :func:`_advance_baseline` reaches for the raw.
    """
    t0 = time_monotonic()
    try:
        data = convert_save_to_json(path)
    except (RakalyError, OSError) as e:
        # ck3_chronicler-27ov.9 (audit H5): OSError, not just
        # FileNotFoundError — create_subprocess/Popen can raise other
        # OSErrors (EMFILE, EACCES, partial-copy locks) that previously
        # escaped and killed the caller.
        log.warning("rakaly failed for %s: %s", path.name, e)
        return None
    t_rakaly = time_monotonic()
    try:
        snap = parse_save(data)
    except Exception:
        log.exception("parse_save failed for %s", path.name)
        return None
    t_parse = time_monotonic()
    # ck3_chronicler-0fi5: baseline timing so we can reference parse cost
    # before optimizing ingestion. rakaly = melt subprocess, parse =
    # structured-snapshot construction.
    log.info(
        "parsed %s in %.2fs (rakaly %.2fs + parse %.2fs)",
        path.name,
        t_parse - t0,
        t_rakaly - t0,
        t_parse - t_rakaly,
    )
    return data, snap


async def _parse_save_at_with_raw_async(
    path: Path,
) -> tuple[dict[str, Any], SaveSnapshot] | None:
    """Async sibling of :func:`_parse_save_at_with_raw` for the save-tail
    consumer hot path (ck3_chronicler-aerw slice 2).

    The rakaly subprocess runs via :func:`convert_save_to_json_async` so
    the child is killable on Ctrl+C / task cancellation; the CPU-bound
    :func:`parse_save` step is offloaded to a worker thread so the event
    loop keeps scheduling SSE pumps + watcher + scheduler tasks during
    the structured-snapshot construction.

    Returns ``None`` on rakaly / parse failure (logged); re-raises
    :class:`asyncio.CancelledError` after rakaly tears down.
    """
    t0 = time_monotonic()
    try:
        data = await convert_save_to_json_async(path)
    except (RakalyError, OSError) as e:
        # ck3_chronicler-27ov.9 (audit H5): OSError, not just
        # FileNotFoundError — create_subprocess_exec can raise other
        # OSErrors (EMFILE, EACCES, partial-copy locks) that previously
        # escaped and killed the consumer.
        log.warning("rakaly failed for %s: %s", path.name, e)
        return None
    t_rakaly = time_monotonic()
    try:
        snap = await asyncio.to_thread(parse_save, data)
    except Exception:
        log.exception("parse_save failed for %s", path.name)
        return None
    t_parse = time_monotonic()
    # ck3_chronicler-0fi5: baseline timing so we can reference parse cost
    # before optimizing ingestion. rakaly = melt subprocess, parse =
    # structured-snapshot construction (offloaded to a worker thread).
    log.info(
        "parsed %s in %.2fs (rakaly %.2fs + parse %.2fs)",
        path.name,
        t_parse - t0,
        t_rakaly - t0,
        t_parse - t_rakaly,
    )
    return data, snap


async def _dispatch_parse(
    pool: ProcessPoolExecutor, path: Path, tracked_ids: frozenset[int]
) -> SaveSnapshot:
    """ck3_chronicler-j86v: dispatch one save's melt+decode+parse to the
    process pool and return ONLY the SaveSnapshot.

    The worker (:func:`chronicler.save.parse_worker.parse_save_in_worker`)
    resolves the per-tracked-character raw extractions into ``snap.tracked_*``
    so the parent never needs the 127 MB raw dict back over the pool boundary.

    Module-level (not a closure) so the save-tail tests can monkeypatch it to
    inject canned snapshots without a real ProcessPoolExecutor or a real .ck3
    — a real pool runs the worker in a separate process where monkeypatches
    don't apply. ``run_in_executor`` passes args positionally (no kwargs).

    Raises RakalyError / OSError on melt failure; the caller logs + skips.
    """
    loop = asyncio.get_running_loop()
    blob, timings = await loop.run_in_executor(pool, parse_save_in_worker, path, tracked_ids)
    # ck3_chronicler-jgsg: restore the per-save parse-timing line lost when
    # j86v moved melt+decode+parse into the worker process (worker logs aren't
    # captured by the parent). rakaly = melt subprocess, parse = structured
    # snapshot, extract = per-tracked-character raw/COA resolution.
    log.info(
        "parsed %s in %.2fs (rakaly %.2fs + parse %.2fs + extract %.2fs)",
        path.name,
        timings.total_s,
        timings.rakaly_s,
        timings.parse_s,
        timings.extract_s,
    )
    # ck3_chronicler-lw47: the worker returns a msgpack blob, not the snapshot
    # object — decoding it here is ~2.6x cheaper than the pickle unpickle the
    # executor would otherwise do, and this decode is the serial parent-side
    # tail of a backlog drain.
    return decode_snapshot(blob)


def latest_save(save_dir: Path, pattern: str | tuple[str, ...] | list[str]) -> Path | None:
    """Most-recently-modified save matching ``pattern``, or None.

    ``pattern`` may be a single glob, a tuple/list of globs, or a
    comma-separated string (CLI ergonomic). Each pattern is globbed
    independently and the union (deduped by resolved path) is scanned.

    audit F-48 / ck3_chronicler-hgnb: was named _latest_save but four
    call sites (routes/tracked.py + three in cli/main.py) reach across
    the underscore — the helper is effectively part of the package's
    public surface. The leading underscore is preserved as an alias
    below so any downstream importer keeps working.
    """
    if not save_dir.exists():
        return None
    patterns = _expand_patterns(pattern)
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pat in patterns:
        for p in save_dir.glob(pat):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def saves_by_recency(save_dir: Path, pattern: str | tuple[str, ...] | list[str]) -> list[Path]:
    """Every save matching ``pattern``, newest first.

    ``latest_save`` answers "the newest save", which is what the tailer
    wants. Callers that need a save carrying a *player* need more than
    one candidate: CK3 writes ``autosave_exit.ck3`` on quit with no
    ``played_character`` root, and that file is the newest one in the
    directory exactly when a user is most likely to run auto-track.
    """
    if not save_dir.exists():
        return []
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pat in _expand_patterns(pattern):
        for p in save_dir.glob(pat):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            candidates.append(p)
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


# Back-compat alias for callers that imported via the leading-underscore
# name before audit F-48 promoted the helper.
_latest_save = latest_save


def _verify_current_save(
    *,
    save_dir: Path,
    pattern: str,
    factory,
) -> tuple[Path | None, tuple[dict[str, Any], SaveSnapshot] | None]:
    """Phase 1: parse + playthrough-verify the current on-disk save.

    Returns ``(current_save_path, parsed_or_None)``. A None-parsed
    means the save was missing or unparseable — the caller proceeds
    bus-less (no startup baseline established yet, the watcher will
    pick one up once a real save lands). Raises
    :class:`PlaythroughMismatchError` when the on-disk save belongs
    to a different campaign than the one this DB is pinned to —
    save-tail must refuse to start in that case so it doesn't ingest
    cross-campaign events.
    """
    current_save_path = _latest_save(save_dir, pattern)
    if current_save_path is None:
        return None, None
    log.info("save-ingest baseline: parsing %s", current_save_path.name)
    # ck3_chronicler-j86v: intentionally NOT routed through the parse pool.
    # This is the single-shot startup baseline read (pre-loop, before the
    # pool is hot) and it needs the raw dict for first-baseline hydration via
    # the import-style path — not the per-save drain bottleneck the pool targets.
    parsed = _parse_save_at_with_raw(current_save_path)
    if parsed is None:
        return current_save_path, None
    try:
        with session_scope(factory) as session:
            # Verify only — never pin from save-tail's startup baseline
            # read. The save in the directory might be a leftover from
            # a previous campaign; pinning is the job of explicit user
            # commands (auto-track, import-save).
            assert_playthrough_or_pin(
                session,
                observed=parsed[1].playthrough_id,
                pin_if_unset=False,
            )
    except PlaythroughMismatchError as e:
        log.error(
            "save-tail refusing to start: %s. "
            "this campaign was previously pinned to a different CK3 "
            "playthrough; pass --force-reset-playthrough on import-save "
            "or auto-track to overwrite, or use a different campaign.",
            e,
        )
        raise
    return current_save_path, parsed


def _campaign_db_is_fresh(factory) -> bool:
    """ck3_chronicler-cij: True when the campaign DB has no events.

    Empty events table is the signal that nothing has been ingested
    against this campaign yet — neither an explicit ``import-save``
    nor any prior save-tail tick. Combined with "no persisted
    baseline" + "no cached saves" upstream, this is the trigger for
    the auto-import path.

    audit F-47 / ck3_chronicler-hgnb: was reaching into db.models
    directly for the count query; the count now lives in
    db/repository.count_events so the ORM-model surface stays inside
    the repository.

    ck3_chronicler-elll: count_events promoted to a module-top import so
    the chronicler_recovered SSE frame can reuse it without re-importing
    per startup.
    """
    with session_scope(factory) as session:
        return count_events(session) == 0


def _maybe_auto_import_first_save(
    *,
    save_path: Path,
    parsed_snap: SaveSnapshot,
    factory,
    persist_path: Path | None,
    campaign_id: str,
    registry_path: Path | None,
) -> SaveSnapshot | None:
    """ck3_chronicler-cij: when the campaign DB is fresh, run import_save
    synchronously to backfill characters + vanilla memories before the
    watcher arms.

    Caller is responsible for ensuring the gating conditions (no
    persisted baseline, no cached saves, fresh DB, opt-in flag).
    Returns the SaveSnapshot to use as the seed baseline on success,
    or ``None`` if the import failed — caller should fall back to the
    silent-baseline path."""
    log.info(
        "fresh campaign DB detected (no baseline, no cached saves, no events); "
        "running auto-import on %s",
        save_path.name,
    )
    result = import_save(save_path, factory=factory)
    if not result.success:
        log.warning(
            "auto-import failed (%s); falling back to silent baseline",
            result.error,
        )
        return None
    log.info(
        "auto-import succeeded: %d characters, %d vanilla memories",
        result.chars_upserted,
        result.memories_inserted,
    )
    # Persist the parsed snap as the baseline so a restart picks up
    # exactly here without re-importing.
    _persist_baseline_safely(persist_path, parsed_snap)
    # ck3_chronicler-8fo: sync the registry row's last_event_at and the
    # cqo identity overview from this seed save. Without this the
    # registry stays NULL on every column populated by _advance_baseline
    # until a SECOND autosave lands and triggers the diff path — which
    # leaves the Library card's marquee byline (cqo T8) blank for a
    # whole tick on first impression. Mirrors the call sequence at the
    # tail of process_save_pair / _advance_baseline so a campaign that
    # only ever does cij seeding still surfaces full identity on the
    # Library card.
    # ck3_chronicler-9xa6: same in-game-date thread-through as the
    # diff-path tail (apply_campaign_overview_from_snap there) so the
    # cij seed-only path also keeps last_event_in_game_date current.
    touch_last_event_at(
        campaign_id,
        in_game_date=parsed_snap.current_date or None,
        registry=registry_path,
    )
    apply_campaign_overview_from_snap(campaign_id, parsed_snap, registry=registry_path)
    return parsed_snap


def _select_starting_baseline(
    *,
    persisted_baseline: SaveSnapshot | None,
    verified_current_parse: tuple[dict[str, Any], SaveSnapshot] | None,
    cache: SaveCache,
) -> SaveSnapshot | None:
    """Phase 2: decide whether the persisted baseline + cache survive.

    If the on-disk save's playthrough doesn't match the persisted
    baseline, the persisted baseline (and any pending cached saves,
    captured under the old playthrough) are stale and must be
    discarded. Returns the chosen starting baseline, or ``None`` when
    none is available — a later phase will adopt the first parseable
    save instead.
    """
    if persisted_baseline is None:
        return None
    if (
        verified_current_parse is None
        or verified_current_parse[1].playthrough_id == persisted_baseline.playthrough_id
    ):
        return persisted_baseline
    log.info(
        "persisted baseline playthrough_id=%s mismatches on-disk save %s; "
        "discarding baseline and any pending cached saves",
        persisted_baseline.playthrough_id,
        verified_current_parse[1].playthrough_id,
    )
    for stale in cache.pending():
        cache.mark_processed(stale)
    return None


async def run_save_ingest(
    *,
    save_dir: Path,
    db_path: Path,
    campaign_id: str,
    registry_path: Path | None = None,
    pattern: str | tuple[str, ...] | list[str] = DEFAULT_SAVE_PATTERN,
    biography_provider=None,  # NarrativeProvider | None
    stop_event: asyncio.Event | None = None,
    cache_max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    parse_concurrency: int = DEFAULT_PARSE_CONCURRENCY,  # ck3_chronicler-r8l1
    event_bus: EventBus | None = None,  # ck3_chronicler-ek2
    narrative_queue: NarrativeQueueState | None = None,  # ck3_chronicler-eev
    auto_import_first_save: bool = True,  # ck3_chronicler-cij
    on_scheduler_ready: Callable[[NarrativeScheduler | None], None] | None = None,
    on_foreign_playthrough: Callable[[str, Path], None] | None = None,
) -> None:
    """Long-running async loop: watch save_dir and ingest events.

    Establishes a baseline on startup. Two paths:

    1. **Persisted baseline (ck3_chronicler-96m):** if a previous run
       wrote ``<db_path>.baseline.json`` AND its ``playthrough_id``
       matches the current on-disk save, the persisted snapshot is used
       as the starting baseline and the on-disk save is immediately
       diffed against it. This catches up state-diff events accumulated
       across multi-campaign restarts that would otherwise be silently
       lost.
    2. **Silent baseline (no persisted file or playthrough mismatch):**
       parse the latest existing save as the baseline without emitting
       events. Today's behavior, preserved as the fallback.

    Each subsequent save detected by the watcher gets diffed against the
    previous snapshot and emitted through the same insert path the
    debug_log tailer uses.

    ``biography_provider``: if non-None, a :class:`BiographyScheduler`
    runs alongside, scheduling biographies for tracked characters' death
    events. Pass ``None`` for a diagnostic ingest that only writes
    events to the DB.

    ``pattern``: glob applied within ``save_dir``. Default
    ``autosave.ck3`` focuses on the current autosave and ignores rolling
    backups (which would re-process old states). Override to ``*.ck3``
    if you want manual-save handling too.

    ``cache_max_bytes`` (ck3_chronicler-1js): per-campaign save-cache
    disk-usage cap. Each detected autosave is copied into the cache the
    moment we see it (insulating us from CK3's autosave rotation), then
    parsed + ingested + deleted. If ingestion falls behind the watcher
    sustainedly (Speed-5 marathon), oldest cached saves are GC'd to
    keep disk usage bounded. Default 8 GB (ck3_chronicler-5x47 — old
    2 GB cap dropped saves the ingest worker still needed in
    adventurer-mode smoke).
    """
    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)

    # ck3_chronicler-j86v: the GIL-bound melt+decode+parse (~86% of the ~7s
    # per-save floor) runs in a worker PROCESS so a backlog drain fans across
    # cores instead of saturating one. Owned for the loop's lifetime; torn
    # down in the finally block (cancel_futures) so no rakaly grandchild
    # survives loop exit.
    #
    # The mp context is EXPLICITLY spawn (#60). Linux defaults to fork, and a
    # forked worker inherits every open fd including uvicorn's LISTENING socket
    # on :8000. Kill the parent ungracefully and the workers are reparented to
    # init still holding that socket, so the port stays bound by processes that
    # will never answer a request: the next launch cannot bind, Vite comes up
    # alone, and every /api call returns a proxy Bad Gateway until someone finds
    # the strays by hand. One hard kill poisoned every subsequent launch.
    #
    # Windows was always spawn, so the worker was already written to survive a
    # re-import (`parse_save_in_worker` is module-level and its args pickle).
    # This makes both platforms take the path the code was already designed for.
    pool_workers = _resolve_parse_pool_workers(parse_concurrency)
    parse_pool = ProcessPoolExecutor(
        max_workers=pool_workers, mp_context=multiprocessing.get_context("spawn")
    )
    log.info("save-ingest parse pool: %d workers", pool_workers)

    def _is_tracked(character_id: int) -> bool:
        return is_character_tracked(campaign_id, character_id, registry=registry_path)

    def _is_paused(character_id: int) -> bool:
        paused, _ = get_tracked_status(campaign_id, character_id, registry=registry_path)
        return paused

    def _get_bumped_at(character_id: int) -> str | None:
        _, bumped = get_tracked_status(campaign_id, character_id, registry=registry_path)
        return bumped

    scheduler: NarrativeScheduler | None = (
        NarrativeScheduler(
            factory,
            biography_provider,
            campaign_uuid=campaign_id,
            is_tracked=_is_tracked,
            is_paused=_is_paused,
            # ck3_chronicler-gx7b: scheduler-side global-pause gate reads
            # the same cached helper as the ingest call sites, so the
            # PUT-toggle handler's cache reset invalidates both.
            is_globally_paused=_is_llm_paused,
            get_bumped_at=_get_bumped_at,
            queue_state=narrative_queue,
        )
        if biography_provider is not None
        else None
    )
    # ck3_chronicler-fiv6 (was kdf): hand the scheduler back to the
    # orchestrator so it can stash it on app.state for diagnostics. The
    # defer/drain endpoints that previously consumed it were removed
    # post-tbrm.3 along with the local-tier providers.
    if on_scheduler_ready is not None:
        try:
            on_scheduler_ready(scheduler)
        except Exception:
            log.exception("on_scheduler_ready callback raised")

    persist_path = baseline_path_for(db_path)
    # ck3_chronicler-elll: load_baseline now returns a BaselineLoad
    # carrying snapshot + forensic fields (generation, persisted_at).
    # Keep ``persisted_load`` in scope — the boot log line below + the
    # chronicler_recovering SSE frame consume its forensic fields.
    persisted_load = load_baseline(persist_path)
    persisted_baseline = persisted_load.snapshot if persisted_load is not None else None
    if persisted_load is not None:
        log.info(
            "save-ingest baseline loaded: gen=%s persisted_at=%s date=%s chars=%d",
            persisted_load.generation
            if persisted_load.generation is not None
            else "None (legacy v1)",
            persisted_load.persisted_at
            if persisted_load.persisted_at is not None
            else "None (legacy v1)",
            persisted_load.snapshot.current_date,
            len(persisted_load.snapshot.characters),
        )
    cache = SaveCache(cache_dir_for(get_data_dir(), campaign_id))

    # 4-phase startup. See each helper's docstring; the orchestration
    # threads `last_snapshot` (the high-water mark) through them.
    # ck3_chronicler 2026-05-09: a playthrough mismatch on the latest
    # save in save_dir is NOT a fatal startup error — it's the normal
    # state when the user has just adopted a campaign for a CK3 game
    # they haven't started playing yet (the on-disk autosave is a
    # leftover from a prior CK3 session in a different playthrough).
    # Previously we crashed save-tail on this and the orchestrator's
    # _on_done buried the exception silently; symptom was 'tail keeps
    # turning on/off, nothing in the watcher list' for hours of play.
    # Now we treat it as 'no usable startup baseline' — the watcher
    # arms regardless, and once a matching save lands the diff layer's
    # _is_advance_candidate filter (which already drops mismatched
    # saves at run-time) silently pivots us onto the new playthrough.
    # ck3_chronicler-kp8f: each boot phase below does a synchronous rakaly
    # subprocess + 70 MB json.loads (Phase 1 + Phase 3) or heavy SQL/diff
    # work (Phase 4). Wrap each in asyncio.to_thread so uvicorn (sharing
    # the event loop) stays responsive while the drain churns. Pre-fix
    # the entire drain wall-time blocked /api/* — Bad Gateway on
    # launch.bat, FE frozen on "the chronicler stirs" for minutes when
    # the cache had a multi-save backlog.
    try:
        current_save_path, verified_current_parse = await asyncio.to_thread(
            _verify_current_save,
            save_dir=save_dir,
            pattern=pattern,
            factory=factory,
        )
    except PlaythroughMismatchError as e:
        log.warning(
            "save-tail startup: latest on-disk save is from a different "
            "playthrough (%s) — likely a leftover autosave from a prior "
            "CK3 session. Skipping startup baseline; the watcher will pick "
            "up matching saves once CK3 writes them.",
            e,
        )
        current_save_path, verified_current_parse = None, None

    last_snapshot = _select_starting_baseline(
        persisted_baseline=persisted_baseline,
        verified_current_parse=verified_current_parse,
        cache=cache,
    )

    # ck3_chronicler-27ov.12 (audit H6/J3): collapse the three divergent
    # "ingest one cached save" paths into the single live consumer below.
    # Rather than two bespoke startup drains (_drain_pending_cache +
    # _catch_up_to_current) that each re-implemented sidecar-skip -> parse ->
    # pin-gate -> establish/advance -> mark_processed, seed the pipeline queue
    # with the pre-existing work — the saves a prior run left cached, plus a
    # freshly-cached copy of the current on-disk save — and let the consumer
    # drain it through the exact code path live saves take. The pin gate,
    # sidecar skip, and fresh-DB cij bootstrap now exist in one place (the
    # consumer). The watcher arms immediately; the backlog drains through the
    # live pipeline (ck3_chronicler-ylbu).
    seeded: list[CachedSave] = []
    pending_cache_count = len(cache.pending())
    for cached in cache.pending():
        # ck3_chronicler-2faj: skip foreign saves identifiable from the sidecar
        # without paying rakaly (~7s/save), but only when we have a baseline to
        # compare against. The consumer also drops foreign saves — but only
        # AFTER parsing — so doing it here keeps that cost off the startup
        # backlog drain. With no baseline we can't tell foreign from own, so
        # those fall through to the consumer's post-parse pin gate.
        if (
            cached.playthrough_id is not None
            and last_snapshot is not None
            and last_snapshot.playthrough_id
            and cached.playthrough_id != last_snapshot.playthrough_id
        ):
            log.debug(
                "skipping cached %s — sidecar says foreign playthrough %s",
                cached.path.name,
                cached.playthrough_id,
            )
            cache.mark_processed(cached)
            continue
        cache.protect(cached.seqno)
        seeded.append(cached)

    # Freshly cache the current on-disk save so the consumer catches up to it.
    # Only when it parsed + passed the playthrough verify above — a foreign
    # leftover or unparseable save is left for the watcher to pick up. Often
    # the same file as the last cached entry; _advance_baseline's stale-read
    # guard no-ops that case.
    if verified_current_parse is not None and current_save_path is not None:
        cached_current = cache.cache_save(current_save_path)
        if cached_current is not None:
            cache.protect(cached_current.seqno)
            seeded.append(cached_current)

    # ck3_chronicler-elll: bracket the seeded-backlog drain with the
    # chronicler_recovering / chronicler_recovered SSE frames the AppShell
    # banner renders. Fires only when there's a surviving baseline to recover
    # against — fresh adopts (last_snapshot is None) skip the banner. The
    # recovered frame closes the window with the events-table delta once the
    # consumer has drained every seeded item (tracked via recovery_remaining
    # in the consumer loop below).
    recovery_active = last_snapshot is not None and event_bus is not None
    recovery_remaining = len(seeded) if recovery_active else 0
    events_before_recovery: int | None = None

    def _publish_recovered() -> None:
        nonlocal recovery_active
        if not recovery_active:
            return
        recovery_active = False
        with session_scope(factory) as session:
            events_after_recovery = count_events(session)
        events_ingested = max(0, events_after_recovery - (events_before_recovery or 0))
        if event_bus is not None:
            try:
                event_bus.publish(
                    campaign_id,
                    {"kind": "chronicler_recovered", "events_ingested": events_ingested},
                )
            except Exception:
                log.exception("chronicler_recovered publish failed; continuing")
        log.info(
            "save-ingest recovered: ingested %d events from cached + current",
            events_ingested,
        )

    if recovery_active and event_bus is not None:
        try:
            event_bus.publish(
                campaign_id,
                {
                    "kind": "chronicler_recovering",
                    "baseline_persisted_at": (
                        persisted_load.persisted_at if persisted_load is not None else None
                    ),
                    "baseline_date": last_snapshot.current_date,
                    "baseline_generation": (
                        persisted_load.generation if persisted_load is not None else None
                    ),
                    "pending_cache_count": pending_cache_count,
                },
            )
        except Exception:
            log.exception("chronicler_recovering publish failed; continuing")
        log.info(
            "save-ingest recovering: baseline date=%s pending_cache=%d",
            last_snapshot.current_date,
            pending_cache_count,
        )
        # Snapshot the events row count so _publish_recovered can compute the
        # delta once the seeded backlog drains.
        with session_scope(factory) as session:
            events_before_recovery = count_events(session)
        # No seeded backlog (baseline but nothing on disk / all cache foreign):
        # close the recovery window immediately so the banner doesn't hang.
        if recovery_remaining == 0:
            _publish_recovered()

    # ck3_chronicler-xy69: re-schedule death-biography work lost when the
    # chronicler last exited (in-process queue_state evaporates on shutdown).
    if scheduler is not None:
        _startup_catchup_scan(
            factory=factory,
            scheduler=scheduler,
            campaign_id=campaign_id,
            registry_path=registry_path,
        )

    log.info(
        "starting save-ingest loop: campaign=%s save_dir=%s pattern=%r biography=%s",
        campaign_id,
        save_dir,
        pattern,
        biography_provider.name if biography_provider is not None else "disabled",
    )

    # ck3_chronicler-p1h: producer/consumer split. Watcher does only the
    # fast path (cache_save + enqueue) — guaranteed never to block on
    # rakaly or DB work, so it can keep up with arbitrarily fast CK3
    # autosave rotation. Consumer drains the queue at its own pace,
    # parses + diffs + ingests + GCs. Bounded queue (size 50) gives
    # graceful back-pressure: if the consumer falls way behind the
    # producer's await queue.put() yields without dropping events.
    pipeline_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=50)
    _DRAIN_SENTINEL = object()

    def _publish_cache_state() -> None:
        """ck3_chronicler-7w5: surface SaveCache observability over the
        SSE bus so the v0.7 Save-tail page can show ingestion lag +
        the lossy-GC banner. Failures swallowed — observability is
        best-effort and must never break ingest."""
        if event_bus is None:
            return
        try:
            event_bus.publish(
                campaign_id,
                {"kind": "cache_state", **cache.snapshot()},
            )
        except Exception:
            log.exception("event_bus.publish failed for cache_state")

    async def _producer() -> None:
        try:
            # ck3_chronicler-27ov.12: feed the seeded startup backlog
            # (prior-run cached saves + the current on-disk save) before
            # arming the watcher, so the consumer drains it through the
            # single live ingest path. Each seeded seqno was protected at
            # seed time; the consumer's mark_processed releases it.
            for cached in seeded:
                await pipeline_queue.put(cached)
            async for evt in watch_saves(save_dir, pattern=pattern, stop_event=stop_event):
                cached = cache.cache_save(evt.path)
                if cached is None:
                    continue
                # ck3_chronicler-5x47: shield this seqno from gc_oldest the
                # moment it enters the pipeline. Cleared by mark_processed
                # in the consumer (every consumer exit path calls it
                # exactly once on the popped item). Without this protect,
                # the gc_oldest call after each consumer iteration can
                # drop the next-pending file out from under the producer's
                # backlog when the cap is exceeded.
                cache.protect(cached.seqno)
                _publish_cache_state()
                await pipeline_queue.put(cached)
        finally:
            await pipeline_queue.put(_DRAIN_SENTINEL)

    async def _parse_cached(cached):
        # ck3_chronicler-j86v: melt+decode+parse run in a worker PROCESS so
        # the GIL-bound decode (~50% of the ~7s floor) fans across cores
        # instead of serializing onto the single ingest core under the GIL.
        # The worker returns ONLY the snapshot (tracked extractions ride on
        # it via snap.tracked_*), never the 127 MB raw dict — returning the
        # dict regressed 50% on the pickle round-trip (see scripts/bench_parse.py).
        # Current tracked set (player + family, small). Per-save registry hit;
        # correctness-first — cache + invalidate-on-track if it shows up in the
        # drain profile. Staleness between dispatch and consume is acceptable:
        # a missing extraction leaves the column intact and the next save catches up.
        tracked_ids = frozenset(get_tracked_character_ids(campaign_id, registry=registry_path))
        try:
            return await _dispatch_parse(parse_pool, cached.path, tracked_ids)
        except (RakalyError, OSError) as e:
            log.warning("rakaly failed for %s: %s", cached.path.name, e)
            return None
        except Exception:
            log.exception("parse worker failed for %s", cached.path.name)
            return None

    async def _consume_one(cached, parsed) -> None:
        """Ingest a single parsed save (one consumer-loop item).

        Extracted from the consumer loop body (ck3_chronicler-27ov.9,
        audit H5) so the loop can guard each item individually — one bad
        save must skip that save, never kill the consumer.
        """
        nonlocal last_snapshot
        # ck3_chronicler-2faj: same sidecar short-circuit as the startup
        # drain — drop a seqno we already know is foreign. Now applied
        # after the parse (the parse is dispatched before we get here),
        # so a foreign seqno's parse is wasted only during re-drains;
        # the live tail is single-playthrough and never hits this.
        if (
            cached.playthrough_id is not None
            and last_snapshot is not None
            and last_snapshot.playthrough_id
            and cached.playthrough_id != last_snapshot.playthrough_id
        ):
            log.debug(
                "skipping cached %s — sidecar says foreign playthrough %s",
                cached.path.name,
                cached.playthrough_id,
            )
            cache.mark_processed(cached)
            _publish_cache_state()
            return
        if parsed is None:
            cache.mark_processed(cached)
            _publish_cache_state()
            return
        # ck3_chronicler-j86v: _parse_cached now returns the SaveSnapshot only
        # (the parse worker resolved tracked extractions into snap.tracked_*).
        snap = parsed
        # ck3_chronicler-2faj: persist the observed playthrough_id
        # for future drains of this seqno (small window — mark_
        # processed below deletes both files — but covers the case
        # where the process exits between parse and mark_processed).
        if snap.playthrough_id:
            cache.tag_playthrough(cached, snap.playthrough_id)

        # ck3_chronicler 2026-05-09: when last_snapshot is None we are
        # about to establish baseline from this save. Refuse to do so
        # if the save's playthrough_id doesn't match the campaign's
        # pinned playthrough — otherwise a leftover autosave_exit.ck3
        # from a prior CK3 session can hijack the baseline and cause
        # save-tail to drop every subsequent save from the actual
        # current playthrough. _is_advance_candidate already does this
        # for the steady-state path; the bootstrap path was missing
        # the same gate.
        if last_snapshot is None:
            try:
                with session_scope(factory) as session:
                    assert_playthrough_or_pin(
                        session,
                        observed=snap.playthrough_id,
                        pin_if_unset=False,
                    )
            except PlaythroughMismatchError as e:
                log.warning(
                    "save %s playthrough mismatch during baseline "
                    "bootstrap (%s); dropping without establishing "
                    "baseline — waiting for a save from the campaign's "
                    "pinned playthrough.",
                    cached.path.name,
                    e,
                )
                cache.mark_processed(cached)
                _publish_cache_state()
                return

        if last_snapshot is None:
            # ck3_chronicler-qtz: re-apply the cij gates here so the
            # watch-and-adopt arrival path runs auto-import (seeds
            # tracked_characters + vanilla memories) instead of just
            # silently baselining. Without this, `chronicler dev
            # --campaign <name>` started against an empty save dir
            # leaves tracked_characters empty when the first user
            # save lands, and the diff layer's tracked_filter drops
            # every subsequent event until the user runs
            # `chronicler auto-track` manually.
            if auto_import_first_save and _campaign_db_is_fresh(factory):
                log.info(
                    "first snapshot via watcher on fresh DB; running auto-import on %s",
                    cached.path.name,
                )
                # ck3_chronicler-27ov.3 (audit H7): the fresh-DB auto-import
                # calls the SYNC convert_save_to_json (300s timeout) + a
                # ~40k-row upsert loop. Run it off the uvicorn loop like the
                # startup gate does (kp8f) so /api/* and SSE heartbeats stay
                # responsive.
                seeded = await asyncio.to_thread(
                    _maybe_auto_import_first_save,
                    save_path=cached.path,
                    parsed_snap=snap,
                    factory=factory,
                    persist_path=persist_path,
                    campaign_id=campaign_id,
                    registry_path=registry_path,
                )
                if seeded is not None:
                    last_snapshot = seeded
                    cache.mark_processed(cached)
                    _publish_cache_state()
                    return
                # Auto-import failed; fall through to silent baseline so
                # at least the watcher arms with a valid high-water mark.

            log.info(
                "first snapshot established (no prior baseline): %s, %d chars",
                snap.current_date,
                len(snap.characters),
            )
            last_snapshot = snap
            # ck3_chronicler 2026-05-09: also flush the registry
            # overview so the Library card reflects the current
            # in-game date (and player identity) even on the very
            # first watcher tick. Pre-fix: current_in_game_date
            # stayed pinned at the adopt-time value until the
            # second save landed (a full month or more in the
            # user's workflow), which read as "the tail isn't
            # working".
            _persist_per_save_state(
                snap,
                campaign_id=campaign_id,
                registry_path=registry_path,
                persist_path=persist_path,
            )
            cache.mark_processed(cached)
            _publish_cache_state()
            return

        # ck3_chronicler-27ov.3 (audit H7): _advance_baseline is a
        # multi-second diff + hundreds of inserts + three registry
        # transactions per tick, and its fresh-DB bootstrap shells out to
        # the SYNC convert_save_to_json. Run it off the uvicorn loop — the
        # startup phases already wrap it in to_thread (proven thread-safe);
        # calling it synchronously here froze /api/* and SSE heartbeats
        # (tuned to 2s) whenever a backlog drained.
        last_snapshot = await asyncio.to_thread(
            _advance_baseline,
            snap,
            last_snapshot=last_snapshot,
            factory=factory,
            scheduler=scheduler,
            registry_path=registry_path,
            campaign_id=campaign_id,
            save_path_name=cached.path.name,
            persist_path=persist_path,
            event_bus=event_bus,
            on_foreign_playthrough=on_foreign_playthrough,
        )
        cache.mark_processed(cached)
        cache.gc_oldest(cache_max_bytes)
        _publish_cache_state()

    async def _consumer() -> None:
        nonlocal recovery_remaining
        # ck3_chronicler-r8l1: parse up to `parse_concurrency` saves at once
        # (each rakaly run is an independent, side-effect-free subprocess, so
        # N overlap in the OS — draining the ~80%/9.5s bottleneck N-ways)
        # while map_ordered_bounded hands results to this loop in strict
        # pull (= save) order. The diff/baseline tail in _consume_one stays
        # exactly as serial and in-order as the old one-at-a-time loop, so
        # events are byte-identical — only the parse stage parallelises.
        async for cached, parsed in map_ordered_bounded(
            pipeline_queue,
            _DRAIN_SENTINEL,
            _parse_cached,
            concurrency=parse_concurrency,
        ):
            # ck3_chronicler-27ov.9 (audit H5): per-item guard. The module
            # contract is "never stop on bad data" — a save that fails
            # anywhere in the diff/baseline tail (locked registry, poisoned
            # session, unexpected save shape) is logged and skipped; the
            # consumer lives on. Pre-fix, an escaped exception killed the
            # consumer while the producer kept caching saves, protecting
            # seqnos and blocking on the full queue — a half-dead pipeline
            # that looked alive in the UI.
            try:
                await _consume_one(cached, parsed)
            except Exception:
                log.exception("ingest failed for %s; skipping this save", cached.path.name)
                # Idempotent — releases the 5x47 protect and deletes the
                # cached file even when the body got partway there already.
                cache.mark_processed(cached)
                _publish_cache_state()
            finally:
                # ck3_chronicler-27ov.12: a seeded item is "drained"
                # whether it ingested, was skipped (foreign/parse-fail), or
                # errored — count it toward closing the recovery window
                # either way. Live items after recovery no-op this guard.
                if recovery_active and recovery_remaining > 0:
                    recovery_remaining -= 1
                    if recovery_remaining == 0:
                        _publish_recovered()

    # ck3_chronicler-27ov.9 (audit H5): TaskGroup, not gather — if one task
    # still dies (the per-item guard makes that rare), the sibling is
    # cancelled with it instead of being orphaned.
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_producer())
            tg.create_task(_consumer())
    finally:
        # ck3_chronicler-j86v: tear the parse pool down on stop_event / Ctrl+C /
        # normal exit. Python 3.11 cancel_futures cancels NOT-YET-STARTED tasks;
        # a worker mid-melt finishes its current save (bounded by the rakaly
        # timeout) — so no rakaly grandchild outlives the loop. Verified
        # empirically (5 tasks / 2 workers, teardown mid-flight): in-flight
        # workers reap their blocking subprocess.run rakaly child before
        # exiting, queued tasks cancel, and zero rakaly processes survive.
        parse_pool.shutdown(wait=True, cancel_futures=True)
        if scheduler is not None:
            # ck3_chronicler-91hh: last-chance death-bio catch-up on graceful
            # shutdown — recover any DB-recorded death that lacks a scheduled
            # biography now, instead of waiting for the next launch's
            # _startup_catchup_scan. Best-effort: a failure here must not
            # prevent the scheduler drain or the rest of teardown.
            try:
                drain_for_campaign(
                    factory=factory,
                    scheduler=scheduler,
                    campaign_id=campaign_id,
                    registry_path=registry_path,
                )
            except Exception:
                log.exception("91hh teardown drain_for_campaign failed")
            await scheduler.drain()
        if biography_provider is not None and hasattr(biography_provider, "aclose"):
            await biography_provider.aclose()
        engine.dispose()
        # ck3_chronicler-tail-pip: publish a save_tail_stopped frame so the
        # AppShell pip can flip from --live to "Tail unavailable" without
        # waiting for the SSE channel to error out 3 times. Without this,
        # halting save-tail (e.g. via /api/migrate/halt-save-tail before a
        # schema migration) leaves uvicorn + the SSE channel healthy, so
        # the EventSource never errors and the pip stays --live even
        # though no saves are being watched. Best-effort: failures are
        # swallowed, the existing onerror dead-threshold remains the
        # backstop for pathological cases (SSE bus crash, process kill).
        if event_bus is not None:
            try:
                event_bus.publish(
                    campaign_id,
                    {"kind": "save_tail_stopped", "stopped_at": _now_iso()},
                )
            except Exception:
                log.exception("event_bus.publish failed for save_tail_stopped")
        log.info("save-ingest loop stopped: campaign=%s", campaign_id)
