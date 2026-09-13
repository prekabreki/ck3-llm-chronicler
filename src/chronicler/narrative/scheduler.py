"""Narrative scheduling for the live ingest loop.

When the tailer ingests a death event for a tracked character, the
orchestrator (V02-N03) needs to run as a background task so it never
blocks the next debug.log line. Both kinds of background LLM work share the
same single-lane semaphore (a 14B local model can only generate one
response at a time).

:class:`NarrativeScheduler` owns:

- a ref to the per-campaign session **factory** (sessions for narrative
  generation are created fresh, separate from the ingest session, and
  released between read/LLM/write phases — see pipeline.generate_biography)
- a long-lived :class:`NarrativeProvider` (the httpx client lives across
  calls so we don't open/close per generation)
- a global :class:`asyncio.Semaphore` (ck3_chronicler-6mek: default size
  2, raised to 4 in ck3_chronicler-w2se) so concurrent death events don't
  spawn unbounded parallel LLM tasks. Was 1 in the local-Ollama era — 14B
  can only run one generation at a time on a single GPU, so anything
  beyond max=1 just created queue overhead. After the tbrm pivot to
  hosted Claude Code each ``claude --print`` is its own Node.js process
  and Anthropic rate-limits the calls, so we can run a small fan-out
  (2-4) without contention. Issue #46: the width now comes from the
  provider's :attr:`~chronicler.narrative.provider.NarrativeProvider.max_concurrent`
  (4 for the Anthropic-family transports, 1 for the local
  openai-compatible presets — one Ollama/LM Studio server shares one GPU,
  so a 4-wide fan-out thrashes it). Pass ``max_concurrent`` explicitly to
  override the provider's own number; that is what the tests do.
- a per-character :class:`asyncio.Lock` so a duplicate death event
  during testing doesn't kick off two concurrent generations for the
  same char (race against version=1 / version=2 inserts).
- a **tracked-character filter** (``is_tracked`` callable). Default is
  "track everything" — the user explicitly opts in characters they care
  about via ``chronicler track <id>`` or
  :func:`chronicler.db.registry.add_tracked_character`. This is the
  v0.2 throttle for the "drowning in 1,144 NPC deaths per game-year"
  problem; v0.4's ``chronicler_is_relevant`` will gate at the mod-side
  emit for ingest volume, but the scheduler filter is what protects
  the LLM from being asked to write biographies for every peasant.

The scheduler is opt-in: ``run_ingest`` only constructs one when a
provider is supplied. ``chronicler tail --no-biography`` is the
diagnostic escape hatch (passes ``provider=None`` so scheduler is None,
no narrative generated). All exceptions inside narrative tasks are
logged and swallowed so the ingest loop is unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from chronicler.narrative.pipeline import generate_biography
from chronicler.narrative.provider import NarrativeProvider
from chronicler.narrative.queue_state import NarrativeQueueState

log = logging.getLogger(__name__)


class _SessionFactory(Protocol):
    """Structural type for a SQLAlchemy ``sessionmaker``-like callable."""

    def __call__(self): ...


def _allow_all(_character_id: int) -> bool:
    return True


class NarrativeScheduler:
    """Schedules biography tasks for the ingest loop's lifetime."""

    def __init__(
        self,
        factory: _SessionFactory,
        provider: NarrativeProvider,
        *,
        campaign_uuid: str | None = None,
        is_tracked: Callable[[int], bool] | None = None,
        is_paused: Callable[[int], bool] | None = None,
        is_globally_paused: Callable[[], bool] | None = None,
        get_bumped_at: Callable[[int], str | None] | None = None,
        max_concurrent: int | None = None,
        queue_state: NarrativeQueueState | None = None,
    ) -> None:
        self._factory = factory
        self._provider = provider
        # ck3_chronicler-tbrm.1: per-campaign UUID forwarded to the
        # ClaudeCodeProvider via NarrativeRequest.metadata so briefings
        # land at briefings/<uuid>/<char>-vN.md. None falls back to
        # the literal "_unscoped" bucket.
        self._campaign_uuid = campaign_uuid
        # Default is permissive (back-compat with existing tests). Production
        # callers in run_ingest pass a real callable backed by the tracked
        # characters set.
        self._is_tracked = is_tracked if is_tracked is not None else _allow_all
        # ck3_chronicler-t2v5: vysp.10 added paused_at + bumped_at on
        # tracked_characters. Paused chars are skipped at schedule time;
        # bumped chars jump the deferred-drain order. Both callables
        # default to back-compat no-ops so tests + non-production callers
        # don't have to wire them.
        self._is_paused = is_paused if is_paused is not None else (lambda _cid: False)
        # ck3_chronicler-gx7b: global LLM pause — defense-in-depth gate
        # alongside the call-site short-circuit in save/ingest.py.
        # Catches any future code path that schedules from outside ingest.
        self._is_globally_paused = (
            is_globally_paused if is_globally_paused is not None else (lambda: False)
        )
        self._get_bumped_at = get_bumped_at if get_bumped_at is not None else (lambda _cid: None)
        # Issue #46: None means "ask the provider". A provider reporting
        # a nonsense width must not be able to produce a zero-permit
        # semaphore — that would deadlock the queue forever with no error,
        # which is strictly worse than running one at a time.
        width = max_concurrent if max_concurrent is not None else provider.max_concurrent
        if not isinstance(width, int) or width < 1:
            log.warning(
                "narrative scheduler: provider %r reported max_concurrent=%r; "
                "clamping to 1 (a non-positive width would deadlock the queue)",
                getattr(provider, "name", type(provider).__name__),
                width,
            )
            width = 1
        self._max_concurrent = width
        self._semaphore = asyncio.Semaphore(width)
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        # ck3_chronicler-c5wq: item_id → asyncio.Task lookup so the queue
        # page's Cancel button can find an in-flight task and cancel it.
        # Populated in _enqueue_and_spawn the moment a task is created,
        # cleared in _run's `finally` whether the task succeeds, fails, or
        # is cancelled. Keyed only when queue_state is wired (item_id is
        # not None) — no lookup is possible otherwise.
        self._active_tasks: dict[int, asyncio.Task[None]] = {}
        # ck3_chronicler-fiv6 (2026-05-08): deferred-narrative mode
        # (ck3_chronicler-kdf) was removed. It existed to keep local-LLM
        # work off the GPU/CPU while CK3 was running, but tbrm.3 deleted
        # the local-tier providers — every biography call now shells out
        # to claude --print (hosted), which has no contention with CK3.
        # ck3_chronicler-eev: optional shared queue state. When supplied,
        # every scheduled task records lifecycle transitions (queued →
        # generating → completed/failed) so the FastAPI app can serve a
        # snapshot endpoint and the SSE channel can fan out per-event
        # narrative_* frames to the AppShell activity strip. None keeps
        # the back-compat path for tests + the standalone CLI.
        self._queue_state = queue_state

    @property
    def factory(self) -> _SessionFactory:
        """The session factory this scheduler writes through.

        ck3_chronicler-27ov.78 (audit L13): public read accessor so the
        llm-pause drain reads ``scheduler.factory`` instead of reaching
        for the private ``_factory``.
        """
        return self._factory

    # ck3_chronicler-27ov.43 (audit M-N4): the per-task `kind` parameter and
    # regenerate's `force_rebuild` were dead generality left from the
    # demolished memory pipeline — QueueKind is single-valued ("biography")
    # and _run never read force_rebuild (a silent no-op for callers). Both
    # are gone end-to-end; QueueItem keeps the literal "biography" for the
    # API wire shape.

    def schedule(self, character_id: int) -> None:
        """Fire-and-forget biography generation for ``character_id``.

        Skipped (silently) if:
        - the character isn't on the tracked list, or
        - we're not running on an asyncio event loop (test contexts).
        """
        if not self._is_tracked(character_id):
            log.debug(
                "schedule(biography): character %d is not tracked; skipping",
                character_id,
            )
            return
        # ck3_chronicler-t2v5: paused chars stay tracked but their
        # biographies don't auto-fire until the user explicitly Resumes
        # from the Tracked page.
        if self._is_paused(character_id):
            log.info(
                "schedule(biography): character %d is paused; skipping",
                character_id,
            )
            return
        # ck3_chronicler-gx7b: global LLM pause. Defense-in-depth — the
        # call sites in save/ingest.py short-circuit before calling
        # schedule(); this guards against any future scheduler caller
        # that wasn't aware of the call-site gate (e.g. a backfill
        # script). regenerate() deliberately bypasses this so explicit
        # user actions still work.
        if self._is_globally_paused():
            log.info(
                "schedule(biography): LLM globally paused; skipping (character %d)",
                character_id,
            )
            return
        self._enqueue_and_spawn(character_id, caller="schedule")

    def regenerate(self, character_id: int) -> int | None:
        """User-initiated regeneration (ck3_chronicler-7gw).

        Bypasses the tracked-set / paused / global-pause gates that
        :meth:`schedule` honours — those are appropriate for the
        save-tail's automatic stream, but a regenerate is the user
        explicitly asking for this character right now. Still serialises
        through the same single-lane semaphore + per-character lock so
        a concurrent scheduled run can't race-insert two biography rows.

        Returns the queue ``item_id`` when ``queue_state`` is wired, so
        the caller can correlate the SSE narrative_* frames; ``None``
        when no queue state is attached or there's no event loop
        (the latter shouldn't happen in production — the route handler
        runs under uvicorn's loop).
        """
        return self._enqueue_and_spawn(character_id, caller="regenerate")

    def _resolve_character_name(self, character_id: int) -> str | None:
        """Best-effort first-name lookup for the queue-item label
        (ck3_chronicler-27ov.81 / audit L30).

        Resolved here, at enqueue, so the queue strip + page can render
        names without a FE join against the active campaign's top-250
        relevance window — which missed tracked souls on large campaigns
        and could never name cross-campaign items at all. The scheduler
        owns its campaign's session factory, so this naturally resolves
        against the correct per-campaign DB. A single primary-key read;
        the multi-second LLM call is the thing we keep off the loop, not
        a sub-millisecond lookup. Returns None on any miss/error —
        labelling must never wedge enqueue, and the FE falls back to
        "character {id}"."""
        try:
            from chronicler.db.engine import session_scope
            from chronicler.db.repository import get_character

            with session_scope(self._factory) as session:
                character = get_character(session, character_id)
                return character.first_name if character is not None else None
        except Exception:  # noqa: BLE001 — best-effort label, never fatal
            log.debug(
                "could not resolve name for character %d at enqueue",
                character_id,
                exc_info=True,
            )
            return None

    def _enqueue_and_spawn(self, character_id: int, *, caller: str) -> int | None:
        """Shared enqueue + create_task tail of schedule/regenerate
        (27ov.43 merged the duplicated block)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("%s called outside an event loop; skipping", caller)
            return None
        # Enqueue *before* spawning so the AppShell strip sees a "queued"
        # event the moment the user's death lands, even while the task
        # is still waiting on the semaphore.
        item_id: int | None = None
        if self._queue_state is not None:
            item_id = self._queue_state.enqueue(
                character_id,
                "biography",
                campaign_uuid=self._campaign_uuid,
                character_name=self._resolve_character_name(character_id),
            )
        task = loop.create_task(self._run(character_id, item_id=item_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if item_id is not None:
            self._active_tasks[item_id] = task
        return item_id

    async def cancel_item(self, item_id: int) -> bool:
        """Cancel a queued or in-flight narrative task.

        Returns ``True`` when the item was found and cancellation was
        signalled, ``False`` when no active task exists for the id (the
        item already finished, was already cancelled, or never existed).

        Cancellation is hard: ``task.cancel()`` propagates a CancelledError
        which in turn triggers ``claude_code.py``'s subprocess-kill path
        for active tasks, and falls through the outer try in :meth:`_run`
        for queued tasks. Both paths land at :meth:`mark_failed` with
        error="cancelled" before the task exits.

        The method awaits ``task`` so that by the time it returns, the
        queue state has been updated — callers can serialise the post-
        cancel snapshot in the same request.
        """
        task = self._active_tasks.get(item_id)
        if task is None or task.done():
            return False
        task.cancel()
        # Wait for the task to actually finish processing the cancellation
        # — gather with return_exceptions swallows the CancelledError so
        # we don't have to wrap in try/except.
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def reorder_queued(self, requested_ids: list[int]) -> list[int]:
        """Reorder the currently-queued narrative tasks.

        Implementation note (spec deviation, ck3_chronicler-c5wq): the
        spec described a ``bumped_at``-driven dispatch loop, but the
        scheduler doesn't have one — queued items are asyncio.Tasks
        already awaiting ``self._semaphore.acquire()``, and
        ``asyncio.Semaphore`` is FIFO. We therefore implement reorder
        as **cancel-and-respawn**: cancel every currently-queued task,
        then re-enqueue them in the requested order so the new tasks
        line up at the semaphore in the desired sequence.

        Order rules:
        - IDs in ``requested_ids`` come first, in the order given,
          skipping any that don't match a queued item.
        - Items not mentioned in ``requested_ids`` are appended in
          their current (item_id-ascending) order — so a partial
          reorder doesn't reshuffle unrelated rows.

        Returns the new item_ids of the re-enqueued tasks, in the
        resulting dispatch order. New ids are assigned because the
        original tasks have been cancelled (their entries in
        ``_items`` are popped on cancel). Callers should refetch the
        queue snapshot.
        """
        if self._queue_state is None:
            return []

        snap = self._queue_state.snapshot()
        queued_by_id: dict[int, object] = {i.item_id: i for i in snap.queued}
        if not queued_by_id:
            return []

        # Build the desired sequence: requested first (filtered to
        # known ids), then un-mentioned items in current order.
        seen: set[int] = set()
        ordered_items: list = []
        for rid in requested_ids:
            item = queued_by_id.get(rid)
            if item is None or rid in seen:
                continue
            ordered_items.append(item)
            seen.add(rid)
        # ck3_chronicler-c5wq: if none of the requested ids matched a
        # currently-queued item (stale snapshot, all unknown), short-
        # circuit — no point cancelling-and-respawning items the user
        # didn't actually ask to move. Returning [] here is the contract
        # the unknown-ids test asserts.
        if not ordered_items:
            return []
        for original in snap.queued:
            if original.item_id in seen:
                continue
            ordered_items.append(original)

        # ck3_chronicler-27ov.42 (audit M-N3): reordering is "left the queue",
        # NOT "failed". Drop these items from queue_state up front so the
        # cancel below — which routes through _run's CancelledError handler ->
        # mark_failed('cancelled') — is a no-op (mark_failed early-returns when
        # the item is already gone). Without this, dragging N rows registered N
        # failures: inflated failed_count + per-character stats + spurious
        # narrative_failed SSE frames. Explicit user Cancel still marks failed.
        for item in snap.queued:
            self._queue_state.remove(item.item_id)

        # Cancel every queued task. Cancel takes effect when each task's
        # await on self._semaphore yields — which it must, because
        # CancelledError is delivered the next time the coroutine is
        # resumed. We then await each one to be sure the queue_state
        # has been updated before re-enqueueing.
        for item in snap.queued:
            task = self._active_tasks.get(item.item_id)
            if task is not None and not task.done():
                task.cancel()
        # Await all cancellations together. CancelledErrors are swallowed
        # by gather(return_exceptions=True).
        await asyncio.gather(
            *(t for t in (self._active_tasks.get(i.item_id) for i in snap.queued) if t is not None),
            return_exceptions=True,
        )

        # Re-spawn in the new order. Each new task races for the
        # semaphore in the order create_task is called — FIFO under
        # asyncio.Semaphore.
        new_ids: list[int] = []
        for item in ordered_items:
            # Use the existing regenerate path so we bypass tracked/
            # paused/cooldown gates — the user explicitly asked for
            # these items to remain queued, just in a different order.
            new_id = self.regenerate(item.character_id)
            if new_id is not None:
                new_ids.append(new_id)
        return new_ids

    async def _run(
        self,
        character_id: int,
        *,
        item_id: int | None = None,
    ) -> None:
        # ck3_chronicler-c5wq: outer try wraps the semaphore acquire so a
        # cancel that fires while this task is still queued (waiting on
        # the semaphore) marks the item failed("cancelled") on its way
        # out instead of leaking as an uncaught CancelledError. The
        # `finally` clears the item_id→task index regardless of outcome.
        try:
            async with self._semaphore:
                lock = self._locks.setdefault(character_id, asyncio.Lock())
                async with lock:
                    start_loop_time = asyncio.get_running_loop().time()
                    if self._queue_state is not None and item_id is not None:
                        self._queue_state.mark_started(item_id)
                    try:
                        outcome = await generate_biography(
                            character_id,
                            factory=self._factory,
                            provider=self._provider,
                            campaign_uuid=self._campaign_uuid,
                        )
                        elapsed = asyncio.get_running_loop().time() - start_loop_time
                        duration_ms = int(elapsed * 1000)
                        if outcome.error is not None:
                            log.warning(
                                "biography returned error for %d: %s",
                                character_id,
                                outcome.error,
                            )
                            if self._queue_state is not None and item_id is not None:
                                # ck3_chronicler-sezy: log lifecycle transitions
                                # so we can read mark_completed / mark_failed
                                # firing from chronicler.log alone.
                                log.info(
                                    "queue.mark_failed item_id=%d character_id=%d kind=biography",
                                    item_id,
                                    character_id,
                                )
                                self._queue_state.mark_failed(item_id, str(outcome.error))
                        else:
                            if self._queue_state is not None and item_id is not None:
                                log.info(
                                    "queue.mark_completed item_id=%d "
                                    "character_id=%d kind=biography "
                                    "duration_ms=%d",
                                    item_id,
                                    character_id,
                                    duration_ms,
                                )
                                self._queue_state.mark_completed(item_id, duration_ms)
                    except asyncio.CancelledError:
                        # Active cancel: cancel arrived mid-generation. The
                        # claude_code.py CancelledError handler has already
                        # killed the subprocess. We mark failed("cancelled")
                        # and re-raise so asyncio sees the task as cancelled.
                        if self._queue_state is not None and item_id is not None:
                            self._queue_state.mark_failed(item_id, "cancelled")
                        raise
                    except Exception as e:
                        # Last-line defence: any unexpected failure must not
                        # propagate out of a fire-and-forget task — that would
                        # leave an unhandled task exception on the event loop.
                        log.exception("biography task failed for character %d", character_id)
                        if self._queue_state is not None and item_id is not None:
                            self._queue_state.mark_failed(item_id, repr(e))
        except asyncio.CancelledError:
            # Queued cancel: cancel arrived while still waiting on the
            # semaphore — never entered the inner try. Mark failed and
            # re-raise.
            if self._queue_state is not None and item_id is not None:
                # If the active-cancel path above already marked failed,
                # this is a no-op — mark_failed early-returns when the
                # item has been popped from _items.
                self._queue_state.mark_failed(item_id, "cancelled")
            raise
        finally:
            if item_id is not None:
                self._active_tasks.pop(item_id, None)

    async def drain(self, *, timeout: float = 60.0) -> None:
        """Wait for outstanding tasks; cancel any still running after timeout."""
        while self._tasks:
            snapshot = list(self._tasks)
            done, pending = await asyncio.wait(snapshot, timeout=timeout)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            # If pending was non-empty, we timed out; bail out instead of
            # looping forever. The cancelled tasks' done_callbacks remove
            # them from self._tasks, but defensive break here in case any
            # newly-spawned tasks would keep us spinning past the budget.
            if pending:
                break


# Back-compat alias. Older imports still resolve; existing call sites
# (BiographyScheduler(...).schedule(id)) continue to work unchanged.
BiographyScheduler = NarrativeScheduler
