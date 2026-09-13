"""Combined save-tail + web-server orchestrator for the typical user workflow.

``chronicler dev --campaign <name>`` runs both processes in one terminal:

1. The :func:`chronicler.save.ingest.run_save_ingest` async loop watches
   the autosave directory and ingests events into the campaign DB,
   scheduling biographies for tracked-character deaths.
2. uvicorn serves the FastAPI app so the user can browse the campaign
   in a browser concurrent with playing CK3.

Both run as :func:`asyncio.gather`-paired tasks. Ctrl+C (SIGINT) sets
the shared stop event so each can drain cleanly: biography tasks
finish or cancel, the web server stops accepting connections.

Kept deliberately simple — no signal-handling magic beyond what
asyncio.run + uvicorn provide. v0.9 polish can add structured shutdown
metrics if useful.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator
from pathlib import Path

import uvicorn

from chronicler.api import create_app
from chronicler.config import get_ck3_save_dir
from chronicler.narrative import NarrativeProvider
from chronicler.save import DEFAULT_SAVE_PATTERN, run_save_ingest

log = logging.getLogger(__name__)


@contextlib.contextmanager
def absorb_sigterm_reraise() -> Iterator[None]:
    """Issue #7: keep a SIGTERM from killing us before teardown runs.

    ``uvicorn.Server.serve()`` wraps its work in ``capture_signals()``, which
    swaps in uvicorn's own handler for SIGINT/SIGTERM, restores whatever was
    there before on the way out, and then **re-raises the captured signal**
    so the caller sees the behaviour it originally asked for. For SIGINT the
    restored handler raises ``KeyboardInterrupt``, an ordinary exception, so
    every ``finally`` on the way out still runs. For SIGTERM the restored
    handler is ``SIG_DFL`` — the process is terminated on the spot, inside
    ``await server.serve()``.

    That is what orphaned the parse-pool workers. None of the three nested
    teardown paths got to run: ``_run_server``'s finally (set ``stop``),
    ``run_dev``'s finally (cancel ingest tasks), or ``run_save_ingest``'s
    finally (``parse_pool.shutdown``). Measured on Linux 2026-08-13 —
    SIGTERM left all 3 workers alive and reparented to init, while SIGINT on
    the same build reaped them cleanly. It also explains why the j86v
    teardown test passed while the bug was live: it called ``shutdown()``
    in-process and never delivered a signal.

    So we register a handler for SIGTERM *before* uvicorn captures signals.
    uvicorn records ours as the original, restores it, and re-raises into it
    — where it does nothing but note the request, letting the stack unwind
    through every finally.

    Shutdown cannot itself become the hang, because the absorber's scope is
    exactly ``server.serve()``. It restores the previous disposition on the
    way out, so the long part of teardown — ``asyncio.run`` joining the
    default executor, which on a first-save auto-import of a 26MB save took
    ~2 minutes — runs with plain ``SIG_DFL`` back in place and a second
    SIGTERM kills immediately. Covered by
    tests/integration/test_shutdown_signals.py.

    The counter is the narrow guard for signals arriving inside that scope
    (before uvicorn installs its own handler, or between its restore and its
    re-raise): the first is absorbed, a second escalates rather than being
    swallowed again.

    No-op off the main thread — ``signal.signal`` raises there — which keeps
    the embedded/test paths working.
    """
    if not _on_main_thread():
        yield
        return

    seen = 0

    def _handler(signum: int, _frame: object) -> None:
        nonlocal seen
        seen += 1
        if seen == 1:
            log.info("SIGTERM received; draining (send again to exit immediately)")
            return
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.raise_signal(signum)

    previous = signal.signal(signal.SIGTERM, _handler)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGTERM, previous)


def _on_main_thread() -> bool:
    import threading

    return threading.current_thread() is threading.main_thread()


async def run_dev(
    *,
    campaign_id: str | None = None,
    db_path: Path | None = None,
    save_dir: Path | None = None,
    pattern: str | tuple[str, ...] | list[str] = DEFAULT_SAVE_PATTERN,
    host: str = "127.0.0.1",
    port: int = 8000,
    biography_provider: NarrativeProvider | None = None,
    registry_path: Path | None = None,
) -> None:
    """Run uvicorn (always) + save-ingest (when a campaign is bound).

    ck3_chronicler-nji: ``campaign_id`` is now optional. When None, the
    orchestrator boots uvicorn alone — the user adopts a save through
    the Library page's '+ Adopt save' button (POST /api/campaigns/
    adopt-from-save), and the new endpoint fires
    ``app.state.spawn_save_ingest_for_campaign`` which lazily spawns
    the per-campaign ingest loop on the running event loop.

    When ``campaign_id`` IS supplied at startup, the orchestrator
    immediately spawns save_ingest for it (the legacy path).
    """
    save_dir = save_dir or get_ck3_save_dir()
    stop = asyncio.Event()

    # Construct the app once so we can capture its EventBus and hand it
    # to run_save_ingest — otherwise every event_bus.publish(...) inside
    # the ingest loop is a silent no-op and the SSE pipeline at
    # /api/sse/ingest/{name} never sees save-tail events. (F001 / v5x.)
    app = create_app(registry_path=registry_path, narrative_provider=biography_provider)
    # ck3_chronicler-72a: surface the stop event to the migrate route so
    # it can ask save-tail to drain before running schema upgrades.
    app.state.save_tail_stop = stop
    event_bus = app.state.event_bus
    narrative_queue = app.state.narrative_queue

    # ck3_chronicler-eev → ck3_chronicler-u0eu (2026-05-08):
    # The narrative_queue → SSE-fanout listener moved to app.py's
    # lifespan handler so it's registered at startup, not deferred
    # to the first save_ingest call. QueueItem.campaign_uuid carries
    # the owning campaign explicitly, so the orchestrator no longer
    # needs an ambient ``bound_campaign_for_narrative`` shadow var.

    # Web server task
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        # ck3_chronicler-7p2: pass log_config=None so uvicorn skips its
        # own dictConfig() during serve(). uvicorn's default LOGGING_CONFIG
        # silently overrode chronicler.logging_setup's per-module
        # configuration, leaving chronicler.save.* loggers at WARNING and
        # making the ingest pipeline an observability black box during
        # the cqo smoke session 2026-05-04. Uvicorn's own startup lines
        # ("Started server process", etc.) still flow because uvicorn.error
        # propagates to the root logger we configured at INFO.
        log_config=None,
        # disable uvicorn's signal handling so asyncio's KeyboardInterrupt
        # propagates to BOTH tasks rather than just terminating uvicorn
        # while save-tail keeps running orphaned.
        lifespan="on",
        # ck3_chronicler-dsjo: belt-and-braces. uvicorn's default
        # timeout_keep_alive=5s matches the SSE heartbeat cadence
        # exactly — every quiet SSE stream was one race-loss away from
        # a server-side disconnect. The other dsjo fix (replacing the
        # cancellation-unsafe iterator pattern in ingest_stream.py)
        # is the load-bearing one; this matches cli/main.py's
        # cmd_serve config so all chronicler entrypoints behave alike.
        timeout_keep_alive=120,
    )
    server = uvicorn.Server(config)

    async def _run_server() -> None:
        try:
            await server.serve()
        finally:
            log.info("uvicorn stopped; signalling save-tail to drain")
            stop.set()

    # ck3_chronicler-m4cn: per-campaign schedulers stored in a dict so
    # the adopt-from-save flow (which already supports N concurrent
    # campaigns via ingest_tasks below) can route regenerate-biography
    # requests to the right campaign's live scheduler. The previous
    # single-slot design lost campaign A's scheduler reference as soon
    # as campaign B was adopted, even though A's save-tail task was
    # still running — readers in characters.py and migrate.py then
    # built phantom shadow schedulers that didn't share per-character
    # locks with the live one.
    app.state.narrative_schedulers: dict[str, object] = {}

    # ck3_chronicler-nji: track running per-campaign ingest tasks so
    # adopt-from-save can spawn one without dup-spawning if the
    # campaign already has a loop running.
    ingest_tasks: dict[str, asyncio.Task] = {}

    def _spawn_save_ingest_for_campaign(camp_id: str, camp_db_path: Path) -> None:
        """Lazily spawn a save_ingest task for ``camp_id``. Idempotent
        — re-calling for a campaign that's already being tailed is a
        no-op. Surfaced on app.state for the adopt-from-save endpoint."""
        if camp_id in ingest_tasks and not ingest_tasks[camp_id].done():
            log.debug("save_ingest already running for campaign %s; skip", camp_id)
            return
        log.info("spawning save_ingest task for campaign %s", camp_id)
        loop = asyncio.get_running_loop()

        def _on_scheduler_ready(sched) -> None:  # type: ignore[no-untyped-def]
            # Per-spawn closure binds the campaign id at the call site so
            # concurrent adopts don't trample each other's schedulers.
            app.state.narrative_schedulers[camp_id] = sched

        def _on_foreign_playthrough(other_camp_id: str, other_db_path: Path) -> None:
            # ck3_chronicler-3v0s + bpg8: a save belonging to another known
            # campaign just landed on THIS campaign's tail — the user has
            # started playing it. Switch the tail to it (single active
            # consumer): spawn the other campaign's ingest, then cancel this
            # now-superseded one so we don't keep two consumers rakaly-parsing
            # every save (the old one only to drop it as foreign — the ~2x
            # catch-up cost seen in the v0.12 smoke).
            #
            # Scoped to THIS path on purpose: an explicit two-campaign adopt
            # (calling the spawner directly) still keeps both live so each
            # keeps its own scheduler (ck3_chronicler-m4cn).
            def _do_switch() -> None:
                # ck3_chronicler-27ov.2 (audit H4): defence-in-depth against a
                # self-cancel. The notifier already excludes self-matches, but
                # if one ever slips through, cancelling our own task here would
                # wedge this campaign's ingest until restart.
                if other_camp_id == camp_id:
                    return
                _spawn_save_ingest_for_campaign(other_camp_id, other_db_path)
                this_task = ingest_tasks.get(camp_id)
                if this_task is not None and not this_task.done():
                    log.info(
                        "switching save_ingest: campaign %s superseded by %s",
                        camp_id,
                        other_camp_id,
                    )
                    ingest_tasks.pop(camp_id, None)
                    app.state.narrative_schedulers.pop(camp_id, None)
                    this_task.cancel()

            # ck3_chronicler-27ov.21 (audit M-I2): this callback fires from a
            # worker thread (the to_thread'd _advance_baseline — audit H7),
            # where asyncio.get_running_loop() raises and create_task/
            # Task.cancel() aren't thread-safe. Marshal the whole switch onto
            # the loop. call_soon_threadsafe is safe from any thread, including
            # the loop thread itself (the adopt-endpoint / steady-state paths).
            loop.call_soon_threadsafe(_do_switch)

        task = loop.create_task(
            run_save_ingest(
                save_dir=save_dir,
                db_path=camp_db_path,
                campaign_id=camp_id,
                registry_path=registry_path,
                pattern=pattern,
                biography_provider=biography_provider,
                stop_event=stop,
                event_bus=event_bus,
                narrative_queue=narrative_queue,
                on_scheduler_ready=_on_scheduler_ready,
                on_foreign_playthrough=_on_foreign_playthrough,
            )
        )

        def _on_done(t: asyncio.Task) -> None:
            # ck3_chronicler 2026-05-09: surface task exceptions loudly.
            try:
                exc = t.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                exc = None
            if exc is not None:
                log.error(
                    "save_ingest task for campaign %s crashed: %s",
                    camp_id,
                    exc,
                    exc_info=exc,
                )
            else:
                log.info("save_ingest task for campaign %s completed", camp_id)
            ingest_tasks.pop(camp_id, None)
            # Pop only this campaign's scheduler — earlier code blanket-
            # nulled the singleton slot which corrupted other live
            # schedulers' reachability.
            app.state.narrative_schedulers.pop(camp_id, None)

        task.add_done_callback(_on_done)
        ingest_tasks[camp_id] = task

    app.state.spawn_save_ingest_for_campaign = _spawn_save_ingest_for_campaign

    # Pre-bind the startup campaign if one was supplied — preserves the
    # legacy `chronicler dev --campaign foo` flow where save_ingest
    # runs from the moment uvicorn comes up.
    if campaign_id is not None and db_path is not None:
        _spawn_save_ingest_for_campaign(campaign_id, db_path)
        # Yield once so the spawned task actually starts before
        # uvicorn's serve loop takes over. Without this the test
        # harness's no-op fake_serve() can return before save_ingest
        # gets a single tick of its coroutine — and any side effects
        # (event_bus subscription, scheduler registration) wouldn't fire.
        await asyncio.sleep(0)

    log.info(
        "chronicler dev: serving on http://%s:%d, watching saves at %s (campaign=%s)",
        host,
        port,
        save_dir,
        campaign_id or "<unbound — adopt via Library>",
    )
    try:
        # Issue #7: must wrap server.serve(), not sit inside it — uvicorn
        # re-raises the captured SIGTERM as it leaves capture_signals(), and
        # the handler this installs is the one it restores and raises into.
        with absorb_sigterm_reraise():
            await _run_server()
    finally:
        # Cancel any per-campaign ingest tasks still running so the
        # process exits cleanly. The save_ingest loop honors `stop`
        # internally, but cancel as belt-and-suspenders.
        for _camp_id, task in list(ingest_tasks.items()):
            if not task.done():
                task.cancel()
        if ingest_tasks:
            await asyncio.gather(*ingest_tasks.values(), return_exceptions=True)


def make_default_provider() -> NarrativeProvider:
    """Mirror chronicler.cli.main's provider construction.

    ck3_chronicler-tbrm.1: returns a ClaudeCodeProvider. Function
    signature kept for back-compat with any caller; use
    :func:`chronicler.narrative.make_narrative_provider` directly in
    new code (same result).
    """
    from chronicler.narrative import make_narrative_provider

    return make_narrative_provider()
