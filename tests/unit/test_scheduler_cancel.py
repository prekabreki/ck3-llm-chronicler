"""Tests for NarrativeScheduler cancel semantics (ck3_chronicler-c5wq)."""

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
async def test_cancel_queued_marks_failed_and_does_not_run_provider(factory) -> None:
    """A queued task that is cancelled before acquiring the semaphore
    must (a) be marked failed with error="cancelled" and (b) never
    invoke the provider."""
    with factory() as s:
        upsert_character(s, ck3_id=1)
        upsert_character(s, ck3_id=2)
        s.commit()

    # Slow provider + semaphore=1 means the second task stays queued
    # behind the first.
    started = asyncio.Event()
    block = asyncio.Event()

    class BlockingProvider(CountingProvider):
        async def generate(self, req):  # type: ignore[override]
            started.set()
            await block.wait()
            return await super().generate(req)

    provider = BlockingProvider()
    queue = NarrativeQueueState()
    scheduler = NarrativeScheduler(factory, provider, queue_state=queue, max_concurrent=1)

    scheduler.schedule(1)
    scheduler.schedule(2)
    # Item 1 starts (semaphore acquired), item 2 is queued behind it.
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # Find item 2's item_id from the queue state.
    snap = queue.snapshot()
    assert len(snap.queued) == 1
    queued_id = snap.queued[0].item_id

    cancelled = await scheduler.cancel_item(queued_id)
    assert cancelled is True

    # Let item 1 finish so the test can shut down cleanly.
    block.set()
    await scheduler.drain()

    final = queue.snapshot()
    # Item 2 never made it into provider.calls.
    assert provider.calls == [1]
    # Item 2 lives in the recent ring as failed("cancelled").
    failed_recent = [i for i in final.recent if i.item_id == queued_id]
    assert len(failed_recent) == 1
    assert failed_recent[0].status == "failed"
    assert failed_recent[0].error == "cancelled"


@pytest.mark.asyncio
async def test_cancel_active_cancels_task_and_marks_failed(factory) -> None:
    """An active task that is cancelled must have its asyncio task
    actually cancelled (CancelledError delivered into the provider's
    in-flight await) and be marked failed with error="cancelled".

    M-T4 (27ov.70): this test used to also assert proc.kill/proc.wait —
    against its own FakeProc copy of claude_code.py's try/except, which
    proved nothing about production. The real subprocess-kill-on-cancel
    branch is covered in test_claude_code_provider.py (M-T5); what's
    left here is purely the scheduler's cancel contract."""
    with factory() as s:
        upsert_character(s, ck3_id=1)
        s.commit()

    cancelled_in_provider = asyncio.Event()

    class HangingProvider(CountingProvider):
        async def generate(self, req):  # type: ignore[override]
            char_id = int(req.metadata.get("character_id", "-1"))
            self.calls.append(char_id)
            try:
                # Hang "forever" — gives the test time to cancel.
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                cancelled_in_provider.set()
                raise
            from chronicler.narrative.provider import NarrativeResponse

            return NarrativeResponse(
                text="x", model="x", input_tokens=0, output_tokens=0, latency_ms=0
            )

    provider = HangingProvider()
    queue = NarrativeQueueState()
    scheduler = NarrativeScheduler(factory, provider, queue_state=queue, max_concurrent=1)

    scheduler.schedule(1)
    # Let the task progress past mark_started.
    for _ in range(20):
        if any(i.status == "generating" for i in queue.snapshot().active):
            break
        await asyncio.sleep(0.02)

    active = queue.snapshot().active
    assert len(active) == 1
    item_id = active[0].item_id

    cancelled = await scheduler.cancel_item(item_id)
    assert cancelled is True
    assert cancelled_in_provider.is_set()

    failed = [i for i in queue.snapshot().recent if i.item_id == item_id]
    assert len(failed) == 1
    assert failed[0].status == "failed"
    assert failed[0].error == "cancelled"
