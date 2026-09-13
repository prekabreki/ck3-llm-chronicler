"""ck3_chronicler-2cc.1: curated event-roll rendering for the export."""

from __future__ import annotations

from chronicler.export.event_roll import (
    EventRollRow,
    build_event_roll,
    is_significant,
    render_roll_entry,
)


def test_noise_event_types_are_dropped():
    for t in (
        "travel",
        "modifier_acquired",
        "perk_acquired",
        "decision_taken",
        "domicile_moved",
    ):
        assert is_significant(t, {}) is False


def test_milestone_vanilla_memory_kept_social_churn_dropped():
    assert is_significant("vanilla_memory", {"memory_type": "married"}) is True
    assert is_significant("vanilla_memory", {"memory_type": "battle_won_memory"}) is True
    assert is_significant("vanilla_memory", {"memory_type": "became_friends"}) is False
    # Unknown / long-tail memory_type defaults to dropped.
    assert is_significant("vanilla_memory", {"memory_type": "some_future_memory"}) is False
    assert is_significant("vanilla_memory", {}) is False


def test_substantive_event_types_kept():
    for t in (
        "death",
        "marriage",
        "title_acquired",
        "war_concluded",
        "artifact_acquired",
        "trait_gained",
    ):
        assert is_significant(t, {}) is True


def test_render_roll_entry_capitalizes_and_resolves_names():
    out = render_roll_entry(
        "alliance_formed", {"ally_character_id": 100}, names_map={100: "Harold"}
    )
    assert out == "Formed alliance with Harold"


def test_render_roll_entry_humanizes_unregistered_event_type():
    # ck3_chronicler-2etd: an event type with no renderer falls back to the
    # bare engine key; the roll must show "Unknown future event", not the
    # raw underscore-bearing "Unknown_future_event".
    out = render_roll_entry("unknown_future_event", {}, names_map={})
    assert out == "Unknown future event"


def test_render_roll_entry_strips_vanilla_memory_prefix():
    out = render_roll_entry("vanilla_memory", {"memory_type": "battle_won_memory"}, names_map={})
    assert out == "Won a battle"


def test_build_event_roll_curates_and_renders():
    events = [
        {"date": "1066.1.1", "type": "travel", "payload": {"from_location": 1, "to_location": 2}},
        {"date": "1066.2.2", "type": "marriage", "payload": {"spouse_character_id": 100}},
        {
            "date": "1066.3.3",
            "type": "vanilla_memory",
            "payload": {"memory_type": "became_friends"},
        },
    ]
    rows = build_event_roll(events, names_map={100: "Edith"})
    assert rows == [EventRollRow(date="1066.2.2", description="Married Edith")]


def test_build_event_roll_empty_when_all_noise():
    events = [{"date": "1066.1.1", "type": "travel", "payload": {}}]
    assert build_event_roll(events, names_map={}) == []
