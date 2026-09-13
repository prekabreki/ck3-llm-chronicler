"""Tests for the bounded-concurrency, order-preserving async pipeline
helper (ck3_chronicler-r8l1).

The save-ingest consumer parses rakaly saves (the ~80%/9.5s bottleneck)
but must run the diff/ingest tail strictly in save order. ``map_ordered_bounded``
parses up to N saves concurrently while yielding results in the exact order
items were pulled, so the consumer keeps its serial, in-order semantics with
zero event loss while the slow parses overlap.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from chronicler.util.async_pipeline import map_ordered_bounded


@pytest.mark.asyncio
async def test_preserves_input_order_despite_out_of_order_completion() -> None:
    """Workers finish out of order (item 0 is slowest, odd items instant),
    but results must come back in input order."""
    source: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    for i in range(5):
        await source.put(i)
    await source.put(sentinel)

    delays = {0: 0.05, 1: 0.0, 2: 0.04, 3: 0.0, 4: 0.03}

    async def worker(i: int) -> int:
        await asyncio.sleep(delays[i])
        return i * 10

    out = [pair async for pair in map_ordered_bounded(source, sentinel, worker, concurrency=3)]
    assert out == [(0, 0), (1, 10), (2, 20), (3, 30), (4, 40)]


@pytest.mark.asyncio
async def test_caps_concurrency_at_the_configured_limit() -> None:
    """At most `concurrency` workers run at once; all items still yielded
    in order."""
    source: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    for i in range(6):
        await source.put(i)
    await source.put(sentinel)

    active = 0
    peak = 0

    async def worker(i: int) -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return i

    out = [item async for item, _ in map_ordered_bounded(source, sentinel, worker, concurrency=2)]
    assert out == [0, 1, 2, 3, 4, 5]
    assert peak == 2, f"expected exactly 2 concurrent workers, saw peak={peak}"


@pytest.mark.asyncio
async def test_empty_source_yields_nothing() -> None:
    source: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    await source.put(sentinel)

    async def worker(i: int) -> int:  # pragma: no cover - never called
        return i

    out = [pair async for pair in map_ordered_bounded(source, sentinel, worker, concurrency=3)]
    assert out == []


@pytest.mark.asyncio
async def test_worker_returning_none_is_passed_through() -> None:
    """The save parser returns None on failure; the helper must surface
    that None to the consumer rather than dropping or raising."""
    source: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    for i in range(3):
        await source.put(i)
    await source.put(sentinel)

    async def worker(i: int):
        return None if i == 1 else i

    out = [
        result async for _, result in map_ordered_bounded(source, sentinel, worker, concurrency=2)
    ]
    assert out == [0, None, 2]


@pytest.mark.asyncio
async def test_yields_ready_head_without_blocking_to_fill_window() -> None:
    """Regression (ck3_chronicler-jea4): a finite burst with NO sentinel,
    then a quiet queue — exactly what the save-tail producer does (seed the
    startup backlog, then park in watch_saves with nothing imminent and no
    sentinel). Every already-parsed item must still be yielded.

    Pre-fix the window-fill loop blocked on ``await source.get()`` trying to
    top the window back up to ``width`` BEFORE yielding a completed head,
    stranding the last parsed save behind an empty queue. That hung
    save-ingest recovery at 'Catching up · N left' forever (and, in live
    play, delayed every save until the next one arrived).
    """
    source: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    # Two items, NO sentinel. With concurrency=3 the pre-fix fill loop tries
    # to pull a third item and blocks indefinitely — so even the FIRST yield
    # never arrives.
    for i in range(2):
        await source.put(i)

    async def worker(i: int) -> int:
        return i * 10

    gen = map_ordered_bounded(source, sentinel, worker, concurrency=3)
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    finally:
        await gen.aclose()
    assert first == (0, 0)
    assert second == (1, 10)


@pytest.mark.asyncio
async def test_cancels_inflight_workers_when_consumer_stops_early() -> None:
    """If the consumer breaks out early (loop teardown), in-flight parse
    tasks must be cancelled, not left running — otherwise rakaly children
    leak on shutdown."""
    source: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    for i in range(6):
        await source.put(i)
    await source.put(sentinel)

    cancelled: list[int] = []
    started: list[int] = []

    async def worker(i: int) -> int:
        started.append(i)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(i)
            raise
        return i

    gen = map_ordered_bounded(source, sentinel, worker, concurrency=3)
    # Pull nothing to completion; close the generator while workers are
    # in flight.
    started_first = await asyncio.wait_for(_first_started(gen, started), timeout=1.0)
    assert started_first
    await gen.aclose()
    # Give cancellation a tick to propagate.
    await asyncio.sleep(0)
    assert cancelled, "in-flight workers must be cancelled on early close"


async def _first_started(gen, started) -> bool:
    """Drive the generator just enough to spin up the first window of
    workers, then return without consuming a result."""
    task = asyncio.ensure_future(gen.__anext__())
    while not started:
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await task
    return True
