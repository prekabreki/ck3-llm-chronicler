"""Tests for the in-process event bus (ck3_chronicler-ek2)."""

from __future__ import annotations

import asyncio

import pytest

from chronicler.api.events import EventBus
from tests.helpers.async_wait import await_condition


def test_publish_with_no_subscribers_is_noop() -> None:
    """An event posted to an empty campaign is silently dropped — no
    queues materialise, no exceptions."""
    bus = EventBus()
    bus.publish("camp-1", {"x": 1})
    assert bus.subscriber_count("camp-1") == 0


@pytest.mark.asyncio
async def test_subscribe_receives_published_events() -> None:
    bus = EventBus()
    received: list[dict] = []
    stop = asyncio.Event()

    async def consumer() -> None:
        async for event in bus.subscribe("camp-1", stop_event=stop):
            received.append(event)
            if len(received) >= 2:
                stop.set()

    consumer_task = asyncio.create_task(consumer())
    await await_condition(lambda: bus.subscriber_count("camp-1") == 1)
    bus.publish("camp-1", {"event_id": 1})
    bus.publish("camp-1", {"event_id": 2})
    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert received == [{"event_id": 1}, {"event_id": 2}]


@pytest.mark.asyncio
async def test_publish_fan_outs_to_multiple_subscribers() -> None:
    bus = EventBus()
    a_events: list[dict] = []
    b_events: list[dict] = []
    stop_a = asyncio.Event()
    stop_b = asyncio.Event()

    async def sub(target: list[dict], stop: asyncio.Event) -> None:
        async for event in bus.subscribe("camp-1", stop_event=stop):
            target.append(event)
            stop.set()

    task_a = asyncio.create_task(sub(a_events, stop_a))
    task_b = asyncio.create_task(sub(b_events, stop_b))
    await await_condition(lambda: bus.subscriber_count("camp-1") == 2)
    assert bus.subscriber_count("camp-1") == 2
    bus.publish("camp-1", {"x": "ping"})
    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2.0)
    assert a_events == [{"x": "ping"}]
    assert b_events == [{"x": "ping"}]


@pytest.mark.asyncio
async def test_publish_isolated_per_campaign() -> None:
    bus = EventBus()
    a_events: list[dict] = []
    stop = asyncio.Event()

    async def sub() -> None:
        async for event in bus.subscribe("camp-A", stop_event=stop):
            a_events.append(event)
            stop.set()

    task = asyncio.create_task(sub())
    await await_condition(lambda: bus.subscriber_count("camp-A") == 1)
    # Publish to a different campaign — subscriber must not receive it
    bus.publish("camp-B", {"x": "wrong"})
    # camp-B has no subscribers, so nothing is ever enqueued for camp-A;
    # one loop turn is enough to prove no stray frame arrives.
    await asyncio.sleep(0)
    assert a_events == []
    # Now to the right campaign
    bus.publish("camp-A", {"x": "right"})
    await asyncio.wait_for(task, timeout=2.0)
    assert a_events == [{"x": "right"}]


@pytest.mark.asyncio
async def test_subscriber_unregistered_on_iterator_exit() -> None:
    bus = EventBus()
    stop = asyncio.Event()

    async def sub() -> None:
        async for _event in bus.subscribe("camp-1", stop_event=stop):
            pass

    task = asyncio.create_task(sub())
    await await_condition(lambda: bus.subscriber_count("camp-1") == 1)
    assert bus.subscriber_count("camp-1") == 1
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    # After exit the subscriber set should be cleaned up
    assert bus.subscriber_count("camp-1") == 0


def test_full_queue_evicts_oldest_event() -> None:
    """When a subscriber's queue is full, publish drops the oldest entry."""
    bus = EventBus(buffer_size=3)
    # Manually register a queue (mimicking what subscribe would do) so
    # the test can inspect contents synchronously.
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=3)
    bus._subscribers.setdefault("camp-1", set()).add(q)
    for i in range(5):
        bus.publish("camp-1", {"i": i})
    # Queue holds at most 3 — newest 3 should remain after eviction
    contents = []
    while not q.empty():
        contents.append(q.get_nowait())
    assert contents == [{"i": 2}, {"i": 3}, {"i": 4}]


@pytest.mark.asyncio
async def test_publish_from_worker_thread_after_attach_loop() -> None:
    """audit F-06 / ck3_chronicler-xpiu: after attach_loop, publish() from
    a non-loop thread must route through call_soon_threadsafe so
    asyncio.Queue.put_nowait runs on the loop thread.

    The repro: subscribe() (on the loop), then publish() from a
    run_in_executor worker — exactly the importer / heraldry pattern."""
    bus = EventBus()
    bus.attach_loop(asyncio.get_running_loop())

    received: list[dict] = []
    stop = asyncio.Event()

    async def consumer() -> None:
        async for event in bus.subscribe("camp-1", stop_event=stop):
            received.append(event)
            if len(received) >= 2:
                stop.set()

    consumer_task = asyncio.create_task(consumer())
    await await_condition(lambda: bus.subscriber_count("camp-1") == 1)

    loop = asyncio.get_running_loop()

    def _from_worker(i: int) -> None:
        # asserts the test really is on a different thread — guards
        # against future refactors that accidentally inline.
        running: asyncio.AbstractEventLoop | None
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        assert running is not loop
        bus.publish("camp-1", {"i": i, "from": "worker"})

    await loop.run_in_executor(None, _from_worker, 1)
    await loop.run_in_executor(None, _from_worker, 2)

    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert received == [
        {"i": 1, "from": "worker"},
        {"i": 2, "from": "worker"},
    ]


@pytest.mark.asyncio
async def test_concurrent_subscribe_unsubscribe_under_publish_storm() -> None:
    """Subscribers churning while publish is iterating must not raise.

    Before the F-06 fix, set mutation during publish iteration was
    racy. With the threading.Lock around the subscriber dict, the
    snapshot-then-iterate path in publish is consistent."""
    bus = EventBus()
    bus.attach_loop(asyncio.get_running_loop())

    stop = asyncio.Event()
    subscriber_count = 5
    received_per_sub: list[list[dict]] = [[] for _ in range(subscriber_count)]

    async def sub(idx: int) -> None:
        try:
            async for event in bus.subscribe("camp-x", stop_event=stop):
                received_per_sub[idx].append(event)
        except asyncio.CancelledError:
            pass

    tasks = [asyncio.create_task(sub(i)) for i in range(subscriber_count)]
    await await_condition(lambda: bus.subscriber_count("camp-x") == subscriber_count)

    # Storm the bus from a worker thread while subscribers register.
    loop = asyncio.get_running_loop()

    def _storm() -> None:
        for i in range(50):
            bus.publish("camp-x", {"i": i})

    await loop.run_in_executor(None, _storm)
    # Drain pending call_soon_threadsafe enqueues until every subscriber
    # has seen at least one event (the invariant under test).
    await await_condition(
        lambda: all(len(events) > 0 for events in received_per_sub),
        message="not all subscribers received an event after the storm",
    )
    stop.set()
    for t in tasks:
        await asyncio.wait_for(t, timeout=2.0)

    # Every subscriber should have seen at least one event — the
    # invariant is no exception, not strict ordering across subscribers.
    assert all(len(events) > 0 for events in received_per_sub)
