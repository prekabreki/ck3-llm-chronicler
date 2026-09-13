"""End-to-end tests for the chronicler dev composition.

ck3_chronicler-v5x / F001: orchestrator.run_dev was constructing the
FastAPI app (which initialises ``app.state.event_bus``) but never
plumbing that bus into ``run_save_ingest``. Every event_bus.publish(...)
inside the ingest loop silently no-op'd, leaving the SSE pipeline at
``/api/sse/ingest/{name}`` blind to save-tail events.

Two layered tests live here:

1. :func:`test_run_dev_plumbs_event_bus_into_run_save_ingest` — the
   surgical wire-up regression for F001. Patches uvicorn + run_save_ingest
   at the orchestrator boundary and asserts the bus passed in is the
   same instance ``app.state.event_bus`` exposes.

2. :func:`test_save_ingest_publishes_event_ingested_and_cache_state` —
   the dwe end-to-end coverage. Drives ``process_save_pair`` and a real
   cache_state publish through the actual EventBus from
   ``app.state.event_bus``, and asserts both frame kinds reach a
   subscriber. We subscribe via ``EventBus.subscribe`` directly rather
   than httpx ASGI streaming because the latter is finicky on Windows
   event loops (see test_ingest_stream.py docstring); the SSE HTTP
   wrapping is already covered by that file's unit tests, so the
   integration concern here is the bus → subscriber flow.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chronicler import orchestrator
from chronicler.api import create_app
from chronicler.api.events import EventBus
from chronicler.db import (
    Base,
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.engine import session_scope
from chronicler.db.registry import create_campaign
from chronicler.save.cache import SaveCache, cache_dir_for
from chronicler.save.ingest import process_save_pair
from chronicler.save.parse import (
    CharacterSnapshot,
    SaveSnapshot,
)
from tests.helpers.snapshots import make_char


@pytest.mark.asyncio
async def test_run_dev_plumbs_event_bus_into_run_save_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    real_create_app = orchestrator.create_app

    def _capturing_create_app(**kwargs: Any) -> Any:
        app = real_create_app(**kwargs)
        captured["app_bus"] = app.state.event_bus
        return app

    async def _fake_run_save_ingest(**kwargs: Any) -> None:
        captured["ingest_kwargs"] = kwargs

    class _FakeServer:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.should_exit = False

        async def serve(self) -> None:
            return

    monkeypatch.setattr(orchestrator, "create_app", _capturing_create_app)
    monkeypatch.setattr(orchestrator, "run_save_ingest", _fake_run_save_ingest)
    monkeypatch.setattr(orchestrator.uvicorn, "Server", _FakeServer)

    await asyncio.wait_for(
        orchestrator.run_dev(
            campaign_id="test-camp",
            db_path=tmp_path / "campaign.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        ),
        timeout=5.0,
    )

    app_bus = captured["app_bus"]
    ingest_kwargs = captured["ingest_kwargs"]

    assert isinstance(app_bus, EventBus)
    assert ingest_kwargs.get("event_bus") is app_bus, (
        "run_save_ingest must receive the same EventBus instance that the "
        "FastAPI app exposes on app.state.event_bus — see F001 / v5x."
    )


@pytest.mark.asyncio
async def test_run_dev_disables_uvicorn_log_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ck3_chronicler-7p2 regression: uvicorn.Config must be constructed
    with ``log_config=None`` so uvicorn's own ``dictConfig`` doesn't
    clobber the chronicler.* logger configuration set up by
    ``chronicler.logging_setup.setup_logging``.

    Without this, chronicler.save.* INFO/DEBUG logs were silently dropped
    during the cqo smoke session 2026-05-04, hiding what the ingest
    pipeline was doing and making issue ck3_chronicler-pxk impossible
    to triage live.
    """
    captured_config_kwargs: dict[str, Any] = {}

    real_config_cls = orchestrator.uvicorn.Config

    def _capturing_config(*args: Any, **kwargs: Any) -> Any:
        captured_config_kwargs.update(kwargs)
        return real_config_cls(*args, **kwargs)

    async def _fake_run_save_ingest(**_kwargs: Any) -> None:
        return

    class _FakeServer:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.should_exit = False

        async def serve(self) -> None:
            return

    monkeypatch.setattr(orchestrator.uvicorn, "Config", _capturing_config)
    monkeypatch.setattr(orchestrator.uvicorn, "Server", _FakeServer)
    monkeypatch.setattr(orchestrator, "run_save_ingest", _fake_run_save_ingest)

    await asyncio.wait_for(
        orchestrator.run_dev(
            campaign_id="test-camp",
            db_path=tmp_path / "campaign.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        ),
        timeout=5.0,
    )

    assert "log_config" in captured_config_kwargs, (
        "uvicorn.Config must be constructed with an explicit log_config "
        "kwarg to opt out of uvicorn's default LOGGING_CONFIG."
    )
    assert captured_config_kwargs["log_config"] is None, (
        "log_config must be None so uvicorn skips dictConfig() during "
        "serve() — otherwise it overrides chronicler's logger config "
        "and silences chronicler.save.* INFO/DEBUG output. See 7p2."
    )


# --- dwe: end-to-end SSE pipeline ---


def _char(cid: int, *, is_dead: bool = False, death_date: str | None = None) -> CharacterSnapshot:
    return make_char(cid, is_dead=is_dead, death_date=death_date)


def _snap(chars: dict[int, CharacterSnapshot], *, date: str = "1067.2.1") -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=list(chars.keys())[0] if chars else None,
        characters=chars,
    )


@pytest.fixture
def fixture_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Any, str, Any]]:
    """Yield ``(app, campaign_id, factory)`` for the dwe end-to-end test.

    Sets CHRONICLER_DATA_DIR to tmp_path so SaveCache resolves into the
    temp tree. Creates a campaign in the registry, builds its DB schema,
    and spins up a FastAPI app pointing at the same registry. Tears
    everything down on exit.
    """
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    registry = tmp_path / "registry.db"
    campaign_db = tmp_path / "campaigns" / "dwe.db"
    campaign_db.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine_for_path(campaign_db)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    campaign = create_campaign("dwe", db_path=str(campaign_db), registry=registry)
    app = create_app(registry_path=registry)
    try:
        yield app, campaign.id, factory
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_save_ingest_publishes_event_ingested_and_cache_state(
    fixture_campaign: tuple[Any, str, Any],
    tmp_path: Path,
) -> None:
    """End-to-end (dwe / F016): a save-pair processed against the bus
    that ``app.state.event_bus`` exposes delivers an ``event_ingested``
    frame to a subscriber, and a ``cache_state`` snapshot published the
    same way the ingest loop's ``_publish_cache_state`` helper would
    also reaches the subscriber.

    This would have failed before the F001 fix because the bus the
    ingest path published to was a local default instead of the app's
    EventBus instance — subscribers attached via the SSE endpoint
    would never see the frames. Today both wires share one bus.
    """
    app, campaign_id, factory = fixture_campaign
    bus: EventBus = app.state.event_bus

    # Subscribe first so we don't race the publish. Drain frames into
    # a list as they arrive; the subscriber generator is driven by an
    # asyncio task and the test stops it once it's seen what it needs.
    received: list[dict[str, Any]] = []
    stop = asyncio.Event()

    async def _drain() -> None:
        async for frame in bus.subscribe(campaign_id, stop_event=stop):
            received.append(frame)
            kinds = {f.get("kind") for f in received}
            if {"event_ingested", "cache_state"}.issubset(kinds):
                stop.set()
                return

    drain_task = asyncio.create_task(_drain())

    # The subscribe() body registers its queue lazily on the first
    # iteration. Wait until the bus reports a subscriber before
    # publishing — otherwise the publish lands with no queues and the
    # event is silently dropped (publish() is best-effort by design).
    for _ in range(40):
        if bus.subscriber_count(campaign_id) >= 1:
            break
        await asyncio.sleep(0.05)
    assert bus.subscriber_count(campaign_id) == 1, "subscriber failed to register"

    # 1) Drive a real ingest through process_save_pair with the app's
    #    EventBus. This exercises the F001 wire: _ingest_diff_event ->
    #    event_bus.publish(bus_campaign_id, {"kind": "event_ingested", ...}).
    prev = _snap({4242: _char(4242, is_dead=False)})
    curr = _snap({4242: _char(4242, is_dead=True, death_date="1067.1.15")})
    with session_scope(factory) as session:
        results = process_save_pair(
            prev,
            curr,
            session=session,
            event_bus=bus,
            bus_campaign_id=campaign_id,
        )
    assert results and results[0].outcome == "ingested"

    # 2) Mirror what run_save_ingest's _publish_cache_state helper does
    #    on each watcher tick — emit a cache_state frame derived from
    #    a real SaveCache snapshot.
    cache = SaveCache(cache_dir_for(tmp_path, campaign_id))
    bus.publish(campaign_id, {"kind": "cache_state", **cache.snapshot()})

    # Give the drain task up to a couple of seconds to pull both frames
    # off its queue; the subscribe() loop wakes on a 1s interval.
    try:
        await asyncio.wait_for(drain_task, timeout=3.0)
    except TimeoutError:
        stop.set()
        await drain_task

    kinds = [f.get("kind") for f in received]
    assert "event_ingested" in kinds, f"missing event_ingested frame; got {kinds}"
    assert "cache_state" in kinds, f"missing cache_state frame; got {kinds}"

    ingested_frame = next(f for f in received if f.get("kind") == "event_ingested")
    assert ingested_frame.get("event_type") == "death"
    assert ingested_frame.get("character_id") == 4242


# --- audit F-07 / ck3_chronicler-46lq: orchestrator spawner tests -----------
#
# `_spawn_save_ingest_for_campaign` is a closure inside run_dev. The
# tests below drive run_dev far enough that the closure is reachable
# via app.state.spawn_save_ingest_for_campaign, then poke it directly.
#
# Pattern: a FakeServer.serve() blocks on a per-test stop event; the
# test starts run_dev as a task, exercises the spawner, releases the
# stop event, and awaits the cleanup phase.


class _ControllableFakeServer:
    """Fake uvicorn.Server whose serve() blocks until stop is set.

    Keeps run_dev pinned in its serve() phase so the test can interact
    with app.state.spawn_save_ingest_for_campaign before the cleanup
    block tears down ingest_tasks.
    """

    instances: list[_ControllableFakeServer] = []

    def __init__(self, config: Any) -> None:
        self.config = config
        self.should_exit = False
        self.stop_event = asyncio.Event()
        type(self).instances.append(self)

    async def serve(self) -> None:
        await self.stop_event.wait()


@pytest.fixture
def fake_server_instances() -> Iterator[list[_ControllableFakeServer]]:
    _ControllableFakeServer.instances = []
    yield _ControllableFakeServer.instances
    _ControllableFakeServer.instances = []


def _patch_orchestrator_for_spawn_tests(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_run_save_ingest=None,
):
    """Patch orchestrator's external boundaries so run_dev can run
    inside a unit test. Returns a captured-app dict the test can read."""
    captured: dict[str, Any] = {}
    real_create_app = orchestrator.create_app

    def _capturing_create_app(**kwargs: Any) -> Any:
        app = real_create_app(**kwargs)
        captured["app"] = app
        return app

    async def _default_run_save_ingest(**kwargs: Any) -> None:
        # Default: never returns until cancelled. Mirrors a healthy
        # save-tail loop awaiting its stop_event.
        if on_run_save_ingest is not None:
            await on_run_save_ingest(kwargs)
        else:
            await asyncio.Event().wait()

    monkeypatch.setattr(orchestrator, "create_app", _capturing_create_app)
    monkeypatch.setattr(orchestrator, "run_save_ingest", _default_run_save_ingest)
    monkeypatch.setattr(orchestrator.uvicorn, "Server", _ControllableFakeServer)
    return captured


@pytest.mark.asyncio
async def test_spawn_save_ingest_idempotent_on_double_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """audit F-07.a: re-spawning for a campaign that already has a
    running task is a no-op. Without this, adopt-from-save → manual
    re-adopt would leak parallel save-tail loops on the same DB."""
    spawn_calls: list[str] = []

    async def _track(kwargs: dict[str, Any]) -> None:
        spawn_calls.append(kwargs["campaign_id"])
        await asyncio.Event().wait()

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_track)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="test-camp-A",
            db_path=tmp_path / "a.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )

    # Wait until the FakeServer instance materialises (its construction
    # is the last thing run_dev does before _run_server).
    for _ in range(50):
        if fake_server_instances and "app" in captured:
            break
        await asyncio.sleep(0.02)
    assert fake_server_instances, "FakeServer never instantiated"
    assert "app" in captured

    # Initial spawn from campaign_id → 1 call.
    assert spawn_calls == ["test-camp-A"]

    # Re-spawn for the same campaign should be a no-op.
    captured["app"].state.spawn_save_ingest_for_campaign("test-camp-A", tmp_path / "a.db")
    await asyncio.sleep(0.05)
    assert spawn_calls == ["test-camp-A"]

    # Spawning a different campaign opens a second task.
    captured["app"].state.spawn_save_ingest_for_campaign("test-camp-B", tmp_path / "b.db")
    await asyncio.sleep(0.05)
    assert spawn_calls == ["test-camp-A", "test-camp-B"]

    # Release the server so run_dev's cleanup runs and cancels the tasks.
    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)


@pytest.mark.asyncio
async def test_foreign_playthrough_switches_to_single_active_consumer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """ck3_chronicler-bpg8: when a save for another known campaign lands on
    the current tail (the user started playing it), switch — spawn that
    campaign's consumer and cancel this one, so exactly one consumer runs
    instead of both rakaly-parsing every save (~2x catch-up cost). Scoped to
    the foreign-playthrough path; explicit two-campaign adopt stays dual (m4cn,
    covered by test_two_adopted_campaigns_keep_independent_schedulers)."""
    started: list[str] = []
    cancelled: list[str] = []
    foreign_cbs: dict[str, Any] = {}

    async def _track(kwargs: dict[str, Any]) -> None:
        cid = kwargs["campaign_id"]
        started.append(cid)
        foreign_cbs[cid] = kwargs["on_foreign_playthrough"]
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(cid)
            raise

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_track)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="camp-A",
            db_path=tmp_path / "a.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )
    for _ in range(50):
        if fake_server_instances and "app" in captured and "camp-A" in foreign_cbs:
            break
        await asyncio.sleep(0.02)
    assert started == ["camp-A"]

    # A foreign save for camp-B lands on camp-A's tail → switch.
    foreign_cbs["camp-A"]("camp-B", tmp_path / "b.db")
    await asyncio.sleep(0.05)
    assert started == ["camp-A", "camp-B"]
    assert cancelled == ["camp-A"]
    assert "camp-A" not in captured["app"].state.narrative_schedulers

    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)


@pytest.mark.asyncio
async def test_foreign_playthrough_switch_works_when_callback_fires_off_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """ck3_chronicler-27ov.21 (audit M-I2): the on_foreign_playthrough callback
    fires from a WORKER THREAD — _advance_baseline (which calls it) now runs via
    asyncio.to_thread (audit H7). The orchestrator's spawn+cancel must be
    marshalled onto the loop; calling asyncio.get_running_loop() / Task.cancel()
    on a worker thread used to raise (swallowed upstream), so the auto-resume
    feature silently no-op'd in this context."""
    started: list[str] = []
    cancelled: list[str] = []
    foreign_cbs: dict[str, Any] = {}

    async def _track(kwargs: dict[str, Any]) -> None:
        cid = kwargs["campaign_id"]
        started.append(cid)
        foreign_cbs[cid] = kwargs["on_foreign_playthrough"]
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(cid)
            raise

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_track)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="camp-A",
            db_path=tmp_path / "a.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )
    for _ in range(50):
        if fake_server_instances and "app" in captured and "camp-A" in foreign_cbs:
            break
        await asyncio.sleep(0.02)
    assert started == ["camp-A"]

    # Fire the callback from a worker thread, exactly as the to_thread'd
    # _advance_baseline does in production.
    def _invoke_from_worker() -> None:
        foreign_cbs["camp-A"]("camp-B", tmp_path / "b.db")

    # Pre-fix: get_running_loop() raises on the worker thread. Swallow so the
    # assertion below reports the real failure (switch didn't happen).
    with contextlib.suppress(RuntimeError):
        await asyncio.to_thread(_invoke_from_worker)
    await asyncio.sleep(0.05)
    assert started == ["camp-A", "camp-B"], (
        "foreign switch must work when the callback fires off the loop (M-I2)"
    )
    assert cancelled == ["camp-A"]

    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)


@pytest.mark.asyncio
async def test_spawn_save_ingest_publishes_scheduler_to_app_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """audit F-07.b: the on_scheduler_ready callback the spawner passes
    into run_save_ingest must populate
    app.state.narrative_schedulers[campaign_id]. Without it, POST
    /characters/{id}/biography/regenerate has nowhere to enqueue work
    and 503s.

    ck3_chronicler-m4cn: per-campaign dict instead of a single slot."""
    sentinel_scheduler = object()

    async def _emit_scheduler(kwargs: dict[str, Any]) -> None:
        kwargs["on_scheduler_ready"](sentinel_scheduler)
        await asyncio.Event().wait()

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_emit_scheduler)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="test-camp",
            db_path=tmp_path / "c.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )

    for _ in range(50):
        if fake_server_instances and "app" in captured:
            break
        await asyncio.sleep(0.02)

    # Wait for the scheduler ready callback to land.
    for _ in range(50):
        schedulers = getattr(captured["app"].state, "narrative_schedulers", {})
        if schedulers.get("test-camp") is sentinel_scheduler:
            break
        await asyncio.sleep(0.02)
    assert captured["app"].state.narrative_schedulers["test-camp"] is sentinel_scheduler

    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)


@pytest.mark.asyncio
async def test_spawn_save_ingest_cleans_up_on_task_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """audit F-07.c: when a save_ingest task completes naturally, the
    spawner's done-callback must pop the campaign's slot from
    app.state.narrative_schedulers so a subsequent re-spawn isn't
    shadowed by a stale value.

    ck3_chronicler-m4cn: per-campaign dict; pop just this campaign's
    entry instead of nulling the singleton."""
    completion_gate = asyncio.Event()
    sentinel_scheduler = object()

    async def _short_lived(kwargs: dict[str, Any]) -> None:
        kwargs["on_scheduler_ready"](sentinel_scheduler)
        # Wait for the test to release us, then return cleanly (not
        # cancelled) so the done-callback runs the natural-completion
        # branch.
        await completion_gate.wait()

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_short_lived)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="test-camp",
            db_path=tmp_path / "d.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )

    for _ in range(50):
        if (
            fake_server_instances
            and "app" in captured
            and getattr(captured["app"].state, "narrative_schedulers", {}).get("test-camp")
            is sentinel_scheduler
        ):
            break
        await asyncio.sleep(0.02)
    assert captured["app"].state.narrative_schedulers["test-camp"] is sentinel_scheduler

    # Let the save_ingest task complete naturally.
    completion_gate.set()
    for _ in range(50):
        if "test-camp" not in captured["app"].state.narrative_schedulers:
            break
        await asyncio.sleep(0.02)
    assert "test-camp" not in captured["app"].state.narrative_schedulers

    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)


@pytest.mark.asyncio
async def test_two_adopted_campaigns_keep_independent_schedulers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """ck3_chronicler-m4cn: the previous single-slot
    app.state.narrative_scheduler lost campaign A's reference the
    instant campaign B was adopted. The dict-keyed storage keeps both
    live and lookable up by campaign_id.

    Without this, regenerate-biography on the first-adopted campaign
    fell through to the lazy-construction path, building a *new*
    scheduler that didn't share per-character locks with the live one
    — racing the save-tail biography insert."""
    sched_a = object()
    sched_b = object()

    async def _hold(kwargs: dict[str, Any]) -> None:
        if kwargs["campaign_id"] == "camp-A":
            kwargs["on_scheduler_ready"](sched_a)
        elif kwargs["campaign_id"] == "camp-B":
            kwargs["on_scheduler_ready"](sched_b)
        await asyncio.Event().wait()

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_hold)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="camp-A",
            db_path=tmp_path / "a.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )

    for _ in range(50):
        if fake_server_instances and "app" in captured:
            break
        await asyncio.sleep(0.02)

    # camp-A's spawn is triggered by the startup arg.
    for _ in range(50):
        scheds = getattr(captured["app"].state, "narrative_schedulers", {})
        if scheds.get("camp-A") is sched_a:
            break
        await asyncio.sleep(0.02)

    # Now lazily adopt camp-B mid-flight.
    captured["app"].state.spawn_save_ingest_for_campaign("camp-B", tmp_path / "b.db")

    for _ in range(50):
        scheds = captured["app"].state.narrative_schedulers
        if scheds.get("camp-A") is sched_a and scheds.get("camp-B") is sched_b:
            break
        await asyncio.sleep(0.02)

    scheds = captured["app"].state.narrative_schedulers
    assert scheds["camp-A"] is sched_a
    assert scheds["camp-B"] is sched_b

    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)


@pytest.mark.asyncio
async def test_spawn_save_ingest_cancels_in_flight_tasks_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_server_instances: list[_ControllableFakeServer],
) -> None:
    """audit F-07.d: when the server stops, the orchestrator must
    cancel all running save_ingest tasks. Without this, the process
    would hang on shutdown waiting for orphaned ingest loops."""
    cancelled_count = 0

    async def _await_cancel(_kwargs: dict[str, Any]) -> None:
        nonlocal cancelled_count
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled_count += 1
            raise

    captured = _patch_orchestrator_for_spawn_tests(monkeypatch, on_run_save_ingest=_await_cancel)

    run_task = asyncio.create_task(
        orchestrator.run_dev(
            campaign_id="test-camp-X",
            db_path=tmp_path / "x.db",
            save_dir=tmp_path / "saves",
            registry_path=tmp_path / "registry.db",
        )
    )

    for _ in range(50):
        if fake_server_instances and "app" in captured:
            break
        await asyncio.sleep(0.02)

    # Spawn a second campaign so cleanup has something to cancel beyond
    # the campaign_id-driven first spawn.
    captured["app"].state.spawn_save_ingest_for_campaign("test-camp-Y", tmp_path / "y.db")
    await asyncio.sleep(0.05)

    # Release server -> orchestrator's finally: cancels both ingest tasks.
    fake_server_instances[0].stop_event.set()
    await asyncio.wait_for(run_task, timeout=5.0)
    assert cancelled_count == 2
