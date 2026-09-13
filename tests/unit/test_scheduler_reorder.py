"""Tests for NarrativeScheduler.reorder_queued (ck3_chronicler-c5wq)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import upsert_character
from chronicler.narrative.queue_state import NarrativeQueueState
from chronicler.narrative.scheduler import NarrativeScheduler
from tests.helpers.providers import CountingProvider


@pytest.fixture
def factory(tmp_path: Path) -> Iterator:
    engine = make_engine_for_path(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_reorder_queued_changes_dispatch_order(factory) -> None:
    """Reorder cancels currently-queued tasks and re-spawns them in the
    requested order, so the provider sees the new sequence."""
    for cid in (1, 2, 3, 4):
        with factory() as s:
            upsert_character(s, ck3_id=cid)
            s.commit()

    block = asyncio.Event()

    class BlockingProvider(CountingProvider):
        async def generate(self, req):  # type: ignore[override]
            char_id = int(req.metadata.get("character_id", "-1"))
            self.calls.append(char_id)
            await block.wait()
            from chronicler.narrative.provider import NarrativeResponse

            return NarrativeResponse(
                text=f"bio {char_id}", model="x", input_tokens=0, output_tokens=0, latency_ms=1
            )

    provider = BlockingProvider()
    queue = NarrativeQueueState()
    scheduler = NarrativeScheduler(factory, provider, queue_state=queue, max_concurrent=1)

    # Enqueue 4 items; item 1 starts (semaphore), items 2/3/4 queue.
    scheduler.schedule(1)
    scheduler.schedule(2)
    scheduler.schedule(3)
    scheduler.schedule(4)
    # Let item 1 acquire the semaphore.
    for _ in range(20):
        if provider.calls:
            break
        await asyncio.sleep(0.02)

    snap = queue.snapshot()
    queued = snap.queued  # sorted by item_id
    assert [i.character_id for i in queued] == [2, 3, 4]
    new_order_ids = [queued[2].item_id, queued[0].item_id]  # [4, 2]; 3 omitted

    new_ids = await scheduler.reorder_queued(new_order_ids)
    # reorder returns the *new* item_ids in the resulting queue order.
    assert len(new_ids) == 3  # 4, 2, 3 (un-mentioned items appended)

    # Let the queue drain.
    block.set()
    await scheduler.drain()

    # Provider was called in order: item 1 (already running), then 4, 2, 3.
    assert provider.calls == [1, 4, 2, 3]


@pytest.mark.asyncio
async def test_reorder_does_not_register_failures(factory) -> None:
    """ck3_chronicler-27ov.42 (audit M-N3): reorder is cancel-and-respawn, but
    a reordered item 'left the queue' — it did NOT fail. The old path routed
    each cancelled queued task through mark_failed('cancelled'), inflating
    failed_count + per-character failed stats and firing spurious
    narrative_failed SSE frames (dragging 5 rows = 5 'failures'). Reordering
    must register zero failures."""
    for cid in (1, 2, 3):
        with factory() as s:
            upsert_character(s, ck3_id=cid)
            s.commit()

    block = asyncio.Event()

    class BlockingProvider(CountingProvider):
        async def generate(self, req):  # type: ignore[override]
            self.calls.append(int(req.metadata.get("character_id", "-1")))
            await block.wait()
            from chronicler.narrative.provider import NarrativeResponse

            return NarrativeResponse(
                text="x", model="x", input_tokens=0, output_tokens=0, latency_ms=0
            )

    provider = BlockingProvider()
    queue = NarrativeQueueState()
    failed_frames: list = []
    queue.add_listener(lambda item: failed_frames.append(item) if item.status == "failed" else None)
    scheduler = NarrativeScheduler(factory, provider, queue_state=queue, max_concurrent=1)

    scheduler.schedule(1)  # acquires the semaphore (blocks)
    scheduler.schedule(2)
    scheduler.schedule(3)
    for _ in range(20):
        if provider.calls:
            break
        await asyncio.sleep(0.02)

    queued = queue.snapshot().queued  # items 2, 3
    assert [i.character_id for i in queued] == [2, 3]
    # Reorder the two queued items (3 before 2).
    await scheduler.reorder_queued([queued[1].item_id, queued[0].item_id])

    block.set()
    await scheduler.drain()

    final = queue.snapshot()
    assert final.failed_count == 0, "reorder must not register failures"
    assert all(i.status != "failed" for i in final.recent), (
        "no failed entries in the recent ring after a reorder"
    )
    assert failed_frames == [], "no narrative_failed frames for reordered items"


@pytest.mark.asyncio
async def test_reorder_queued_with_unknown_ids_silently_skips(factory) -> None:
    """Unknown / stale item_ids in the request are ignored — the client
    may be working against a stale snapshot. No raise."""
    with factory() as s:
        upsert_character(s, ck3_id=1)
        s.commit()
    block = asyncio.Event()

    class BlockingProvider(CountingProvider):
        async def generate(self, req):  # type: ignore[override]
            self.calls.append(int(req.metadata.get("character_id", "-1")))
            await block.wait()
            from chronicler.narrative.provider import NarrativeResponse

            return NarrativeResponse(
                text="x", model="x", input_tokens=0, output_tokens=0, latency_ms=0
            )

    provider = BlockingProvider()
    queue = NarrativeQueueState()
    scheduler = NarrativeScheduler(factory, provider, queue_state=queue, max_concurrent=1)
    scheduler.schedule(1)

    # Reorder against entirely unknown ids → no-op.
    result = await scheduler.reorder_queued([9999, 7777])
    assert result == []  # nothing reordered

    block.set()
    await scheduler.drain()
    assert provider.calls == [1]
