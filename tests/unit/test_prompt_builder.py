"""Tests for chronicler.narrative.prompt_builder.

The point of ck3_chronicler-05rs: prompt assembly is now a pure function
of plain structs. These tests build a :class:`BiographyInputs` by hand
and call :func:`build_briefing` — no database, no session, no provider.
That's the whole win, exercised directly.
"""

from __future__ import annotations

import json

from chronicler.narrative.prompt_builder import (
    BiographyInputs,
    Briefing,
    CharacterSnapshot,
    EventSnapshot,
    build_briefing,
)


def _char(**overrides: object) -> CharacterSnapshot:
    base = dict(
        ck3_id=100,
        first_name="Sigtrygg",
        dynasty_name="Hardrada",
        nickname=None,
        female=False,
        birth_date="1066-01-01",
        death_date="1120-06-02",
        culture="norse",
        faith="asatru",
    )
    base.update(overrides)
    return CharacterSnapshot(**base)  # type: ignore[arg-type]


def _inputs(character: CharacterSnapshot, **overrides: object) -> BiographyInputs:
    base = dict(
        character=character,
        events=[],
        names_map={},
        name_genders={},
        related_snapshots={},
    )
    base.update(overrides)
    return BiographyInputs(**base)  # type: ignore[arg-type]


def test_build_briefing_assembles_all_three_fields_without_db() -> None:
    """The headline guarantee: a complete Briefing from plain structs.

    Issue #19 dropped the fourth field (``system_prompt``): it was read
    from an in-repo template every provider then discarded. The real
    register is assembled from the prose dir at request-construction
    time, which keeps this builder free of filesystem reads.
    """
    briefing = build_briefing(_inputs(_char()), mode="scene_setter")

    assert isinstance(briefing, Briefing)
    assert briefing.kind == "biography"
    assert briefing.prompt_version == "biography_v3"
    assert not hasattr(briefing, "system_prompt")
    assert "Known names: Sigtrygg Hardrada" in briefing.user_prompt
    assert "Gender: man" in briefing.user_prompt
    assert "Culture: norse; Faith: asatru" in briefing.user_prompt
    assert "Recorded events: (none)" in briefing.user_prompt


def test_mode_selects_prompt_version() -> None:
    assert build_briefing(_inputs(_char()), mode="scene_setter").prompt_version == "biography_v3"
    assert build_briefing(_inputs(_char()), mode="woven").prompt_version == "biography_v5"
    # Unknown mode falls back to the scene-setter (v3) tuple.
    assert build_briefing(_inputs(_char()), mode="nonsense").prompt_version == "biography_v3"


def test_kind_is_biography_without_world_context() -> None:
    assert build_briefing(_inputs(_char()), mode="scene_setter").kind == "biography"


def test_kind_is_woven_when_world_context_renders() -> None:
    region = json.dumps(
        {
            "region_empire_name": "Scandinavia",
            "self_realm": {"kingdom_name": "Norway", "culture": "norse", "faith": "asatru"},
        }
    )
    briefing = build_briefing(
        _inputs(_char(region_summary_json=region)),
        mode="scene_setter",
    )
    assert briefing.kind == "biography_woven"
    assert "World context (Scandinavia" in briefing.user_prompt
    assert "Self: Norway — norse, asatru." in briefing.user_prompt


def test_relation_labels_decorate_the_glossary() -> None:
    """Relation inference moved out of the read phase into build_briefing
    (ck3_chronicler-05rs). The glossary line for a parent should carry
    its label, derived from family_data — no DB involved."""
    family = json.dumps(
        {
            "family_data": {
                "father": {"id": 200, "name": "Harald"},
                "primary_spouse": {"id": 300, "name": "Gyda"},
            }
        }
    )
    inputs = _inputs(
        _char(raw_record_json=family),
        names_map={100: "Sigtrygg", 200: "Harald", 300: "Gyda"},
    )
    user_prompt = build_briefing(inputs, mode="scene_setter").user_prompt

    assert "200 = Harald (father)" in user_prompt
    assert "300 = Gyda (spouse)" in user_prompt
    # The subject itself is never listed in its own glossary.
    assert "100 = Sigtrygg" not in user_prompt


def test_relation_reverse_scan_via_related_snapshots() -> None:
    """A parent the subject's own record omits is back-derived from the
    related character's family_data — the u0eu reverse-scan, now driven
    by BiographyInputs.related_snapshots / .name_genders."""
    parent_record = json.dumps({"family_data": {"child": [{"id": 100, "name": "Sigtrygg"}]}})
    inputs = _inputs(
        _char(raw_record_json=None),
        names_map={100: "Sigtrygg", 200: "Harald"},
        name_genders={200: False},  # male -> father
        related_snapshots={200: parent_record},
    )
    user_prompt = build_briefing(inputs, mode="scene_setter").user_prompt
    assert "200 = Harald (father)" in user_prompt


def test_events_render_chronologically_in_user_prompt() -> None:
    events = [
        EventSnapshot(
            id=1,
            event_type="title_gained",
            event_date="1.1.1085",
            event_date_iso="1085-01-01",
            payload_json=json.dumps({"p": {"title_key": "k_norway"}}),
        ),
    ]
    user_prompt = build_briefing(_inputs(_char(), events=events), mode="scene_setter").user_prompt
    assert "Recorded events (chronological):" in user_prompt
    assert "[1085-01-01]" in user_prompt


def test_include_raw_record_appends_block_only_when_opted_in() -> None:
    raw = json.dumps({"some": "extra detail"})
    char = _char(raw_record_json=raw)

    without = build_briefing(_inputs(char), mode="scene_setter", include_raw_record=False)
    assert "Full character record from save" not in without.user_prompt

    with_raw = build_briefing(_inputs(char), mode="scene_setter", include_raw_record=True)
    assert "Full character record from save" in with_raw.user_prompt
    assert raw in with_raw.user_prompt
