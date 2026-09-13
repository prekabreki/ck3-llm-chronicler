"""Player-identity → registry-overview write, shared by save-tail + importer.

ck3_chronicler-27ov.39 (audit M-I3): the resolve-identity / resolve-stats
pair and the 11-kwarg :func:`update_campaign_overview` call sequence used
to exist three times — twice in ``save/ingest.py`` (the per-tick
_persist_per_save_state tail and the cij auto-import seed path) and once
inlined in ``save/importer.py`` "to avoid the circular import (ingest
imports importer)". The wdhe stats addition already had to land in all
three. This is a leaf module (imports only parse / localization /
registry) so both sides can import it without a cycle.
"""

from __future__ import annotations

from pathlib import Path

from chronicler.db.registry import update_campaign_overview
from chronicler.save.ck3_names import resolve_house_name
from chronicler.save.parse import SaveSnapshot


def resolve_player_identity(
    snap: SaveSnapshot,
) -> tuple[int | None, str | None, str | None, str | None]:
    """ck3_chronicler-cqo: read the player character's identity directly
    from the SaveSnapshot for the registry-overview write.

    Returns ``(player_character_id, first_name, nickname, house_name)``.
    Pulls from ``snap.characters[player_id]`` for the names + nickname
    (post-decoded by parse.decode_ck3_name) and from
    ``snap.houses_lookup[dynasty_house_id]`` for the house name —
    exactly the same resolution _refresh_tracked_characters uses, so
    the registry and the per-campaign DB are guaranteed consistent.

    Reading from snap (rather than the DB) is deliberate: it removes
    the order-dependency between this call and
    _refresh_tracked_characters. Whichever order they run in, both
    write identical values for tick N. Importantly, this avoids a
    one-tick lag in the registry (reading the DB before refresh would
    give tick N-1 identity), which would visibly mis-render the
    Library card on the same tick the player succeeds to an heir."""
    pid = snap.player_character_id
    if pid is None:
        return None, None, None, None
    char = snap.characters.get(pid)
    if char is None:
        return pid, None, None, None
    raw_house = (
        snap.houses_lookup.get(char.dynasty_house_id) if char.dynasty_house_id is not None else None
    )
    # ck3_chronicler-6d1c: houses_lookup gives the localization key
    # (``dynn_Barcelona``); resolve_house_name prefers CK3's dynasty-name
    # loca (authoritative diacritics, 6rgx) and falls back to the
    # prefix-strip + diacritic-decode heuristic, so the byline reads
    # ``House Barcelona`` instead of ``House dynn_Barcelona``.
    culture_name = (
        snap.cultures_lookup.get(char.culture_id) if char.culture_id is not None else None
    )
    house_name = resolve_house_name(raw_house, culture=culture_name)
    return pid, char.first_name, char.nickname, house_name


def resolve_player_stats(
    snap: SaveSnapshot,
) -> tuple[float | None, float | None, float | None, float | None]:
    """ck3_chronicler-wdhe: read the player's gold/prestige(lifetime)/
    piety + dynasty renown for the registry-overview write.

    Returns ``(gold, prestige_lifetime, piety, dynasty_renown)``. All
    four are None when the player record can't be resolved or when CK3
    didn't carry the field (older saves, adventurer mode lacking a
    dynasty, etc.). The player char's currencies live on
    ``CharacterSnapshot``; renown comes from the dynasty record one
    hop past the player's house — house_to_dynasty[dynasty_house_id]
    keys into ``dynasties_renown``. Adventurer characters have no
    dynasty_house_id and yield None for renown.
    """
    pid = snap.player_character_id
    if pid is None:
        return None, None, None, None
    char = snap.characters.get(pid)
    if char is None:
        return None, None, None, None
    renown: float | None = None
    if char.dynasty_house_id is not None:
        did = snap.house_to_dynasty.get(char.dynasty_house_id)
        if did is not None:
            renown = snap.dynasties_renown.get(did)
    return char.gold, char.prestige_lifetime, char.piety_lifetime, renown


def apply_campaign_overview_from_snap(
    campaign_id: str,
    snap: SaveSnapshot,
    *,
    registry: Path | None,
) -> None:
    """Snapshot per-campaign identity + stats onto the registry row.

    ck3_chronicler-cqo/wdhe: the Library page reads these denormalised
    columns so each card is one registry query; archived campaigns
    retain a frozen byline. Identity and stats come from the snap
    directly so they stay consistent with what the caller wrote to the
    per-campaign DB in the same tick (see resolve_player_identity).
    """
    pid, player_name, player_nickname, house_name = resolve_player_identity(snap)
    gold, prestige_lifetime, piety, renown = resolve_player_stats(snap)
    update_campaign_overview(
        campaign_id,
        ck3_playthrough_id=snap.playthrough_id or None,
        bookmark_date=snap.bookmark_date,
        current_in_game_date=snap.current_date,
        current_player_character_id=pid,
        current_player_name=player_name,
        current_player_nickname=player_nickname,
        current_house_name=house_name,
        current_player_gold=gold,
        current_player_prestige_lifetime=prestige_lifetime,
        current_player_piety=piety,
        current_dynasty_renown=renown,
        registry=registry,
    )
