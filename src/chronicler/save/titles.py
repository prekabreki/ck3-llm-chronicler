"""Post-parse title-holding queries (ck3_chronicler-27ov.36 / M-S4).

Read-only helpers over a parsed :class:`SaveSnapshot` — who holds which
titles, the primary title, and a tier-sorted list. Split out of parse.py;
re-exported there for compatibility.
"""

from __future__ import annotations

from chronicler.save.snapshot import SaveSnapshot, TitleSnapshot


def get_titles_held_by(snap: SaveSnapshot, character_id: int) -> tuple[TitleSnapshot, ...]:
    """Return the titles directly held by a character in this snapshot.

    Direct holdings only — does not transitively include subordinate
    titles a vassal holds on the character's behalf.
    """
    # ck3_chronicler-hk9i: uses the title_holders reverse index built at
    # parse time when present (the common case); falls back to a full
    # snapshot scan for SaveSnapshot instances constructed by hand in
    # tests that pre-date the index.
    if snap.title_holders:
        return tuple(
            snap.titles[tid]
            for tid in snap.title_holders.get(character_id, frozenset())
            if tid in snap.titles
        )
    return tuple(t for t in snap.titles.values() if t.holder_id == character_id)


# Tier ranking for ``get_primary_title_held_by``. Higher = grander. The
# "other" bucket (non-tiered titles, e.g. titular court positions that
# slipped into landed_titles without a b_/c_/d_/k_/e_ prefix) ranks
# lowest so it's only chosen if nothing tiered is held. This is the
# single canonical table — :mod:`chronicler.save.worldbuilding` imports
# it as ``_TIER_RANK`` (27ov.13 removed its duplicated copy).
_PRIMARY_TIER_RANK: dict[str, int] = {
    "barony": 0,
    "county": 1,
    "duchy": 2,
    "kingdom": 3,
    "empire": 4,
    "other": -1,
}


def get_primary_title_held_by(snap: SaveSnapshot, character_id: int) -> TitleSnapshot | None:
    """Return the character's highest-tier directly-held title, or None.

    Ranks: empire > kingdom > duchy > county > barony > other. Ties are
    broken by smallest ``title_id`` for deterministic ordering across
    runs. Returns ``None`` for landless characters or characters absent
    from the snapshot's title hierarchy.

    Distinct from :func:`chronicler.save.worldbuilding._primary_realm_with_fallback`
    in that this is *only* the directly-held title — no de_jure walk to
    a parent kingdom and no family fallback. The Biographies surface
    wants "the title this person actually held"; the world-context
    block wants "which kingdom-tier realm they belong to". Different
    questions, different helpers.
    """
    held = get_held_titles_sorted(snap, character_id)
    return held[0] if held else None


def get_held_titles_sorted(snap: SaveSnapshot, character_id: int) -> list[TitleSnapshot]:
    """Return ALL titles a character directly holds, grandest-first.

    Ranked empire > kingdom > duchy > county > barony > other, ties broken
    by smallest ``title_id`` for deterministic ordering. The head is the
    same title :func:`get_primary_title_held_by` returns.

    Unlike that single-title helper, this surfaces the *whole* holding so
    the Biographies sidebar/overview can list every title held at death —
    a multi-kingdom ruler reads as more than "King of <one kingdom>", and a
    custom runtime kingdom (which gets a high ``title_id`` and so always
    lost the primary tie-break) is no longer dropped (ck3_chronicler-9ngy).
    Returns ``[]`` for landless / unobserved characters.
    """
    held = list(get_titles_held_by(snap, character_id))
    held.sort(key=lambda t: (-_PRIMARY_TIER_RANK.get(t.tier, -1), t.title_id))
    return held
