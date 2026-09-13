"""Unit tests for chronicler.heraldry.resolver — character → CoA resolution."""

from __future__ import annotations

from typing import Any

from chronicler.heraldry import resolve_character_coa


def _build_save(
    *,
    char_id: int = 100,
    house_id: int = 200,
    coa_id: int = 300,
    coa: dict[str, Any] | None = None,
    char_in_dead: bool = False,
    char_in_dead_prunable: bool = False,
) -> dict[str, Any]:
    """Synthesise a minimal rakaly-shaped save with one character +
    house + CoA, wired together."""
    coa_payload = coa or {
        "pattern": "pattern_solid.dds",
        "color1": "red",
        "color2": "yellow",
    }
    char_record = {"first_name": "X", "dynasty_house": house_id}
    save: dict[str, Any] = {
        "living": {},
        "dead_unprunable": {},
        "dynasties": {"dynasty_house": {str(house_id): {"coat_of_arms_id": coa_id}}},
        "coat_of_arms": {"coat_of_arms_manager_database": {str(coa_id): coa_payload}},
    }
    if char_in_dead_prunable:
        save["characters"] = {"dead_prunable": {str(char_id): char_record}}
    elif char_in_dead:
        save["dead_unprunable"] = {str(char_id): char_record}
    else:
        save["living"] = {str(char_id): char_record}
    return save


def test_resolve_character_coa_walks_full_chain() -> None:
    coa = {"pattern": "pattern_solid.dds", "color1": "red", "color2": "blue"}
    save = _build_save(char_id=100, house_id=200, coa_id=300, coa=coa)
    assert resolve_character_coa(save, 100) == coa


def test_resolve_character_coa_finds_dead_character() -> None:
    """Dead-but-not-yet-pruned characters resolve too."""
    coa = {"pattern": "pattern_solid.dds"}
    save = _build_save(char_id=100, char_in_dead=True, coa=coa)
    assert resolve_character_coa(save, 100) == coa


def test_resolve_character_coa_finds_dead_prunable_character() -> None:
    """Freshly-dead characters (in characters.dead_prunable) resolve too."""
    coa = {"pattern": "pattern_solid.dds"}
    save = _build_save(char_id=100, char_in_dead_prunable=True, coa=coa)
    assert resolve_character_coa(save, 100) == coa


def test_resolve_character_coa_returns_none_for_unknown_character() -> None:
    save = _build_save(char_id=100)
    assert resolve_character_coa(save, 99999) is None


def test_resolve_character_coa_returns_none_when_no_dynasty_house() -> None:
    save = {"living": {"100": {"first_name": "X"}}}  # no dynasty_house key
    assert resolve_character_coa(save, 100) is None


def test_resolve_character_coa_returns_none_when_house_missing_coa_id() -> None:
    """House with no coat_of_arms_id and no dynasty link returns None."""
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {"dynasty_house": {"200": {"head_of_house": 100}}},
    }
    assert resolve_character_coa(save, 100) is None


def test_resolve_character_coa_returns_none_when_coa_id_not_in_database() -> None:
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {"dynasty_house": {"200": {"coat_of_arms_id": 999}}},
        "coat_of_arms": {"coat_of_arms_manager_database": {"300": {"pattern": "x"}}},
    }
    assert resolve_character_coa(save, 100) is None


def test_resolve_character_coa_returns_none_with_empty_save() -> None:
    assert resolve_character_coa({}, 1) is None


# --- Precedence: living → dead_unprunable → characters.dead_prunable ---


def test_resolve_character_coa_precedence_living_over_dead_prunable() -> None:
    """living wins when the same ID appears in multiple buckets."""
    coa = {"pattern": "living_coa.dds"}
    save = _build_save(char_id=100, coa=coa, char_in_dead_prunable=True)
    save["dead_unprunable"] = {"100": {"dynasty_house": 200}}
    assert resolve_character_coa(save, 100) == coa


def test_resolve_character_coa_precedence_dead_unprunable_over_dead_prunable() -> None:
    """dead_unprunable takes priority over characters.dead_prunable."""
    coa_dead = {"pattern": "dead_unprunable_coa.dds"}
    coa_prunable = {"pattern": "dead_prunable_coa.dds"}
    save = _build_save(char_id=100, char_in_dead=True, coa=coa_dead)
    save["characters"] = {"dead_prunable": {"100": {"dynasty_house": 200}}}
    save["coat_of_arms"]["coat_of_arms_manager_database"] = {
        "300": coa_dead,
        "301": coa_prunable,
    }
    save["dynasties"]["dynasty_house"]["200"]["coat_of_arms_id"] = 300
    save["dynasties"]["dynasty_house"]["201"] = {"coat_of_arms_id": 301}
    # Tie house_id 200 to dead_unprunable, 201 to dead_prunable
    save["dead_unprunable"]["100"]["dynasty_house"] = 200
    save["characters"]["dead_prunable"]["100"]["dynasty_house"] = 201
    assert resolve_character_coa(save, 100) == coa_dead


# --- ck3_chronicler-ytn: dynasty-level CoA fallback ---


def test_resolve_character_coa_falls_back_to_dynasty_when_house_missing_coa_id() -> None:
    """Live evidence: Isleifur's house dynn_Haukadalur (2335) has no
    coat_of_arms_id, but its parent dynasty (2335) does — CK3 inherits
    the dynasty arms when the house declares no override."""
    dynasty_coa = {
        "pattern": "pattern_diagonal_split_01.dds",
        "color1": "white",
        "color2": "blue",
        "colored_emblem": {
            "color1": "white",
            "color2": "white",
            "color3": "black",
            "texture": "ce_lion_rampant.dds",
            "mask": [0, 2, 0],
        },
    }
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {
            "dynasty_house": {"200": {"dynasty": 400}},  # no coat_of_arms_id
            "dynasties": {"400": {"coat_of_arms_id": 500}},
        },
        "coat_of_arms": {"coat_of_arms_manager_database": {"500": dynasty_coa}},
    }
    assert resolve_character_coa(save, 100) == dynasty_coa


def test_resolve_character_coa_house_override_wins_over_dynasty() -> None:
    """When the house declares its own arms (cadet branch override),
    we use those even if the dynasty also has arms."""
    house_coa = {"pattern": "house_override.dds"}
    dynasty_coa = {"pattern": "dynasty_default.dds"}
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {
            "dynasty_house": {"200": {"coat_of_arms_id": 300, "dynasty": 400}},
            "dynasties": {"400": {"coat_of_arms_id": 500}},
        },
        "coat_of_arms": {"coat_of_arms_manager_database": {"300": house_coa, "500": dynasty_coa}},
    }
    assert resolve_character_coa(save, 100) == house_coa


def test_resolve_character_coa_returns_none_when_dynasty_link_missing() -> None:
    """House has no coat_of_arms_id and no dynasty link — give up."""
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {"dynasty_house": {"200": {"head_of_house": 100}}},
    }
    assert resolve_character_coa(save, 100) is None


def test_resolve_character_coa_returns_none_when_dynasty_record_missing() -> None:
    """House links to a dynasty that isn't in the dynasties table."""
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {
            "dynasty_house": {"200": {"dynasty": 400}},
            "dynasties": {},
        },
    }
    assert resolve_character_coa(save, 100) is None


def test_resolve_character_coa_returns_none_when_dynasty_also_has_no_coa() -> None:
    """Neither house nor dynasty has a coat_of_arms_id — give up."""
    save = {
        "living": {"100": {"dynasty_house": 200}},
        "dynasties": {
            "dynasty_house": {"200": {"dynasty": 400}},
            "dynasties": {"400": {"name": "dyn_Haukadalur"}},
        },
    }
    assert resolve_character_coa(save, 100) is None


# --- ck3_chronicler-wo20: colored_emblem array normalization ---


def test_resolve_normalizes_array_under_singular_colored_emblem() -> None:
    """rakaly may yield ``colored_emblem: [list]`` when a node carries
    multiple charges. resolve_character_coa rewrites this to the canonical
    ``colored_emblems`` (plural) so frontend, PDF export, and texture walkers
    see one shape."""
    raw_coa = {
        "pattern": "pattern_solid.dds",
        "colored_emblem": [
            {"texture": "ce_lion.dds"},
            {"texture": "ce_eagle.dds"},
        ],
    }
    save = _build_save(coa=raw_coa)
    resolved = resolve_character_coa(save, 100)
    assert resolved is not None
    assert "colored_emblem" not in resolved
    assert resolved["colored_emblems"] == [
        {"texture": "ce_lion.dds"},
        {"texture": "ce_eagle.dds"},
    ]


def test_resolve_normalize_does_not_mutate_source_save() -> None:
    """Normalisation deep-copies; the raw save dict stays untouched so a
    second resolve still sees the original shape."""
    raw_coa = {
        "pattern": "x.dds",
        "colored_emblem": [{"texture": "ce_a.dds"}],
    }
    save = _build_save(coa=raw_coa)
    resolve_character_coa(save, 100)
    assert raw_coa == {
        "pattern": "x.dds",
        "colored_emblem": [{"texture": "ce_a.dds"}],
    }


def test_resolve_normalize_merges_array_into_existing_plural_emblems() -> None:
    """If both the singular-as-list and the canonical plural exist, merge
    rather than clobber so no charges are lost."""
    raw_coa = {
        "pattern": "x.dds",
        "colored_emblem": [{"texture": "ce_a.dds"}],
        "colored_emblems": [{"texture": "ce_b.dds"}],
    }
    resolved = resolve_character_coa(_build_save(coa=raw_coa), 100)
    assert resolved is not None
    assert "colored_emblem" not in resolved
    assert resolved["colored_emblems"] == [
        {"texture": "ce_b.dds"},
        {"texture": "ce_a.dds"},
    ]


def test_resolve_normalize_recurses_into_subs() -> None:
    """The array-under-singular shape can appear on nested sub-shields
    too — normalise everywhere."""
    raw_coa = {
        "pattern": "outer.dds",
        "sub": {
            "pattern": "middle.dds",
            "colored_emblem": [{"texture": "ce_nested.dds"}],
        },
        "subs": [
            {
                "pattern": "side.dds",
                "colored_emblem": [{"texture": "ce_side.dds"}],
            },
        ],
    }
    resolved = resolve_character_coa(_build_save(coa=raw_coa), 100)
    assert resolved is not None
    assert "colored_emblem" not in resolved["sub"]
    assert resolved["sub"]["colored_emblems"] == [{"texture": "ce_nested.dds"}]
    assert "colored_emblem" not in resolved["subs"][0]
    assert resolved["subs"][0]["colored_emblems"] == [{"texture": "ce_side.dds"}]
