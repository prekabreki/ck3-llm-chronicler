"""Async producer/consumer pipeline + run_save_ingest lifecycle: burst
draining, slow-consumer backpressure, off-event-loop advance_baseline,
non-blocking boot drain, save_tail_stopped on exit, teardown drain.

Split from the test_save_ingest monolith (ck3_chronicler-27ov.72)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.db import (
    Base,
    make_engine_for_path,
)
from chronicler.save.baseline import baseline_path_for, load_baseline, save_baseline
from chronicler.save.ingest import (
    run_save_ingest,
)
from chronicler.save.parse import (
    SaveSnapshot,
)
from tests.helpers.ingest import (
    _char,
    _FakeProvider,
    _no_op_watch,
    _patch_parse_save,
)


# --- ck3_chronicler-p1h: producer/consumer pipeline split ---
@pytest.mark.asyncio
async def test_producer_consumer_pipeline_drains_burst_of_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5 saves arrive in a burst (the 'Speed 5 marathon' case): all
    five must end up cached + ingested. The producer's only awaitable
    is cache_save + queue.put, so it never blocks on parse latency.

    Synthetic _parse_save_at_with_raw sleeps to model rakaly's slow
    parse — we verify the consumer drains the queue at its own pace
    rather than dropping saves."""
    from chronicler.save.cache import SaveCache, cache_dir_for
    from chronicler.save.watcher import SaveFileEvent

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()
    campaign_id = "test-p1h"

    # Five fake autosaves on disk so cache_save has something to copy.
    save_files = []
    for i in range(5):
        f = save_dir / f"autosave_{i}.ck3"
        f.write_bytes(f"opaque save {i}".encode())
        save_files.append(f)

    monthly_snaps = [
        SaveSnapshot(
            playthrough_id="uuid-A",
            ck3_version="1.19.0.4",
            bookmark_date="1066.9.15",
            current_date=f"1066.{i + 1}.15",
            player_character_id=1234,
            characters={1234: _char(1234)},
        )
        for i in range(5)
    ]

    # Watcher yields the five SaveFileEvents back-to-back without
    # awaiting between them — models a Speed 5 burst.
    async def _burst_watcher(*_args, **_kwargs):
        for f in save_files:
            yield SaveFileEvent(path=f, mtime_ns=1, size_bytes=len(f.read_bytes()))

    # Slow parse — 50ms per call, total 250ms. If the watcher were
    # serial the test would still pass; the value of the test is that
    # the pipeline DOES split work and the producer can put 5 events
    # on the queue while the consumer is still working through them.
    parse_calls: list[Path] = []

    def _slow_parse(path: Path):
        parse_calls.append(path)
        # Match the parse to a snapshot by the order it was cached
        idx = len(parse_calls) - 1
        return ({}, monthly_snaps[idx])

    _patch_parse_save(monkeypatch, _slow_parse)
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _burst_watcher)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    # Pass a pattern that won't match the fake autosave_<i>.ck3 files
    # left in save_dir — the burst watcher feeds those events directly,
    # so the startup _verify_current_save scan should be a no-op rather
    # than parsing the latest fixture file as a 6th event.
    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        pattern=("__no-match__.ck3",),
    )

    # All five saves were parsed (consumer didn't drop any)
    assert len(parse_calls) == 5

    # Cache fully drained — every save mark_processed'd
    cache = SaveCache(cache_dir_for(data_dir, campaign_id))
    assert cache.pending() == []

    # Final baseline reflects the latest snap
    final_load = load_baseline(baseline_path_for(db_path))
    assert final_load is not None
    final = final_load.snapshot
    assert final.current_date == "1066.5.15"


@pytest.mark.asyncio
async def test_producer_continues_during_slow_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tighter version of the burst test: confirm the producer cached
    all saves BEFORE the consumer finished processing the first one
    (the property the producer/consumer split actually delivers)."""
    import asyncio as _asyncio

    from chronicler.save.cache import SaveCache, cache_dir_for
    from chronicler.save.watcher import SaveFileEvent

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()
    campaign_id = "test-p1h-2"

    save_files = []
    for i in range(3):
        f = save_dir / f"autosave_{i}.ck3"
        f.write_bytes(f"opaque save {i}".encode())
        save_files.append(f)
    snap = SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.1.1",
        player_character_id=1234,
        characters={1234: _char(1234)},
    )

    cached_count_when_first_parse_started = -1
    parse_started = _asyncio.Event()

    async def _burst_watcher(*_args, **_kwargs):
        for f in save_files:
            yield SaveFileEvent(path=f, mtime_ns=1, size_bytes=len(f.read_bytes()))

    def _slow_parse(path: Path):
        nonlocal cached_count_when_first_parse_started
        if cached_count_when_first_parse_started < 0:
            # Snapshot the cache state on the first parse call —
            # producer should already have all 3 events queued/cached.
            cache = SaveCache(cache_dir_for(data_dir, campaign_id))
            cached_count_when_first_parse_started = len(cache.pending())
            parse_started.set()
        return ({}, snap)

    _patch_parse_save(monkeypatch, _slow_parse)
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _burst_watcher)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    # See sibling burst test: opt out of the startup save-dir scan so
    # the fake autosave_<i>.ck3 fixtures aren't picked up as a 4th event.
    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        pattern=("__no-match__.ck3",),
    )

    # By the time the first parse fired, the producer should have
    # cached MORE than just the one currently being parsed (because
    # the producer doesn't wait for parse to finish before caching the
    # next save). The exact number depends on scheduler timing — we
    # require at least 2 to prove the split is doing its job.
    assert cached_count_when_first_parse_started >= 2, (
        f"producer only cached {cached_count_when_first_parse_started} save(s) "
        "before consumer started parsing — pipeline isn't split"
    )


@pytest.mark.asyncio
async def test_consumer_runs_advance_baseline_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-27ov.3 (audit H7): the live consumer must run
    _advance_baseline via asyncio.to_thread, not synchronously on the uvicorn
    loop. A multi-second diff + hundreds of inserts + three registry
    transactions per tick otherwise froze /api/* and the 2s SSE heartbeats
    whenever a backlog drained. We assert the call lands on a worker thread,
    not the loop's main thread."""
    import threading

    from chronicler.save import ingest as ingest_module
    from chronicler.save.watcher import SaveFileEvent

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()
    campaign_id = "test-27ov3"

    # Pre-establish a baseline so the consumer takes the steady-state
    # _advance_baseline path, not the first-snapshot branch.
    base_snap = SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.1.15",
        player_character_id=1234,
        characters={1234: _char(1234)},
    )
    save_baseline(baseline_path_for(db_path), base_snap)

    save_file = save_dir / "autosave_1.ck3"
    save_file.write_bytes(b"opaque")
    next_snap = SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.2.15",
        player_character_id=1234,
        characters={1234: _char(1234)},
    )

    async def _watcher(*_args, **_kwargs):
        yield SaveFileEvent(path=save_file, mtime_ns=1, size_bytes=6)

    _patch_parse_save(monkeypatch, lambda path: ({}, next_snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _watcher)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    main_thread = threading.current_thread()
    seen_threads: list[threading.Thread] = []
    real_advance = ingest_module._advance_baseline

    def _capturing_advance(*args, **kwargs):
        seen_threads.append(threading.current_thread())
        return real_advance(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "_advance_baseline", _capturing_advance)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        pattern=("__no-match__.ck3",),
    )

    assert seen_threads, "_advance_baseline was never called by the consumer"
    assert all(t is not main_thread for t in seen_threads), (
        "consumer ran _advance_baseline on the event-loop thread; it must run "
        "via asyncio.to_thread (audit H7)"
    )


# --- ck3_chronicler-kp8f: boot drain must not block the asyncio loop ---
@pytest.mark.asyncio
async def test_boot_does_not_block_event_loop_during_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boot-time rakaly parses (Phase 1 verify + Phase 3 drain) must run
    via asyncio.to_thread so uvicorn (sharing the event loop) keeps
    responding to /api/* while the drain churns. Pre-fix the synchronous
    parses blocked uvicorn-bind for the entire drain wall-time (~10s per
    pending save on a real ~70MB autosave), surfacing as Bad Gateway / FE
    'chronicler stirs' frozen for minutes."""
    import asyncio
    import time

    from chronicler.save.cache import SaveCache, cache_dir_for

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    save_file = save_dir / "autosave.ck3"
    save_file.write_bytes(b"opaque content")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "chronicler-data"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-kp8f"
    playthrough = "uuid-kp8f"

    snap = SaveSnapshot(
        playthrough_id=playthrough,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    save_baseline(baseline_path_for(db_path), snap)

    # 3 pending cache files. Pre-fix the drain blocks the loop for
    # 3 * PARSE_SLEEP seconds. Post-fix the drain runs on a worker
    # thread and the probe below keeps ticking.
    cache_dir = cache_dir_for(data_dir, campaign_id)
    cache = SaveCache(cache_dir)
    cache.cache_save(save_file)
    cache.cache_save(save_file)
    cache.cache_save(save_file)

    PARSE_SLEEP = 0.15

    def slow_parse(path: Path):
        time.sleep(PARSE_SLEEP)
        return ({}, snap)

    _patch_parse_save(monkeypatch, slow_parse)
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    boot_done = asyncio.Event()
    probe_ticks = 0

    async def probe() -> None:
        nonlocal probe_ticks
        # Sleep tightly so we accumulate many ticks during the boot
        # window. If the loop is blocked the loop's IO selector never
        # gets a chance to expire this sleep — probe_ticks stays at 0
        # until boot_done fires (at which point the while-loop exits).
        while not boot_done.is_set():
            await asyncio.sleep(0.01)
            probe_ticks += 1

    async def boot() -> None:
        try:
            await run_save_ingest(
                save_dir=save_dir,
                db_path=db_path,
                campaign_id=campaign_id,
                registry_path=registry_path,
            )
        finally:
            boot_done.set()

    await asyncio.gather(probe(), boot())

    # 3 drain parses + 1 verify parse = 4 * 0.15s = ~0.6s of sync work.
    # On a 10ms probe tick we expect ~50 ticks if the loop is free.
    # Blocked, ticks accumulate only after boot_done fires (~0-2).
    # Threshold of 10 leaves generous headroom for Windows 15ms timer
    # resolution.
    assert probe_ticks > 10, (
        f"event loop appears blocked during boot drain: only "
        f"{probe_ticks} probe ticks while ~0.6s of sync parse work was "
        f"in flight (expected dozens with asyncio.to_thread)"
    )


@pytest.mark.asyncio
async def test_run_save_ingest_publishes_save_tail_stopped_on_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-tail-pip: the finally-block emits a
    ``save_tail_stopped`` frame so the FE AppShell pip can flip to
    "Tail unavailable" without waiting for the SSE channel to error
    out 3 times. The channel itself stays healthy while uvicorn does
    (halt-save-tail route case), so this is the only signal the FE
    has that save-tail is truly off."""
    from chronicler.api.events import EventBus

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-tail-pip-stopped"

    received: list[dict] = []
    bus = EventBus()
    monkey_publish = bus.publish

    def _capture(camp_id, event):
        received.append({"campaign_id": camp_id, **event})
        monkey_publish(camp_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        event_bus=bus,
    )

    stopped = [e for e in received if e.get("kind") == "save_tail_stopped"]
    assert len(stopped) == 1, (
        f"expected exactly one save_tail_stopped frame; received "
        f"{[e.get('kind') for e in received]}"
    )
    assert stopped[0]["campaign_id"] == campaign_id
    assert isinstance(stopped[0].get("stopped_at"), str)


# --- ck3_chronicler-91hh: end-of-ingest death-bio catch-up drain ---
@pytest.mark.asyncio
async def test_teardown_drains_for_campaign_before_scheduler_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-91hh: on graceful save-ingest shutdown the loop must
    invoke ``drain_for_campaign`` (death-bio catch-up) BEFORE
    ``scheduler.drain()``.

    Defense-in-depth for l8h2: a death recorded in the DB but somehow
    lacking a scheduled biography is recovered at shutdown instead of
    waiting for the next launch's ``_startup_catchup_scan``. This is a
    wiring test — ``drain_for_campaign`` itself is covered by
    ``test_startup_catchup_reschedules_deaths``; here we only assert the
    teardown invokes it, exactly once, before the scheduler drain.
    """
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque save content")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-91hh"

    snap = SaveSnapshot(
        playthrough_id="uuid-91hh",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    _patch_parse_save(monkeypatch, lambda path: ({}, snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    # Neutralise the STARTUP catch-up scan (xy69), which is itself a thin
    # wrapper around drain_for_campaign — otherwise its call would be
    # indistinguishable from the teardown call we're actually testing.
    monkeypatch.setattr("chronicler.save.ingest._startup_catchup_scan", lambda **_kwargs: None)

    calls: list[str] = []

    def _record_drain_for_campaign(**_kwargs):
        calls.append("drain_for_campaign")
        return None

    monkeypatch.setattr("chronicler.save.ingest.drain_for_campaign", _record_drain_for_campaign)

    captured: dict[str, object] = {}

    def _capture_scheduler(scheduler) -> None:
        async def _recording_drain() -> None:
            calls.append("scheduler.drain")

        # Replace the real drain with a recorder so we observe ordering
        # without running the LLM pipeline during teardown.
        scheduler.drain = _recording_drain  # type: ignore[method-assign]
        captured["scheduler"] = scheduler

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        biography_provider=_FakeProvider(),
        on_scheduler_ready=_capture_scheduler,
    )

    assert captured.get("scheduler") is not None, (
        "a scheduler must be created when a biography_provider is supplied"
    )
    assert calls == ["drain_for_campaign", "scheduler.drain"], (
        f"teardown must call drain_for_campaign before scheduler.drain; observed order: {calls}"
    )
