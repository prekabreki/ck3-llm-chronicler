"""FastAPI application factory for chronicler."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI

from chronicler.api.dependencies import EngineCache
from chronicler.api.events import EventBus
from chronicler.api.routes import register_routes
from chronicler.narrative.provider import NarrativeProvider
from chronicler.narrative.queue_state import NarrativeQueueState


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Manage the EngineCache lifetime + bind the EventBus to this loop.

    The event-loop binding (audit F-06 / ck3_chronicler-xpiu) is what
    makes EventBus.publish thread-safe from worker threads — without
    it, publishes from run_in_executor would call asyncio.Queue
    primitives off-loop.

    ck3_chronicler-u0eu (2026-05-08): also registers the
    narrative-queue → SSE-fanout listener at app startup. Previously
    this was wired exclusively inside ``run_save_ingest``, which
    meant lazy-regen against a sealed campaign (no save-tail loop)
    would queue items but never publish narrative_* SSE frames —
    the queue strip stayed silent and the user had to F5. Wiring
    here lets every queue transition publish to the right per-
    campaign channel via ``QueueItem.campaign_uuid``, regardless of
    whether save-tail is currently running.
    """
    cache: EngineCache = app.state.engine_cache
    bus: EventBus = app.state.event_bus
    queue: NarrativeQueueState = app.state.narrative_queue
    bus.attach_loop(asyncio.get_running_loop())

    # ck3_chronicler-yrv3: attach the in-process log ring buffer to
    # the chronicler root logger so the FE Logs tab can stream output
    # via /api/sse/logs. Idempotent — re-mounts on the same logger
    # are no-ops, so test fixtures that reuse a process don't stack
    # duplicate handlers.
    from chronicler.api.log_buffer import get_log_buffer

    get_log_buffer().attach()

    def _publish_narrative(item) -> None:  # type: ignore[no-untyped-def]
        if item.campaign_uuid is None:
            return
        # Best-effort observability hook — a publish failure must never
        # break the narrative-status listener.
        with suppress(Exception):
            bus.publish(
                item.campaign_uuid,
                {
                    "kind": f"narrative_{item.status}",
                    "item_id": item.item_id,
                    "character_id": item.character_id,
                    "task_kind": item.kind,
                    "duration_ms": item.duration_ms,
                    "error": item.error,
                },
            )

    queue.add_listener(_publish_narrative)

    # ck3_chronicler-txuo: backfill any sealed campaigns that predate
    # the archive-sync feature. Runs fire-and-forget in a worker
    # thread so a slow git push (or no-network cold boot) can't delay
    # FE first paint. Subsequent boots see all snapshots in place and
    # the backfill is a fast scan-and-skip.
    # ck3_chronicler-te8s: same shape — fix Character rows whose
    # dynasty_name is NULL but house_name is populated. ~30% of
    # characters across every campaign hit this; the backfill is one
    # SQL UPDATE per campaign and is idempotent.
    def _run_backfill() -> None:
        import logging as _logging

        _log = _logging.getLogger(__name__)
        # ck3_chronicler-27ov.15 (audit H12): bootstrap+prune of archived
        # snapshots moved here from inside list_campaigns — a GET must
        # never delete files. Runs FIRST so the backfill below (and every
        # later list_campaigns) sees reconciled rows: tombstoned campaigns
        # pruned, newly-pulled archived sidecars inserted.
        # Issue #24: an install that predates the archive-dir move still has
        # its only copy of those campaigns in the old in-tree location. Warn
        # loudly with the exact move command rather than relocating files
        # under the user on boot.
        try:
            from chronicler.sync import warn_if_legacy_archive_unmigrated

            warn_if_legacy_archive_unmigrated()
        except Exception as exc:  # noqa: BLE001 — observability hook
            _log.warning("issue #24: legacy archive-dir check failed: %s", exc)
        try:
            from chronicler.sync import bootstrap_archived_snapshots

            n = bootstrap_archived_snapshots(registry=cache.registry_path)
            if n:
                _log.info(
                    "txuo: startup bootstrapped %d archived campaign "
                    "snapshot(s) from the archive dir into the registry",
                    n,
                )
        except Exception as exc:  # noqa: BLE001 — observability hook
            _log.warning("txuo: startup snapshot bootstrap failed: %s", exc)
        try:
            from chronicler.sync import backfill_archived_campaigns

            n = backfill_archived_campaigns(registry=cache.registry_path)
            if n:
                _log.info(
                    "txuo: startup backfilled %d sealed campaign(s) into the archive dir",
                    n,
                )
        except Exception as exc:  # noqa: BLE001 — observability hook
            _log.warning("txuo: startup backfill failed: %s", exc)
        try:
            from chronicler.db.backfills import (
                backfill_dynasty_name_from_house_name,
            )

            n = backfill_dynasty_name_from_house_name(registry_path=cache.registry_path)
            if n:
                _log.info(
                    "te8s: startup backfilled dynasty_name from house_name "
                    "on %d Character row(s) across all campaigns",
                    n,
                )
        except Exception as exc:  # noqa: BLE001 — observability hook
            _log.warning("te8s: dynasty backfill failed: %s", exc)
        try:
            from chronicler.db.backfills import (
                backfill_last_event_in_game_date,
            )

            n = backfill_last_event_in_game_date(registry_path=cache.registry_path)
            if n:
                _log.info(
                    "9xa6: startup backfilled last_event_in_game_date "
                    "from events.event_date_iso on %d campaign(s)",
                    n,
                )
        except Exception as exc:  # noqa: BLE001 — observability hook
            _log.warning("9xa6: last_event_in_game_date backfill failed: %s", exc)
        try:
            from chronicler.db.backfills import (
                backfill_redecode_character_names,
            )

            n = backfill_redecode_character_names(registry_path=cache.registry_path)
            if n:
                _log.info(
                    "x2qj: startup re-decoded character names with the "
                    "culture-aware Unicode fixup on %d row(s) across all "
                    "campaigns",
                    n,
                )
        except Exception as exc:  # noqa: BLE001 — observability hook
            _log.warning("x2qj: name re-decode backfill failed: %s", exc)

    # ck3_chronicler-g0ku: own the backfill thread's lifecycle. Capture the
    # executor future and join it on shutdown (before dispose_all) so the
    # worker can never outlive the lifespan. Previously fire-and-forget — it
    # relied on the ASGI server implicitly joining the executor at loop
    # teardown, which is fragile and left dispose_all racing the live backfill.
    backfill_future = asyncio.get_running_loop().run_in_executor(None, _run_backfill)

    try:
        yield
    finally:
        # _run_backfill swallows its own exceptions, so this await won't
        # raise; suppress is belt-and-braces against the future being
        # cancelled during an abrupt loop teardown.
        with suppress(Exception):
            await backfill_future
        # Issue #46: release the narrative transport's HTTP client. In the
        # `finally`, so it happens on the exception path too — a leaked
        # connection pool matters most when shutdown was not clean. Errors
        # are suppressed for the same reason dispose_all is unguarded-last:
        # a failure closing a client must not mask the original exception
        # or strand the engine cache.
        provider = getattr(app.state, "narrative_provider", None)
        if provider is not None:
            with suppress(Exception):
                await provider.aclose()
        cache.dispose_all()


def create_app(
    *,
    registry_path: Path | None = None,
    narrative_provider: NarrativeProvider | None = None,
) -> FastAPI:
    """Build the chronicler ASGI app.

    :param registry_path: optional override for the campaigns registry
        location. Defaults to ``~/Documents/chronicler/registry.db``
        when ``None``. Tests pass an in-tmp_path registry for isolation.
    :param narrative_provider: optional provider used by endpoints that
        run LLM generations from the API (e.g. POST /complete in
        ck3_chronicler-z7l). When ``None``, those endpoints respond 503
        with a hint to configure one. Tests inject a FakeProvider here
        rather than reaching for dependency_overrides.
    """
    app = FastAPI(
        title="chronicler",
        description=(
            "HTTP API for the CK3 narrative chronicler. Reads from per-"
            "campaign SQLite DBs populated by the save-parse pipeline."
        ),
        version="0.3.0.dev0",
        lifespan=_lifespan,
    )
    app.state.engine_cache = EngineCache(registry_path=registry_path)
    app.state.narrative_provider = narrative_provider
    # ck3_chronicler-ek2: in-process SSE event bus. Always present so
    # ingest can publish unconditionally; subscribers attach via
    # EventBus.subscribe (consumed by the forthcoming SSE endpoint).
    app.state.event_bus = EventBus()
    # ck3_chronicler-eev: process-wide narrative-queue state. Always
    # present so the snapshot endpoint never 404s; the orchestrator
    # threads this same instance into run_save_ingest so the scheduler's
    # lifecycle transitions are reflected here, and adds an event_bus
    # listener so the SSE channel sees narrative_* frames.
    app.state.narrative_queue = NarrativeQueueState()
    # ck3_chronicler-27ov.78 (audit L13): last llm-pause unpause-drain
    # report, surfaced by GET /settings/llm-pause. Per-app (was a
    # settings module global, which leaked across in-process tests).
    app.state.last_drain_report = None
    register_routes(app)
    return app
