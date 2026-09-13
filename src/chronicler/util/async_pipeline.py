"""Bounded-concurrency, order-preserving async pipeline helper.

ck3_chronicler-r8l1: save ingestion spends ~80% of every ~11.8s cycle in
rakaly (the binary .ck3 -> JSON melt), parsed serially one save at a time, so
a backlog builds during fast play and only drains after the player stops.

:func:`map_ordered_bounded` parses up to ``concurrency`` saves at once (each
rakaly run is an independent, side-effect-free subprocess, so N of them
overlap in the OS) while yielding ``(item, result)`` pairs in the *exact*
order items were pulled from the source. That lets the ingest consumer keep
its strictly-serial, in-save-order diff/baseline tail — zero event loss, no
diff-layer changes — while the slow parses run ahead.

Why a sliding window rather than a reorder buffer keyed on sequence number:
the window preserves order structurally (we always await the head task before
yielding) and needs no knowledge of how the caller numbers items, so there is
no gap/duplicate reasoning to get wrong.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


async def map_ordered_bounded(
    source: asyncio.Queue,
    sentinel: Any,
    worker: Callable[[Any], Awaitable[Any]],
    *,
    concurrency: int,
) -> AsyncIterator[tuple[Any, Any]]:
    """Pull items from ``source`` until ``sentinel``, apply async ``worker``
    to each with at most ``concurrency`` running at once, and yield
    ``(item, result)`` pairs in the same order the items were pulled.

    - Whatever ``worker`` returns (including ``None``) is surfaced verbatim;
      this helper does not interpret results.
    - On early close / cancellation (e.g. the consuming ``async for`` is
      torn down at shutdown), every in-flight worker task is cancelled and
      awaited before the generator exits, so subprocess children don't leak.
    - ``concurrency`` is clamped to at least 1.
    """
    width = max(1, concurrency)
    pending: deque[tuple[Any, asyncio.Task]] = deque()
    exhausted = False
    try:
        while True:
            # Fill the window: keep up to `width` worker tasks in flight.
            # ck3_chronicler-jea4: block on source.get() ONLY when nothing is
            # in flight (we have no ready head to yield, so we must wait for
            # input). While the window holds work, top it up non-blockingly —
            # a blocking get here would strand an already-complete head behind
            # an empty queue. The live producer seeds a finite backlog then
            # parks in watch_saves with no sentinel, so the queue routinely
            # empties mid-window; blocking to refill hung save-ingest recovery
            # at "Catching up · N left" forever (and delayed every live save
            # until the next one arrived).
            while not exhausted and len(pending) < width:
                if pending:
                    try:
                        item = source.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                else:
                    item = await source.get()
                if item is sentinel:
                    exhausted = True
                    break
                pending.append((item, asyncio.ensure_future(worker(item))))
            if not pending:
                return
            # Await the head IN ORDER. The remaining window tasks keep
            # running concurrently while we wait. Leave the head in `pending`
            # until the await succeeds so cancellation here still cancels it.
            item, task = pending[0]
            result = await task
            pending.popleft()
            yield item, result
    finally:
        for _, task in pending:
            task.cancel()
        for _, task in pending:
            with contextlib.suppress(BaseException):
                await task
