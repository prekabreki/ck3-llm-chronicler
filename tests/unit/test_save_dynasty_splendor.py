"""ck3_chronicler-ei8t: derive_splendor_tier — map lifetime accumulated
renown to a splendor tier integer. The threshold table is documented
in the module; these tests pin every boundary.

Curve is the CK3 vanilla 6-threshold curve (L1..L6); engine supports up
to tier 10 but those thresholds are undocumented. We cap at tier 6;
real campaigns rarely exceed this in a single playthrough."""

from __future__ import annotations

import pytest

from chronicler.save.dynasty_splendor import (
    SPLENDOR_THRESHOLDS,
    SPLENDOR_TIER_NAMES,
    derive_splendor_tier,
)


def test_zero_renown_is_tier_zero() -> None:
    assert derive_splendor_tier(0.0) == 0


def test_just_below_first_threshold_is_tier_zero() -> None:
    assert derive_splendor_tier(999.99) == 0


def test_first_threshold_is_tier_one() -> None:
    """Sleggja crossed at accumulated=1010.485 and got 'level of
    splendor up'; the 1000 boundary is the live-verified anchor."""
    assert derive_splendor_tier(1000.0) == 1
    assert derive_splendor_tier(1010.485) == 1
    assert derive_splendor_tier(4999.99) == 1


@pytest.mark.parametrize(
    "accumulated,expected_tier",
    [
        (5000.0, 2),
        (14_999.99, 2),
        (15_000.0, 3),
        (34_999.99, 3),
        (35_000.0, 4),
        (64_999.99, 4),
        (65_000.0, 5),
        (104_999.99, 5),
        (105_000.0, 6),
    ],
)
def test_each_threshold_boundary(accumulated: float, expected_tier: int) -> None:
    assert derive_splendor_tier(accumulated) == expected_tier


def test_above_max_threshold_caps_at_top_tier() -> None:
    """Engine supports tiers 7-10 but their thresholds are
    undocumented; we cap at the last documented tier rather than
    guessing."""
    assert derive_splendor_tier(1_000_000.0) == len(SPLENDOR_THRESHOLDS)
    assert derive_splendor_tier(1e9) == len(SPLENDOR_THRESHOLDS)


def test_negative_renown_is_tier_zero() -> None:
    """Defensive: accumulated renown should never be negative, but if
    rakaly somehow emits one we don't want to crash or return -1."""
    assert derive_splendor_tier(-1.0) == 0


def test_tier_names_cover_thresholds_plus_zero() -> None:
    """Name table must have one entry per tier (0..N inclusive),
    where N = len(THRESHOLDS). Renderer relies on this invariant."""
    assert len(SPLENDOR_TIER_NAMES) == len(SPLENDOR_THRESHOLDS) + 1


def test_tier_names_match_ck3_vanilla_localization() -> None:
    """Names sourced from CK3's
    localization/english/game_concepts_l_english.yml lines 1270-1276,
    `game_concept_dynasty_prestige_level_N`. Pinned so a future curve
    change doesn't silently desync the name table."""
    assert SPLENDOR_TIER_NAMES[0] == "Base Origins"
    assert SPLENDOR_TIER_NAMES[1] == "Obscure"
    assert SPLENDOR_TIER_NAMES[6] == "Significant"
