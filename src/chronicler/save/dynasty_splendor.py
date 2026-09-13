"""ck3_chronicler-ei8t: dynasty splendor tier derivation.

CK3 emits a "level of splendor increased" notification when a dynasty
crosses a tier boundary. Tier is a function of lifetime accumulated
renown (``dynasties.<id>.prestige.accumulated``) — the engine derives
it via a fixed threshold table and exposes the tier name via the
``dynasty_prestige_level`` getter and the
``game_concept_dynasty_prestige_level_N`` localization keys.

The threshold values themselves are not in CK3's data files
(``common/defines/00_defines.txt``, ``common/script_values/``); they
appear to be engine-hardcoded. The well-documented L1..L6 curve below
is the community-published table, anchored on the live 2026-05-14
Sleggja crossing at accumulated=1010.485 (tier 0 -> 1). Engine
supports up to tier 10 (``max_dynasty_prestige_level = 10`` in
``00_basic_values.txt``); thresholds for tiers 7-10 are undocumented,
and a single playthrough is unlikely to reach them. We cap at tier 6
rather than guessing — if a future smoke campaign observes a
discrepancy at tier 7+, extend both tables together.

Tier names sourced from
``localization/english/game_concepts_l_english.yml`` lines 1270-1276,
keys ``game_concept_dynasty_prestige_level_0..6``.
"""

from __future__ import annotations

SPLENDOR_THRESHOLDS: tuple[float, ...] = (
    1_000.0,
    5_000.0,
    15_000.0,
    35_000.0,
    65_000.0,
    105_000.0,
)
"""Lifetime renown required to enter tier N (1-indexed). Tier 0 is
the state below the first threshold."""

SPLENDOR_TIER_NAMES: tuple[str, ...] = (
    "Base Origins",  # 0 — below 1,000 renown
    "Obscure",  # 1
    "Insignificant",  # 2
    "Noteworthy",  # 3
    "Reputable",  # 4
    "Well-known",  # 5
    "Significant",  # 6
)
"""One entry per tier (0..len(SPLENDOR_THRESHOLDS)). Indexed directly
by the derived tier integer."""


def derive_splendor_tier(accumulated: float) -> int:
    """Return the splendor tier for a given lifetime renown.

    Tier 0 = below first threshold. Tier N = at or above the N-th
    threshold. Result is bounded by ``len(SPLENDOR_THRESHOLDS)`` so
    crossings into engine-tiers 7+ (undocumented thresholds) cap at
    tier 6 rather than guessing."""
    if accumulated <= 0:
        return 0
    tier = 0
    for threshold in SPLENDOR_THRESHOLDS:
        if accumulated >= threshold:
            tier += 1
        else:
            break
    return tier
