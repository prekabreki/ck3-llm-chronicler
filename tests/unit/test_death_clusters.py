"""ck3_chronicler-qdh8: death-cluster detection over relative_died memories."""

from __future__ import annotations

import json
from dataclasses import dataclass

from chronicler.narrative.clusters import (
    DEATH_CLUSTER_MIN_COUNT,
    DEATH_CLUSTER_WINDOW_DAYS,
    ClusterDeath,
    DeathCluster,
    detect_death_clusters,
    format_death_cluster_block,
)


@dataclass(frozen=True, slots=True)
class _FakeEvent:
    """Minimal stand-in for narrative.pipeline._EventSnapshot.

    detect_death_clusters takes a Sequence of objects exposing the four
    fields it cares about. Using a fake keeps the unit tests independent
    of pipeline.py's internal type, and avoids importing private symbols.
    """

    event_type: str
    event_date: str
    event_date_iso: str | None
    payload_json: str


def _relative_died(
    *,
    iso: str | None,
    ck3_date: str | None = None,
    first_name: str | None = "Margit",
    age: int | None = 21,
    cause: str | None = "death_disease_typhus",
) -> _FakeEvent:
    """Build a relative_died vanilla_memory event row for tests."""
    payload = {
        "memory_type": "relative_died",
        "participants": {"dead_relation": 99},
    }
    if first_name is not None:
        payload["deceased_first_name"] = first_name
    if age is not None:
        payload["deceased_age"] = age
    if cause is not None:
        payload["deceased_cause"] = cause
    if ck3_date:
        ck3 = ck3_date
    elif iso:
        y, m, d = iso.split("-")
        ck3 = f"{int(y)}.{int(m)}.{int(d)}"
    else:
        ck3 = "????"
    return _FakeEvent(
        event_type="vanilla_memory",
        event_date=ck3,
        event_date_iso=iso,
        payload_json=json.dumps(payload),
    )


def _other_memory(*, iso: str, memory_type: str = "memory_grand_wedding") -> _FakeEvent:
    return _FakeEvent(
        event_type="vanilla_memory",
        event_date=iso.replace("-", "."),
        event_date_iso=iso,
        payload_json=json.dumps({"memory_type": memory_type, "participants": {}}),
    )


def test_no_relative_died_events_returns_empty() -> None:
    events = [_other_memory(iso="1100-01-01")]
    assert detect_death_clusters(events) == []


def test_two_deaths_within_window_below_min_count_returns_empty() -> None:
    events = [
        _relative_died(iso="1102-06-02"),
        _relative_died(iso="1102-06-07"),
    ]
    assert detect_death_clusters(events) == []


def test_three_deaths_within_window_returns_one_cluster() -> None:
    events = [
        _relative_died(iso="1102-06-02"),
        _relative_died(iso="1102-06-16"),
        _relative_died(iso="1102-06-17"),
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.start_date == "1102.6.2"
    assert c.end_date == "1102.6.17"
    assert c.count == 3


def test_three_deaths_with_a_gap_exceeding_window_returns_empty() -> None:
    # Adjacent gaps of 35 days each — nothing merges, no sub-cluster.
    events = [
        _relative_died(iso="1100-01-01"),
        _relative_died(iso="1100-02-05"),
        _relative_died(iso="1100-03-12"),
    ]
    assert detect_death_clusters(events) == []


def test_slow_trickle_within_adjacent_window_still_merges() -> None:
    # 4 deaths at days 0/25/50/75. Adjacent gaps are 25 days, all <= window.
    # Total span 75d > window, but merge uses last-death-anchor.
    events = [
        _relative_died(iso="1100-01-01"),
        _relative_died(iso="1100-01-26"),
        _relative_died(iso="1100-02-20"),
        _relative_died(iso="1100-03-17"),
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 1
    assert clusters[0].count == 4


def test_two_separate_clusters() -> None:
    # First cluster: 3 deaths in 10 days. Then 90 days quiet.
    # Second cluster: 4 deaths in 20 days.
    events = [
        _relative_died(iso="1100-01-01"),
        _relative_died(iso="1100-01-05"),
        _relative_died(iso="1100-01-10"),
        _relative_died(iso="1100-04-10"),
        _relative_died(iso="1100-04-15"),
        _relative_died(iso="1100-04-25"),
        _relative_died(iso="1100-04-30"),
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 2
    assert clusters[0].count == 3
    assert clusters[1].count == 4


def test_non_relative_died_memories_are_ignored() -> None:
    # Two relative_died with a wedding between them, all within 30 days.
    # Only 2 deaths → below min_count → empty.
    events = [
        _relative_died(iso="1100-01-01"),
        _other_memory(iso="1100-01-10"),
        _relative_died(iso="1100-01-20"),
    ]
    assert detect_death_clusters(events) == []


def test_boundary_window_inclusive() -> None:
    # Exactly 30 days apart counts as in-window.
    events = [
        _relative_died(iso="1100-01-01"),
        _relative_died(iso="1100-01-31"),
        _relative_died(iso="1100-03-02"),  # 30 days after the second
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 1
    assert clusters[0].count == 3


def test_unparseable_date_is_dropped_not_partial_cluster() -> None:
    # A None iso date should be dropped from the cluster scan, not
    # silently bundled or used to short-circuit the loop.
    events = [
        _relative_died(iso="1100-01-01"),
        _relative_died(iso=None),  # unparseable upstream — event_date_iso is None
        _relative_died(iso="1100-01-10"),
        _relative_died(iso="1100-01-20"),
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 1
    assert clusters[0].count == 3  # the three real deaths


def test_cluster_carries_cause_age_first_name_from_payload() -> None:
    events = [
        _relative_died(iso="1102-06-02", first_name="Margit", age=21, cause="death_disease_typhus"),
        _relative_died(
            iso="1102-06-16", first_name="Knud", age=14, cause="death_disease_consumption"
        ),
        _relative_died(
            iso="1102-06-17", first_name="Thorgunna", age=8, cause="death_disease_typhus"
        ),
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 1
    deaths = clusters[0].deaths
    assert [d.first_name for d in deaths] == ["Margit", "Knud", "Thorgunna"]
    assert [d.age for d in deaths] == [21, 14, 8]
    assert [d.cause for d in deaths] == [
        "death_disease_typhus",
        "death_disease_consumption",
        "death_disease_typhus",
    ]


def test_pre_7jwu_payload_with_null_cause_age_still_counted() -> None:
    # Old relative_died rows pre-7jwu have no deceased_* fields.
    events = [
        _relative_died(iso="1100-01-01", first_name=None, age=None, cause=None),
        _relative_died(iso="1100-01-10", first_name=None, age=None, cause=None),
        _relative_died(iso="1100-01-20", first_name=None, age=None, cause=None),
    ]
    clusters = detect_death_clusters(events)
    assert len(clusters) == 1
    deaths = clusters[0].deaths
    assert all(d.cause is None and d.age is None and d.first_name is None for d in deaths)


def test_defaults_match_module_constants() -> None:
    """ck3_chronicler-qdh8: detection defaults are the public constants
    so callers can override at the call site without re-deriving them."""
    assert DEATH_CLUSTER_MIN_COUNT == 3
    assert DEATH_CLUSTER_WINDOW_DAYS == 30


def test_format_block_renders_single_cluster() -> None:
    cluster = DeathCluster(
        start_date="1102.6.2",
        end_date="1102.6.17",
        deaths=(
            ClusterDeath(
                date="1102.6.2",
                first_name="Margit",
                age=21,
                cause="death_disease_typhus",
            ),
            ClusterDeath(
                date="1102.6.16",
                first_name="Knud",
                age=14,
                cause=None,
            ),
            ClusterDeath(
                date="1102.6.17",
                first_name="Thorgunna",
                age=8,
                cause="death_disease_typhus",
            ),
        ),
    )
    block = format_death_cluster_block([cluster])
    assert "Recorded death clusters in this character's life:" in block
    assert "1102.6.2 → 1102.6.17" in block
    assert "3 relatives" in block
    assert "Margit (21, death_disease_typhus)" in block
    assert "Knud (14, —)" in block  # em-dash for missing cause
    assert "Thorgunna (8, death_disease_typhus)" in block


def test_format_block_empty_when_no_clusters() -> None:
    assert format_death_cluster_block([]) == ""


def test_format_block_renders_multiple_clusters_sorted() -> None:
    c1 = DeathCluster(
        start_date="1100.1.1",
        end_date="1100.1.10",
        deaths=(
            ClusterDeath(date="1100.1.1", first_name="A", age=20, cause="death_disease"),
            ClusterDeath(date="1100.1.5", first_name="B", age=21, cause="death_disease"),
            ClusterDeath(date="1100.1.10", first_name="C", age=22, cause="death_disease"),
        ),
    )
    c2 = DeathCluster(
        start_date="1118.3.4",
        end_date="1118.4.1",
        deaths=(
            ClusterDeath(date="1118.3.4", first_name="D", age=30, cause="death_battle"),
            ClusterDeath(date="1118.3.20", first_name="E", age=31, cause="death_battle"),
            ClusterDeath(date="1118.4.1", first_name="F", age=32, cause="death_battle"),
        ),
    )
    block = format_death_cluster_block([c1, c2])
    pos1 = block.index("1100.1.1")
    pos2 = block.index("1118.3.4")
    assert pos1 < pos2


def test_format_block_handles_all_null_payload_fields() -> None:
    cluster = DeathCluster(
        start_date="1100.1.1",
        end_date="1100.1.20",
        deaths=(
            ClusterDeath(date="1100.1.1", first_name=None, age=None, cause=None),
            ClusterDeath(date="1100.1.10", first_name=None, age=None, cause=None),
            ClusterDeath(date="1100.1.20", first_name=None, age=None, cause=None),
        ),
    )
    block = format_death_cluster_block([cluster])
    assert "— (—, —)" in block


def test_format_block_includes_day_span() -> None:
    """The cluster header carries the day count so the LLM knows the
    tempo (15 days reads as outbreak; 60 days reads as bad year)."""
    cluster = DeathCluster(
        start_date="1102.6.2",
        end_date="1102.6.17",
        deaths=(
            ClusterDeath(date="1102.6.2", first_name="A", age=21, cause="x"),
            ClusterDeath(date="1102.6.16", first_name="B", age=14, cause="x"),
            ClusterDeath(date="1102.6.17", first_name="C", age=8, cause="x"),
        ),
    )
    block = format_death_cluster_block([cluster])
    assert "15 days" in block
