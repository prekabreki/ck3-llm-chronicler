"""Auto-track candidate policy (ck3_chronicler-27ov.36 / M-S4).

Decides which characters a campaign should auto-track from a parsed
:class:`SaveSnapshot` and the persisted campaign rules. Split out of
parse.py; re-exported there for compatibility.
"""

from __future__ import annotations

import json

from chronicler.save.snapshot import SaveSnapshot

# ck3_chronicler-gw16: default rules dict — every category on. Used
# when the campaign row has no auto_track_rules JSON yet (legacy
# default matching pre-gw16 behavior).
AUTO_TRACK_RULES_DEFAULT: dict[str, bool] = {
    "include_heirs": True,
    "include_spouses": True,
    "include_grandchildren": True,
    # County-tier vassals isn't yet implementable from the snapshot
    # (no character→liege relation lands today; see gw16.2). Default
    # off so flipping the flag in the future doesn't retroactively
    # change auto-track output for existing campaigns.
    "include_county_vassals": False,
}


def resolve_auto_track_rules(rules_json: str | None) -> dict[str, bool]:
    """Resolve a campaign's persisted auto-track rules JSON to a complete dict.

    Layers the persisted ``{flag: bool}`` JSON over
    :data:`AUTO_TRACK_RULES_DEFAULT` so callers always get concrete bools
    for every known flag. Malformed/absent JSON falls back to the
    defaults — a corrupted blob must never block auto-track.

    The single resolver shared by every entry point that turns a
    ``Campaign.auto_track_rules`` string into the ``rules=`` argument for
    :func:`auto_track_candidates` (the GUI's auto-track route and the
    ``chronicler auto-track`` CLI command). Before ck3_chronicler-vlw3
    the CLI passed no rules at all and silently tracked characters the
    campaign's rules excluded — divergence from the GUI for the same
    operation. Keeping the resolution here, beside the defaults and the
    consumer, means there is one rule-resolution truth, not one per
    surface.
    """
    out = dict(AUTO_TRACK_RULES_DEFAULT)
    if rules_json:
        try:
            parsed = json.loads(rules_json)
        except json.JSONDecodeError:
            return out
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                if isinstance(k, str) and isinstance(v, bool):
                    out[k] = v
    return out


_VASSAL_CHAIN_MAX_DEPTH = 5


def _resolve_county_vassals(
    snap: SaveSnapshot,
    player_id: int,
) -> list[int]:
    """ck3_chronicler-tmjn: enumerate the player's de-jure county vassals.

    Walks ``snap.titles`` to find county-tier titles whose de_jure_liege
    chain terminates at a duchy/kingdom/empire-tier title held by the
    player. Returns the deduplicated sorted list of those counties'
    holders, excluding the player themselves. Empty when the player
    holds no duchy/kingdom/empire title (no realm to walk).
    """
    player_realm: set[int] = {
        t.title_id
        for t in snap.titles.values()
        if t.holder_id == player_id and t.tier in ("duchy", "kingdom", "empire")
    }
    if not player_realm:
        return []

    vassals: set[int] = set()
    for county in snap.titles.values():
        if county.tier != "county":
            continue
        if county.holder_id is None or county.holder_id == player_id:
            continue
        liege_id = county.de_jure_liege_id
        seen: set[int] = set()
        for _ in range(_VASSAL_CHAIN_MAX_DEPTH):
            if liege_id is None or liege_id in seen:
                break
            seen.add(liege_id)
            if liege_id in player_realm:
                vassals.add(county.holder_id)
                break
            parent = snap.titles.get(liege_id)
            if parent is None:
                break
            liege_id = parent.de_jure_liege_id

    return sorted(vassals)


def auto_track_candidates(
    snap: SaveSnapshot,
    rules: dict[str, bool] | None = None,
) -> list[tuple[int, str]]:
    """Suggest tracked-character IDs from a snapshot: player + family.

    Returns (character_id, note) pairs ready for
    :func:`chronicler.db.registry.add_tracked_character`. The caller
    decides whether to add them all or filter.

    The player is always included. The remaining categories are gated
    on ``rules`` (ck3_chronicler-gw16):

    - ``include_heirs`` (default True): the player's children.
    - ``include_spouses`` (default True): primary spouse + all spouses.
    - ``include_county_vassals`` (default False): the player's de-jure
      county vassals (gw16.2 / tmjn). See :func:`_resolve_county_vassals`
      for the walk semantics. De-facto-only vassals are not included
      (TitleSnapshot lacks de_facto_liege today).

    Parents (mother + father) are always included regardless of rules
    — there's no checkbox for them in the UI, and dropping them would
    change the auto-track shape for callers who don't yet pass rules.
    """
    if snap.player_character_id is None:
        return []
    effective = AUTO_TRACK_RULES_DEFAULT | (rules or {})
    out: list[tuple[int, str]] = []
    seen: set[int] = set()

    def _add(cid: int | None, note: str) -> None:
        if cid is None or cid in seen:
            return
        seen.add(cid)
        out.append((cid, note))

    player = snap.characters.get(snap.player_character_id)
    _add(snap.player_character_id, "player")
    if player is not None:
        if effective.get("include_spouses", True):
            _add(player.family.primary_spouse, "spouse")
            for sp in player.family.spouses:
                _add(sp, "spouse")
        if effective.get("include_heirs", True):
            for ch in player.family.children:
                _add(ch, "child")
        if effective.get("include_grandchildren", True):
            for ch in player.family.children:
                child_snap = snap.characters.get(ch)
                if child_snap is not None:
                    for gc in child_snap.family.children:
                        _add(gc, "grandchild")
        # Parents are not gated — they're part of the player's "core
        # narrative orbit" and have no UI checkbox to opt out.
        _add(player.family.mother, "mother")
        _add(player.family.father, "father")
    if effective.get("include_county_vassals", False):
        for vassal_id in _resolve_county_vassals(snap, snap.player_character_id):
            _add(vassal_id, "vassal")
    return out
