"""Map vanilla CK3 lifestyle perk engine keys to their lifestyle key.

ck3_chronicler-9p2g (replaces ej1a — focus tracking was impossible
because modern CK3 saves don't store ``focus`` as a discrete field).
The diff layer uses :func:`lifestyle_of_perk` to detect a character
committing to a lifestyle (first perk in any of its three trees) and
emit a single ``LifestyleCommittedEvent`` per lifestyle per character.

The map is hand-maintained from vanilla CK3 wiki + smoke-session evidence
since rakaly output doesn't carry tree affiliation. It is intentionally
INCOMPLETE — unknown perks return ``None`` from :func:`lifestyle_of_perk`
and the diff layer silently skips them, so a missing entry degrades to
"no event" rather than "wrong event". Coverage grows as user saves
surface new perks; each new entry should be commented with provenance.

Vanilla CK3 1.18+ lifestyles (the LIFESTYLE keys CK3 itself uses on
``alive_data.lifestyle_xp`` — confirmed against the 2026-05-17 Genji
adventurer save):
    - martial_lifestyle (chivalry / gallantry / strategist trees)
    - diplomacy_lifestyle (august / diplomat / family_hierarch trees)
    - stewardship_lifestyle (architect / fiscal / organizer trees)
    - intrigue_lifestyle (schemer / seducer / torturer trees)
    - learning_lifestyle (medicine / scholar / theology trees)
    - wanderer_lifestyle (Roads to Power DLC, adventurer-only)

The renderer derives the prose name by stripping the ``_lifestyle``
suffix ("committed to the martial lifestyle"). Future patches that
rename trees or add new lifestyles only require additions here — the
schema and diff layer are tree-agnostic.
"""

from __future__ import annotations

# Lifestyle keys we recognise. Used by the import-time invariant in
# tests/unit/test_lifestyle_perks.py to catch typos in
# :data:`LIFESTYLE_FOR_PERK` values, and by the renderer to validate
# incoming events before phrasing.
KNOWN_LIFESTYLES: frozenset[str] = frozenset(
    {
        "martial_lifestyle",
        "diplomacy_lifestyle",
        "stewardship_lifestyle",
        "intrigue_lifestyle",
        "learning_lifestyle",
        "wanderer_lifestyle",
    }
)


# Perk engine key -> lifestyle key. Seeded from 2026-05-17 Genji smoke
# evidence (rgay) + rgay test fixtures + vanilla CK3 wiki cross-reference;
# extend organically as live saves surface new perks. Each entry should
# carry a short provenance comment so a future reader can audit the source.
#
# A note on coverage: the goal is to cover the FINAL (capstone) perk of
# each tree, plus any mid-tree perks we've already seen in real saves.
# A character usually buys the capstone before switching trees, so
# covering capstones catches most "committed to <lifestyle>" beats. The
# diff layer silently skips unmapped perks, so even partial coverage
# remains correct-by-omission.
LIFESTYLE_FOR_PERK: dict[str, str] = {
    # ============== MARTIAL ==============
    # chivalry tree — Genji 2026-05-17 ruler-designer start
    "bellum_justum_perk": "martial_lifestyle",
    # strategist tree — Genji 2026-05-17 ruler-designer start
    "parthian_tactics_perk": "martial_lifestyle",
    # capstones across martial trees (vanilla wiki cross-reference)
    "overseer_perk": "martial_lifestyle",
    "chivalric_brilliance_perk": "martial_lifestyle",
    "tomb_raider_perk": "martial_lifestyle",
    "gallant_perk": "martial_lifestyle",
    # ============== INTRIGUE ==============
    # schemer tree — rgay test fixture
    "schemer_perk": "intrigue_lifestyle",
    "intrigue_perk": "intrigue_lifestyle",
    # vanilla intrigue capstones
    "seducer_perk": "intrigue_lifestyle",
    "torturer_perk": "intrigue_lifestyle",
    "underhanded_perk": "intrigue_lifestyle",
    # ============== LEARNING ==============
    # mystic-equivalent — rgay test fixture
    "mystic_perk": "learning_lifestyle",
    # vanilla learning capstones
    "scholar_perk": "learning_lifestyle",
    "medicus_perk": "learning_lifestyle",
    "theologian_perk": "learning_lifestyle",
    # ============== DIPLOMACY ==============
    "august_perk": "diplomacy_lifestyle",
    "diplomat_perk": "diplomacy_lifestyle",
    "family_first_perk": "diplomacy_lifestyle",
    "groomed_to_rule_perk": "diplomacy_lifestyle",
    # ============== STEWARDSHIP ==============
    "architect_perk": "stewardship_lifestyle",
    "fiscal_perk": "stewardship_lifestyle",
    "organizer_perk": "stewardship_lifestyle",
    "avaricious_perk": "stewardship_lifestyle",
    # ============== WANDERER (Roads to Power DLC) ==============
    # RtP adventurer lifestyle — left empty until live RtP-perk evidence
    # surfaces. The key is registered in KNOWN_LIFESTYLES so the renderer
    # can phrase events when entries are added.
}


def lifestyle_of_perk(perk_key: str) -> str | None:
    """Return the lifestyle key (e.g. ``'martial_lifestyle'``) for
    ``perk_key``, or ``None`` if the perk isn't in the static map.

    Callers (the diff layer) should treat ``None`` as "skip — we don't
    know what lifestyle this perk belongs to, don't fire an event with
    a wrong or empty label".
    """
    if not perk_key:
        return None
    return LIFESTYLE_FOR_PERK.get(perk_key)
