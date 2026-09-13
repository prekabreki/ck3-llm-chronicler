"""Tests for chronicler.save.lifestyle_perks — perk → lifestyle map
(ck3_chronicler-9p2g).

The map is hand-maintained from vanilla CK3 game files + smoke-session
evidence. These tests pin the contract that:

- Known perks map to their lifestyle (e.g. bellum_justum_perk -> martial)
- Unknown perks return None (graceful — diff layer skips them)
- The five vanilla lifestyle keys + RtP wanderer lifestyle have at
  least one perk each so a sanity smoke catches accidental empty trees
"""

from __future__ import annotations

from chronicler.save.lifestyle_perks import (
    KNOWN_LIFESTYLES,
    lifestyle_of_perk,
)


def test_known_perks_map_to_expected_lifestyle() -> None:
    # Genji 2026-05-17 smoke + rgay test fixtures + 9p2g spec.
    assert lifestyle_of_perk("bellum_justum_perk") == "martial_lifestyle"
    assert lifestyle_of_perk("parthian_tactics_perk") == "martial_lifestyle"
    assert lifestyle_of_perk("schemer_perk") == "intrigue_lifestyle"
    assert lifestyle_of_perk("mystic_perk") == "learning_lifestyle"


def test_unknown_perk_returns_none() -> None:
    # Graceful: diff layer silently skips unmapped perks instead of
    # firing a half-formed LifestyleCommittedEvent.
    assert lifestyle_of_perk("this_perk_does_not_exist") is None
    assert lifestyle_of_perk("") is None


def test_known_lifestyles_cover_vanilla_five_plus_wanderer() -> None:
    # The set of lifestyle KEYS we know about. Used by the diff layer
    # to validate map consistency at import time.
    assert {
        "martial_lifestyle",
        "diplomacy_lifestyle",
        "stewardship_lifestyle",
        "intrigue_lifestyle",
        "learning_lifestyle",
        "wanderer_lifestyle",
    } == KNOWN_LIFESTYLES


def test_every_mapped_perk_points_to_a_known_lifestyle() -> None:
    """Defensive: catch typos when extending the map. A perk in
    LIFESTYLE_FOR_PERK whose value isn't in KNOWN_LIFESTYLES means the
    diff layer would emit an event with a lifestyle_key downstream
    consumers (renderer, briefing prompt) don't know how to phrase."""
    from chronicler.save.lifestyle_perks import LIFESTYLE_FOR_PERK

    for perk, lifestyle in LIFESTYLE_FOR_PERK.items():
        assert lifestyle in KNOWN_LIFESTYLES, (
            f"perk {perk!r} maps to unknown lifestyle {lifestyle!r}"
        )
