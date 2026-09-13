"""Tests for NarrativeScheduler — fire-and-forget biography generation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import (
    get_latest_biography_for_character,
    insert_event_idempotent,
    list_biographies_for_character,
    upsert_character,
)
from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
)
from chronicler.narrative.scheduler import BiographyScheduler, NarrativeScheduler
from tests.helpers.providers import CountingProvider


@pytest.fixture
def factory(tmp_path: Path) -> Iterator:
    engine = make_engine_for_path(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _allow_all_tracked(_character_id: int) -> bool:
    return True


@pytest.mark.asyncio
async def test_schedule_creates_biography_row(factory) -> None:
    with factory() as s:
        upsert_character(s, ck3_id=1234)
        s.commit()
    provider = CountingProvider()
    scheduler = BiographyScheduler(factory, provider)

    scheduler.schedule(1234)
    await scheduler.drain()

    assert provider.calls == [1234]
    with factory() as s:
        bio = get_latest_biography_for_character(s, 1234)
    assert bio is not None
    assert bio.body == "biography for 1234"


@pytest.mark.asyncio
async def test_schedule_outside_event_loop_is_noop(factory) -> None:
    """Calling schedule from a sync context with no running loop must not
    crash — process_lines (the test helper) doesn't run async."""
    provider = CountingProvider()
    scheduler = BiographyScheduler(factory, provider)
    # Note: NOT inside @pytest.mark.asyncio's loop — but since we ARE in
    # one (decorator on the function), use asyncio.get_event_loop().stop()
    # workaround. Simpler: test by patching out the loop check.
    import unittest.mock

    with unittest.mock.patch(
        "chronicler.narrative.scheduler.asyncio.get_running_loop",
        side_effect=RuntimeError("no running event loop"),
    ):
        scheduler.schedule(1234)  # should not raise

    # No tasks were created
    assert scheduler._tasks == set()


@pytest.mark.asyncio
async def test_per_character_lock_serializes_concurrent_schedules(factory) -> None:
    """Two rapid-fire schedules for the same character must not generate
    two biographies concurrently. The lock makes the second run wait
    until the first commits."""
    with factory() as s:
        upsert_character(s, ck3_id=1234)
        s.commit()

    provider = CountingProvider(delay=0.05)
    scheduler = BiographyScheduler(factory, provider)

    scheduler.schedule(1234)
    scheduler.schedule(1234)
    await scheduler.drain()

    # Both ran (both calls made)
    assert provider.calls == [1234, 1234]
    # And both biographies persisted with sequential versions
    with factory() as s:
        bios = list_biographies_for_character(s, 1234)
    assert [b.version for b in bios] == [1, 2]


@pytest.mark.asyncio
async def test_provider_failure_does_not_propagate(factory) -> None:
    with factory() as s:
        upsert_character(s, ck3_id=1234)
        s.commit()
    provider = CountingProvider(fail_with=RuntimeError("network down"))
    scheduler = BiographyScheduler(factory, provider)

    scheduler.schedule(1234)
    await scheduler.drain()  # must complete cleanly

    # No biography row created
    with factory() as s:
        assert get_latest_biography_for_character(s, 1234) is None


@pytest.mark.asyncio
async def test_drain_with_no_tasks_is_noop(factory) -> None:
    scheduler = BiographyScheduler(factory, CountingProvider())
    await scheduler.drain()  # should return immediately, no errors


@pytest.mark.asyncio
async def test_untracked_character_is_skipped(factory) -> None:
    """V02-N04 follow-up: scheduler must skip characters not on the tracked
    list — that's our v0.2 throttle for the world-is-too-big problem."""
    with factory() as s:
        upsert_character(s, ck3_id=1234)
        upsert_character(s, ck3_id=5678)
        s.commit()

    provider = CountingProvider()
    # Only 1234 is tracked; 5678 is not.
    scheduler = BiographyScheduler(factory, provider, is_tracked=lambda cid: cid == 1234)

    scheduler.schedule(1234)
    scheduler.schedule(5678)
    await scheduler.drain()

    # Provider was only called for the tracked character
    assert provider.calls == [1234]
    with factory() as s:
        assert get_latest_biography_for_character(s, 1234) is not None
        assert get_latest_biography_for_character(s, 5678) is None


@pytest.mark.asyncio
async def test_concurrency_capped_by_semaphore(factory) -> None:
    """Even when many schedules fire, max_concurrent limits in-flight
    biographies — so the connection pool can't be overwhelmed."""
    with factory() as s:
        for cid in range(1000, 1005):
            upsert_character(s, ck3_id=cid)
        s.commit()

    inflight: list[int] = []
    peak: list[int] = [0]
    provider_lock = asyncio.Lock()

    class _TracingProvider(NarrativeProvider):
        @property
        def name(self):
            return "tracing:v1"

        async def generate(self, req):
            char_id = int(req.metadata["character_id"])
            async with provider_lock:
                inflight.append(char_id)
                peak[0] = max(peak[0], len(inflight))
            await asyncio.sleep(0.05)  # simulate LLM latency
            async with provider_lock:
                inflight.remove(char_id)
            return NarrativeResponse(
                text=f"bio for {char_id}",
                model=self.name,
                input_tokens=1,
                output_tokens=1,
                latency_ms=50,
            )

    scheduler = BiographyScheduler(factory, _TracingProvider(), max_concurrent=2)
    for cid in range(1000, 1005):
        scheduler.schedule(cid)
    await scheduler.drain()

    # Five biographies ran, but never more than 2 concurrent.
    assert peak[0] <= 2
    with factory() as s:
        for cid in range(1000, 1005):
            assert get_latest_biography_for_character(s, cid) is not None


@pytest.mark.asyncio
async def test_default_concurrency_allows_four_inflight(factory) -> None:
    """ck3_chronicler-w2se: the default semaphore permits a 4-wide
    biography fan-out (hosted claude --print is remote-bound, within the
    documented 2-4 safe range), so a backlog of deaths drains faster.
    Constructed with no explicit max_concurrent so this pins the DEFAULT."""
    with factory() as s:
        for cid in range(2000, 2010):
            upsert_character(s, ck3_id=cid)
        s.commit()

    inflight: list[int] = []
    peak: list[int] = [0]
    provider_lock = asyncio.Lock()

    class _TracingProvider(NarrativeProvider):
        @property
        def name(self):
            return "tracing:v1"

        async def generate(self, req):
            char_id = int(req.metadata["character_id"])
            async with provider_lock:
                inflight.append(char_id)
                peak[0] = max(peak[0], len(inflight))
            await asyncio.sleep(0.05)
            async with provider_lock:
                inflight.remove(char_id)
            return NarrativeResponse(
                text=f"bio for {char_id}",
                model=self.name,
                input_tokens=1,
                output_tokens=1,
                latency_ms=50,
            )

    # No max_concurrent kwarg -> exercises the production default.
    scheduler = NarrativeScheduler(factory, _TracingProvider())
    for cid in range(2000, 2010):
        scheduler.schedule(cid)
    await scheduler.drain()

    # Default fan-out is 4: peak reaches 4 but never exceeds it.
    assert peak[0] == 4


@pytest.mark.asyncio
async def test_drain_cancels_tasks_past_timeout(factory) -> None:
    with factory() as s:
        upsert_character(s, ck3_id=1234)
        s.commit()
    provider = CountingProvider(delay=10.0)  # would-block-forever
    scheduler = BiographyScheduler(factory, provider)
    scheduler.schedule(1234)

    await scheduler.drain(timeout=0.1)

    # The task was cancelled — no biography written
    with factory() as s:
        assert get_latest_biography_for_character(s, 1234) is None


def test_biography_scheduler_alias_resolves_to_narrative_scheduler() -> None:
    """Existing imports of BiographyScheduler must keep working post-rename."""
    assert BiographyScheduler is NarrativeScheduler


# --- ck3_chronicler-eev: queue state integration ---


@pytest.mark.asyncio
async def test_scheduler_records_queue_lifecycle(factory) -> None:
    """When a queue_state is supplied, scheduler emits queued → started →
    completed transitions for each scheduled biography."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    with factory() as s:
        upsert_character(s, ck3_id=1234)
        s.commit()

    state = NarrativeQueueState()
    seen: list[tuple[int, str]] = []
    state.add_listener(lambda item: seen.append((item.character_id, item.status)))

    provider = CountingProvider()
    scheduler = NarrativeScheduler(factory, provider, queue_state=state)
    scheduler.schedule(1234)
    await scheduler.drain()

    statuses = [s for _, s in seen]
    assert statuses == ["queued", "generating", "completed"]
    snap = state.snapshot()
    assert snap.completed_count == 1
    assert snap.failed_count == 0
    assert snap.queued == []
    assert snap.active == []


@pytest.mark.asyncio
async def test_scheduler_marks_failed_on_provider_error(factory) -> None:
    from chronicler.narrative.queue_state import NarrativeQueueState

    with factory() as s:
        upsert_character(s, ck3_id=1234)
        s.commit()

    state = NarrativeQueueState()
    provider = CountingProvider(fail_with=RuntimeError("network down"))
    scheduler = NarrativeScheduler(factory, provider, queue_state=state)
    scheduler.schedule(1234)
    await scheduler.drain()

    snap = state.snapshot()
    assert snap.completed_count == 0
    assert snap.failed_count == 1
    assert snap.recent[0].status == "failed"
    assert "network down" in (snap.recent[0].error or "")


@pytest.mark.asyncio
async def test_untracked_character_does_not_enqueue(factory) -> None:
    """Filter must run before enqueue — untracked schedules don't
    pollute the queue with phantom 'queued' items that never start."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    with factory() as s:
        upsert_character(s, ck3_id=5678)
        s.commit()

    state = NarrativeQueueState()
    scheduler = NarrativeScheduler(
        factory, CountingProvider(), is_tracked=lambda cid: False, queue_state=state
    )
    scheduler.schedule(5678)
    await scheduler.drain()

    snap = state.snapshot()
    assert snap.completed_count == 0
    assert snap.failed_count == 0
    assert snap.queued == []


def test_resolve_character_name_reads_from_campaign_db(factory) -> None:
    """ck3_chronicler-27ov.81 (audit L30): _resolve_character_name returns
    the first name for a known character and None for an unknown one.
    Best-effort label — a miss must never wedge enqueue."""
    with factory() as s:
        upsert_character(s, ck3_id=1234, first_name="Erik")
        s.commit()

    scheduler = NarrativeScheduler(factory, CountingProvider())
    assert scheduler._resolve_character_name(1234) == "Erik"
    assert scheduler._resolve_character_name(9999) is None


@pytest.mark.asyncio
async def test_scheduler_stamps_character_name_on_enqueue(factory) -> None:
    """L30: schedule() resolves the name at enqueue and stamps it on the
    QueueItem, so every lifecycle frame and the recent ring carry it —
    the FE no longer joins character_id against the active campaign's
    character window (which missed tracked souls on large campaigns and
    never named cross-campaign items)."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    with factory() as s:
        upsert_character(s, ck3_id=1234, first_name="Erik")
        s.commit()

    state = NarrativeQueueState()
    names: list[str | None] = []
    state.add_listener(lambda item: names.append(item.character_name))

    scheduler = NarrativeScheduler(factory, CountingProvider(), queue_state=state)
    scheduler.schedule(1234)
    await scheduler.drain()

    # queued → generating → completed all observed the resolved name.
    assert names and all(n == "Erik" for n in names)
    assert state.snapshot().recent[0].character_name == "Erik"


def _seed_char_with_event(factory, ck3_id: int) -> None:
    with factory() as s:
        upsert_character(s, ck3_id=ck3_id, first_name="Eadmund")
        eid = insert_event_idempotent(
            s,
            schema_version=1,
            event_type="title_gain",
            event_date="14th of October, 1066 AD",
            event_date_iso="1066-10-14",
            wall_clock_at="2026-05-01T12:00:00+00:00",
            primary_character_id=ck3_id,
            payload_json=f'{{"v":1,"t":"x","d":"x","c":{ck3_id},"p":{{}}}}',
            raw_line=f"line-{ck3_id}",
        )
        assert eid is not None
        s.commit()


def _seed_tracked_char_with_region_summary(factory, ck3_id: int) -> None:
    """Seeds a character whose region_summary_json is populated. The
    summary content doesn't matter for scheduler-level tests — only its
    truthiness."""
    with factory() as s:
        upsert_character(
            s,
            ck3_id=ck3_id,
            first_name="Erik",
            culture="norse",
            region_summary_json='{"region_empire_name":"Scandinavia"}',
        )
        eid = insert_event_idempotent(
            s,
            schema_version=1,
            event_type="title_gain",
            event_date="14th of October, 1066 AD",
            event_date_iso="1066-10-14",
            wall_clock_at="2026-05-01T12:00:00+00:00",
            primary_character_id=ck3_id,
            payload_json=f'{{"v":1,"t":"x","d":"x","c":{ck3_id},"p":{{}}}}',
            raw_line=f"line-{ck3_id}",
        )
        assert eid is not None
        s.commit()


# --- ck3_chronicler-t2v5: paused + bumped tracked-character state ---


@pytest.mark.asyncio
async def test_schedule_skips_paused_character(factory) -> None:
    """A character with paused_at set must be skipped at schedule time
    even if is_tracked returns True. Pause is the user's explicit
    'don't generate biographies for this soul right now' signal — see
    vysp.10's tracked-page Pause action."""
    _seed_char_with_event(factory, ck3_id=1234)
    _seed_char_with_event(factory, ck3_id=5678)

    paused_ids = {1234}
    provider = CountingProvider()
    scheduler = NarrativeScheduler(
        factory,
        provider,
        is_paused=lambda cid: cid in paused_ids,
    )

    scheduler.schedule(1234)
    scheduler.schedule(5678)
    await scheduler.drain()

    assert provider.calls == [5678]


# --- ck3_chronicler-7gw: user-initiated regenerate bypasses gates ---


@pytest.mark.asyncio
async def test_regenerate_bypasses_tracked_filter(factory) -> None:
    """Regenerate is the user explicitly asking for this character. The
    tracked-set is the auto-stream's throttle, not a permission gate."""
    with factory() as s:
        upsert_character(s, ck3_id=9999)
        s.commit()
    provider = CountingProvider()
    scheduler = NarrativeScheduler(factory, provider, is_tracked=lambda _cid: False)

    scheduler.regenerate(9999)
    await scheduler.drain()

    assert provider.calls == [9999]


@pytest.mark.asyncio
async def test_regenerate_bypasses_paused(factory) -> None:
    """Pause means 'don't auto-fire on save-tail'. A user clicking
    Regenerate has overridden that intent for this one run."""
    with factory() as s:
        upsert_character(s, ck3_id=9999)
        s.commit()
    provider = CountingProvider()
    scheduler = NarrativeScheduler(factory, provider, is_paused=lambda _cid: True)

    scheduler.regenerate(9999)
    await scheduler.drain()

    assert provider.calls == [9999]


@pytest.mark.asyncio
async def test_regenerate_returns_queue_item_id(factory) -> None:
    """When queue_state is wired, regenerate returns the enqueued item_id
    so the route can return it to the UI for SSE correlation."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    with factory() as s:
        upsert_character(s, ck3_id=9999)
        s.commit()
    state = NarrativeQueueState()
    provider = CountingProvider()
    scheduler = NarrativeScheduler(factory, provider, queue_state=state)

    item_id = scheduler.regenerate(9999)
    assert item_id == 1
    await scheduler.drain()

    snap = state.snapshot()
    assert snap.completed_count == 1


# --- ck3_chronicler-tbrm.2: post-pivot ClaudeCodeProvider behaviour ---


@dataclass
class FakeClaudeCodeProvider(NarrativeProvider):
    """Test stand-in for narrative.claude_code.ClaudeCodeProvider:
    captures NarrativeRequest objects so tests can assert on metadata
    threading."""

    requests: list[NarrativeRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "claude-code:fake-model"

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        self.requests.append(req)
        return NarrativeResponse(
            text=f"biography for {req.metadata.get('character_id')}",
            model=self.name,
            input_tokens=10,
            output_tokens=20,
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_scheduler_threads_campaign_uuid_to_request_metadata(factory) -> None:
    """tbrm.1: NarrativeScheduler(campaign_uuid="abc-123", ...) must
    forward the uuid to NarrativeRequest.metadata so the
    ClaudeCodeProvider can compute briefings/abc-123/<char>-vN.md
    paths. The pipeline already conditionally adds the key — this test
    pins down the scheduler-side wiring."""
    _seed_tracked_char_with_region_summary(factory, ck3_id=7777)
    provider = FakeClaudeCodeProvider()
    scheduler = NarrativeScheduler(factory, provider, campaign_uuid="abc-uuid-123")

    scheduler.schedule(7777)
    await scheduler.drain()

    assert len(provider.requests) == 1
    metadata = provider.requests[0].metadata
    assert metadata["character_id"] == "7777"
    assert metadata["campaign_uuid"] == "abc-uuid-123"


@pytest.mark.asyncio
async def test_scheduler_omits_campaign_uuid_when_none(factory) -> None:
    """When the scheduler is constructed without campaign_uuid (the
    pre-tbrm signature), the metadata simply doesn't carry the key.
    The ClaudeCodeProvider then defaults to the "_unscoped" bucket —
    that fallback is exercised by the provider's own unit tests."""
    _seed_tracked_char_with_region_summary(factory, ck3_id=7778)
    provider = FakeClaudeCodeProvider()
    scheduler = NarrativeScheduler(factory, provider)

    scheduler.schedule(7778)
    await scheduler.drain()

    assert len(provider.requests) == 1
    metadata = provider.requests[0].metadata
    assert metadata["character_id"] == "7778"
    assert "campaign_uuid" not in metadata


# --- issue #46: the width comes from the provider ---


class _DeclaredWidthProvider(NarrativeProvider):
    """A provider that reports whatever width it was handed.

    Stands in for the openai-compatible presets, which declare their own
    (1 for a local Ollama/LM Studio server sharing one GPU, 4 for cloud).
    Never scheduled — these tests only read the width off the scheduler.
    """

    def __init__(self, width: int) -> None:
        self._width = width

    @property
    def name(self) -> str:
        return "ollama:llama3"

    @property
    def max_concurrent(self) -> int:
        return self._width

    async def generate(self, req):  # pragma: no cover - never scheduled
        raise AssertionError("not called")


@pytest.mark.asyncio
async def test_provider_max_concurrent_serialises_a_local_backend(factory) -> None:
    """A local openai-compatible preset declares max_concurrent=1 because one
    Ollama/LM Studio server shares one GPU. The scheduler must honour it
    without the caller passing anything — this is the case the hardcoded 4
    got wrong."""
    with factory() as s:
        for cid in range(3000, 3006):
            upsert_character(s, ck3_id=cid)
        s.commit()

    peak = [0]
    inflight = []
    lock = asyncio.Lock()

    class _SerialProvider(NarrativeProvider):
        @property
        def name(self):
            return "ollama:llama3"

        @property
        def max_concurrent(self) -> int:
            return 1

        async def generate(self, req):
            async with lock:
                inflight.append(1)
                peak[0] = max(peak[0], len(inflight))
            await asyncio.sleep(0.02)
            async with lock:
                inflight.pop()
            return NarrativeResponse(
                text="bio",
                model=self.name,
                input_tokens=1,
                output_tokens=1,
                latency_ms=20,
            )

    scheduler = NarrativeScheduler(factory, _SerialProvider())
    for cid in range(3000, 3006):
        scheduler.schedule(cid)
    await scheduler.drain()

    assert peak[0] == 1


def test_explicit_max_concurrent_still_overrides_the_provider(factory) -> None:
    scheduler = NarrativeScheduler(factory, _DeclaredWidthProvider(1), max_concurrent=3)
    assert scheduler._max_concurrent == 3


def test_nonsense_provider_width_clamps_to_one_instead_of_deadlocking(factory, caplog) -> None:
    """A zero-permit semaphore would hang the queue forever with no error,
    which is strictly worse than running one at a time."""

    with caplog.at_level(logging.WARNING):
        scheduler = NarrativeScheduler(factory, _DeclaredWidthProvider(0))
    assert scheduler._max_concurrent == 1
    assert "clamping to 1" in caplog.text
