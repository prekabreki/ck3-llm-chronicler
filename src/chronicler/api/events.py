"""In-process event bus for the v0.7 SSE pipeline (ck3_chronicler-ek2).

A single :class:`EventBus` instance lives on ``app.state.event_bus`` and
is consulted by:

- :mod:`chronicler.save.ingest` (and tailer ingest path forthcoming)
  publishes per-event dicts via :meth:`EventBus.publish`.
- The forthcoming SSE endpoint (ck3_chronicler-owk) calls
  :meth:`EventBus.subscribe` once per long-poll connection and streams
  the resulting AsyncIterator out to the browser.

Subscriber buffering: each subscriber gets its own
``asyncio.Queue(maxsize=BUFFER_SIZE)``. When the queue is full the bus
drops the oldest event to make room (so a slow subscriber doesn't pin
unbounded memory). A "drop" event is *not* synthesised — the SSE
layer can re-sync via the regular polling endpoints if it needs
absolute fidelity. For settings/library UI use cases that's overkill.

Cross-thread publishes (audit F-06 / ck3_chronicler-xpiu): the import
and heraldry-extract routes call ``publish`` from a ``run_in_executor``
worker thread. ``asyncio.Queue.put_nowait`` is not thread-safe, so the
bus binds to its owning event loop in :meth:`attach_loop` (called from
the FastAPI lifespan startup) and routes off-loop publishes through
``loop.call_soon_threadsafe``. Subscriber-set mutations are guarded by
a regular threading.Lock so the iteration in publish is consistent
with concurrent subscribe/unsubscribe on the loop thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

log = logging.getLogger(__name__)

BUFFER_SIZE = 100


class EventBus:
    """Per-campaign fan-out of dict events to async subscribers.

    Thread-aware: subscribe/unsubscribe always run on the bound event
    loop. ``publish`` may be called from any thread; cross-thread calls
    schedule the actual enqueue back on the loop via
    ``call_soon_threadsafe`` (see audit F-06).
    """

    def __init__(self, buffer_size: int = BUFFER_SIZE) -> None:
        self._buffer_size = buffer_size
        # campaign_id -> set of subscriber queues
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        # Guards _subscribers; cheap (uncontended in the no-cross-thread
        # case because publish/subscribe normally co-locate on the loop).
        self._sub_lock = threading.Lock()
        # Bound by attach_loop() in the lifespan startup. None during
        # construction (create_app may run outside a running loop),
        # which forces publish onto the legacy in-thread path.
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the event loop that subscribes/iterates run on.

        Called once from the FastAPI lifespan startup. After this,
        :meth:`publish` invoked from any other thread is rerouted via
        ``loop.call_soon_threadsafe`` — required for thread-safe
        ``asyncio.Queue.put_nowait``. Calls before attach (e.g. tests
        that publish before any subscriber connects) take the in-thread
        path, which is fine as long as those tests stay single-threaded.
        """
        self._loop = loop

    def publish(self, campaign_id: str, event: dict[str, Any]) -> None:
        """Deliver ``event`` to every active subscriber on this campaign.

        On a full subscriber queue the oldest event is dropped to make
        room for the new one. Empty subscriber lists (no SSE clients
        connected) are a no-op cheap path; this keeps publish() cheap
        enough to call from the per-event ingest hot loop.

        Safe to call from any thread once :meth:`attach_loop` has been
        invoked (audit F-06): cross-thread publishes route through
        ``call_soon_threadsafe`` so the underlying ``put_nowait`` always
        runs on the loop thread.
        """
        # Snapshot under the lock so a concurrent subscribe/unsubscribe
        # on the loop thread can't mutate the set we're iterating.
        with self._sub_lock:
            queues = self._subscribers.get(campaign_id)
            if not queues:
                return
            snapshot = list(queues)

        loop = self._loop
        if loop is None:
            # Pre-attach (or test-only) path — assume single-threaded.
            for q in snapshot:
                self._enqueue(q, event)
            return

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            for q in snapshot:
                self._enqueue(q, event)
        else:
            for q in snapshot:
                loop.call_soon_threadsafe(self._enqueue, q, event)

    def _enqueue(self, q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            q.put_nowait(event)
            return
        except asyncio.QueueFull:
            # Drop the oldest then put the new one. Pragmatic — the
            # alternative (block ingest waiting for a slow SSE client)
            # would gum up the entire event loop.
            with suppress(asyncio.QueueEmpty):
                q.get_nowait()
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Truly pathological — log and move on.
                log.warning("event bus subscriber queue still full after evict; dropping event")

    def subscriber_count(self, campaign_id: str) -> int:
        """Test/observability helper: how many active subscribers on a campaign."""
        with self._sub_lock:
            return len(self._subscribers.get(campaign_id, ()))

    def register(
        self, campaign_id: str
    ) -> tuple[asyncio.Queue[dict[str, Any]], Callable[[], None]]:
        """Synchronously register a subscriber queue and return an
        ``(queue, unsubscribe)`` pair.

        ck3_chronicler-lb5r: pre-fix, ``subscribe`` was an async
        generator whose body — including this set.add — didn't run
        until the first ``__anext__()``. SSE consumers that returned a
        StreamingResponse then immediately published-after-connect
        landed in the gap and silently lost the frame. Root cause of
        j32g / jjqf / dsjo cold-start drops.

        The async iteration path is now :meth:`aiter_queue`. SSE
        endpoints should call ``register`` *before* yielding any wire
        bytes so the queue is on the subscriber set the instant the
        response begins.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._buffer_size)
        with self._sub_lock:
            self._subscribers.setdefault(campaign_id, set()).add(q)

        def _unsubscribe() -> None:
            with self._sub_lock:
                qs = self._subscribers.get(campaign_id)
                if qs is not None:
                    qs.discard(q)
                    if not qs:
                        self._subscribers.pop(campaign_id, None)

        return q, _unsubscribe

    async def aiter_queue(
        self,
        q: asyncio.Queue[dict[str, Any]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async-iterate events from a queue obtained via :meth:`register`.

        Returns when ``stop_event`` is set. Without a stop_event the
        iterator runs forever — caller must wrap with a timeout.

        The caller is responsible for invoking the unsubscribe callable
        from :meth:`register` on exit (typically in a try/finally
        wrapping the iteration).
        """
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
            except TimeoutError:
                continue
            yield event

    async def subscribe(
        self,
        campaign_id: str,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Test-only convenience wrapper: register + iterate in one async
        generator.

        No production callers remain — the SSE routes use the explicit
        register()/aiter_queue() pair so the queue joins the subscriber
        set *before* the response starts streaming. This wrapper pays the
        cold-start race ck3_chronicler-lb5r calls out: its body (including
        register()) doesn't run until the first ``__anext__()``, so a
        publish in the gap is dropped. It must not be reintroduced into
        production; it survives only as a terse iterator for tests.
        """
        q, unsubscribe = self.register(campaign_id)
        try:
            async for event in self.aiter_queue(q, stop_event=stop_event):
                yield event
        finally:
            unsubscribe()
