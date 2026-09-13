"""Tests for chronicler.narrative.queue_state.NarrativeQueueState."""

from __future__ import annotations

from chronicler.narrative.queue_state import NarrativeQueueState, QueueItem


def test_enqueue_assigns_increasing_ids() -> None:
    s = NarrativeQueueState()
    a = s.enqueue(1234, "biography")
    b = s.enqueue(5678, "biography")
    c = s.enqueue(1234, "biography")
    assert a == 1
    assert b == 2
    assert c == 3


def test_snapshot_separates_queued_active_recent() -> None:
    s = NarrativeQueueState()
    a = s.enqueue(1, "biography")
    b = s.enqueue(2, "biography")
    c = s.enqueue(3, "biography")
    s.mark_started(b)
    s.mark_completed(c, duration_ms=12345)

    snap = s.snapshot()
    assert [i.character_id for i in snap.queued] == [1]
    assert [i.character_id for i in snap.active] == [2]
    assert [i.character_id for i in snap.recent] == [3]
    assert snap.completed_count == 1
    assert snap.failed_count == 0
    assert snap.avg_duration_ms == 12345
    # Note: c is no longer queued, so character 3 not in items
    assert a == 1


def test_failed_increments_failed_count_not_completed() -> None:
    s = NarrativeQueueState()
    item_id = s.enqueue(1, "biography")
    s.mark_started(item_id)
    s.mark_failed(item_id, "timeout")

    snap = s.snapshot()
    assert snap.completed_count == 0
    assert snap.failed_count == 1
    assert len(snap.recent) == 1
    failed = snap.recent[0]
    assert failed.status == "failed"
    assert failed.error == "timeout"


def test_avg_duration_averages_completed_only() -> None:
    s = NarrativeQueueState()
    a = s.enqueue(1, "biography")
    s.mark_completed(a, duration_ms=1000)
    b = s.enqueue(2, "biography")
    s.mark_completed(b, duration_ms=3000)
    c = s.enqueue(3, "biography")
    s.mark_failed(c, "x")  # failed shouldn't pollute avg

    snap = s.snapshot()
    assert snap.avg_duration_ms == 2000


# ck3_chronicler-27ov.43 (audit M-N4): has_active (f80k) was deleted —
# it had zero production callers; its test went with it.


def test_recent_capped_at_recent_capacity() -> None:
    s = NarrativeQueueState(recent_capacity=3)
    for _ in range(5):
        item_id = s.enqueue(1, "biography")
        s.mark_completed(item_id, duration_ms=10)

    snap = s.snapshot()
    assert len(snap.recent) == 3
    # Cumulative completed_count keeps climbing past cap
    assert snap.completed_count == 5


def test_listeners_fire_on_each_transition() -> None:
    s = NarrativeQueueState()
    seen: list[tuple[int, str]] = []
    s.add_listener(lambda item: seen.append((item.item_id, item.status)))

    item_id = s.enqueue(42, "biography")
    s.mark_started(item_id)
    s.mark_completed(item_id, duration_ms=500)

    assert seen == [(item_id, "queued"), (item_id, "generating"), (item_id, "completed")]


def test_listener_exception_does_not_break_state() -> None:
    s = NarrativeQueueState()

    def boom(_item: QueueItem) -> None:
        raise RuntimeError("listener exploded")

    s.add_listener(boom)
    item_id = s.enqueue(1, "biography")  # must not raise
    s.mark_started(item_id)
    s.mark_completed(item_id, duration_ms=1)

    snap = s.snapshot()
    assert snap.completed_count == 1


def test_mark_started_on_unknown_id_is_noop() -> None:
    s = NarrativeQueueState()
    s.mark_started(999)  # must not raise
    s.mark_completed(999, duration_ms=1)  # must not raise
    s.mark_failed(999, "x")  # must not raise

    snap = s.snapshot()
    assert snap.completed_count == 0
    assert snap.failed_count == 0


def test_mark_completed_negative_duration_clamped_to_zero() -> None:
    s = NarrativeQueueState()
    item_id = s.enqueue(1, "biography")
    s.mark_completed(item_id, duration_ms=-50)
    snap = s.snapshot()
    assert snap.recent[0].duration_ms == 0


def test_enqueued_at_is_iso_utc() -> None:
    s = NarrativeQueueState()
    s.enqueue(1, "biography")
    snap = s.snapshot()
    ts = snap.queued[0].enqueued_at
    # ISO 8601 with timezone — sanity check, not full parser
    assert "T" in ts
    assert ts.endswith("+00:00") or ts.endswith("Z")


def test_started_at_recorded_on_mark_started() -> None:
    s = NarrativeQueueState()
    item_id = s.enqueue(1, "biography")
    s.mark_started(item_id)
    snap = s.snapshot()
    item = snap.active[0]
    assert item.started_at is not None


# --- ck3_chronicler-fjln: per-character stats panel ---


def test_character_stats_empty_when_nothing_completed() -> None:
    """fjln: with no work done yet, character_stats is the empty list.
    Frontend uses this to skip rendering the panel cleanly."""
    s = NarrativeQueueState()
    s.enqueue(1234, "biography")  # queued but not completed
    assert s.character_stats() == []


def test_character_stats_aggregates_per_character_kind_pair() -> None:
    """fjln: stats are keyed on (character_id, kind). Two biography
    runs for the same character surface as one row with cumulative counts."""
    s = NarrativeQueueState()
    a = s.enqueue(1234, "biography")
    s.mark_completed(a, duration_ms=2000)
    b = s.enqueue(1234, "biography")
    s.mark_completed(b, duration_ms=500)

    stats = s.character_stats()
    by_key = {(row.character_id, row.kind): row for row in stats}
    assert (1234, "biography") in by_key
    assert by_key[(1234, "biography")].completed_count == 2


def test_character_stats_records_completed_and_failed_separately() -> None:
    """fjln: success/fail ratio comes from these two fields."""
    s = NarrativeQueueState()
    a = s.enqueue(1, "biography")
    s.mark_completed(a, duration_ms=1000)
    b = s.enqueue(1, "biography")
    s.mark_failed(b, "ollama 500")
    c = s.enqueue(1, "biography")
    s.mark_failed(c, "json parse")

    stats = s.character_stats()
    assert len(stats) == 1
    row = stats[0]
    assert row.character_id == 1
    assert row.kind == "biography"
    assert row.completed_count == 1
    assert row.failed_count == 2


def test_character_stats_median_duration_from_recent_samples() -> None:
    """fjln: median of recent durations. Stable on odd / even sample
    counts; failed runs do not contribute."""
    s = NarrativeQueueState()
    durations = [100, 300, 200, 700, 500]  # median = 300
    for d in durations:
        item_id = s.enqueue(1, "biography")
        s.mark_completed(item_id, duration_ms=d)
    # A failed run must not pollute the median.
    bad = s.enqueue(1, "biography")
    s.mark_failed(bad, "timeout")

    stats = s.character_stats()
    assert len(stats) == 1
    assert stats[0].median_duration_ms == 300


def test_character_stats_records_last_success_at() -> None:
    """fjln: last_success_at is the ISO timestamp of the most recent
    successful completion. Failed runs don't update it."""
    s = NarrativeQueueState()
    a = s.enqueue(1, "biography")
    s.mark_completed(a, duration_ms=1000)
    # Another success should update last_success_at.
    b = s.enqueue(1, "biography")
    s.mark_completed(b, duration_ms=2000)
    # Failure after success: must NOT clear last_success_at.
    c = s.enqueue(1, "biography")
    s.mark_failed(c, "x")

    stats = s.character_stats()
    assert len(stats) == 1
    assert stats[0].last_success_at is not None
    assert "T" in stats[0].last_success_at
    # No assertion on the exact timestamp; we just need it set + ISO-shaped.


def test_character_stats_median_capped_at_sample_window() -> None:
    """fjln: median is computed from a bounded sliding window so memory
    doesn't grow unbounded over a marathon. 20 samples is enough to
    smooth provider noise; older durations roll off."""
    s = NarrativeQueueState()
    # 30 fast runs first, then 1 slow run. The 30 fast ones should
    # dominate a 20-sample window after the 1 slow run lands at the
    # tail (older 11 fast-runs roll off; window holds 19 fast + 1 slow).
    for _ in range(30):
        item_id = s.enqueue(1, "biography")
        s.mark_completed(item_id, duration_ms=100)
    slow = s.enqueue(1, "biography")
    s.mark_completed(slow, duration_ms=999_999)

    stats = s.character_stats()
    # Median of 19 x 100 + 1 x 999999 in a 20-sample window: sorted
    # has 19 hundreds at the front so element [10] (0-indexed) is 100.
    assert stats[0].median_duration_ms == 100


def test_find_recent_returns_completed_and_failed_items() -> None:
    """ck3_chronicler-c5wq: the regenerate endpoint resolves
    (character_id, kind) from a recent-ring item_id."""
    s = NarrativeQueueState()
    a = s.enqueue(1, "biography")
    s.mark_completed(a, duration_ms=100)
    b = s.enqueue(2, "biography")
    s.mark_failed(b, "x")

    found_a = s.find_recent(a)
    assert found_a is not None
    assert found_a.character_id == 1
    assert found_a.kind == "biography"
    assert found_a.status == "completed"

    found_b = s.find_recent(b)
    assert found_b is not None
    assert found_b.status == "failed"

    # Unknown id → None.
    assert s.find_recent(9999) is None


def test_find_recent_does_not_match_live_queued_items() -> None:
    """Live items (queued / generating) are NOT in the recent ring;
    find_recent must only see completed/failed history."""
    s = NarrativeQueueState()
    live = s.enqueue(1, "biography")
    assert s.find_recent(live) is None


# --- ck3_chronicler-27ov.81 (audit L30): character_name on the wire ---


def test_enqueue_carries_character_name() -> None:
    """L30: the name resolved at enqueue rides on the QueueItem so the
    queue strip/page render it without a FE join. Defaults to None when
    the caller (older callers / tests) doesn't supply one."""
    s = NarrativeQueueState()
    named = s.enqueue(1234, "biography", character_name="Erik")
    anon = s.enqueue(5678, "biography")

    by_id = {i.item_id: i for i in s.snapshot().queued}
    assert by_id[named].character_name == "Erik"
    assert by_id[anon].character_name is None


def test_character_name_survives_to_recent_ring() -> None:
    """L30: the name is preserved through completion onto the recent ring,
    so the 'recently completed/failed' rows are named too."""
    s = NarrativeQueueState()
    a = s.enqueue(1234, "biography", character_name="Erik")
    s.mark_completed(a, duration_ms=100)
    assert s.snapshot().recent[0].character_name == "Erik"


def test_character_stats_carry_character_name() -> None:
    """L30: the per-character stats row is stamped with the name from the
    QueueItem on each transition, so the stats panel renders names without
    its own FE join. A failure-only character is still named."""
    s = NarrativeQueueState()
    a = s.enqueue(1234, "biography", character_name="Erik")
    s.mark_completed(a, duration_ms=100)
    b = s.enqueue(5678, "biography", character_name="Astrid")
    s.mark_failed(b, "timeout")

    by_id = {row.character_id: row for row in s.character_stats()}
    assert by_id[1234].character_name == "Erik"
    assert by_id[5678].character_name == "Astrid"
