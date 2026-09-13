"""Shared character-row hydration for offline import + live save-tail.

ck3_chronicler-vbfa: ``save/importer.py`` (offline backfill) and
``save/ingest.py`` (live save-tail refresh) used to compute the same
nine character-column derivations (culture, house, dynasty, faith,
plus the four JSON columns) side-by-side. Centralising here so future
changes to how a character's persisted columns are derived from a
:class:`SaveSnapshot` land in exactly one place.

The live save-tail path layers on its own extras after this (region
summary, great cause, CoA history) — those stay in ingest.py because
they're not relevant to offline import.
"""

from __future__ import annotations

import json
from typing import Any

from chronicler.heraldry import resolve_character_coa
from chronicler.save.ck3_names import resolve_dynasty_name
from chronicler.save.parse import (
    CharacterSnapshot,
    SaveSnapshot,
    extract_character_record,
    get_held_titles_sorted,
)


def hydrate_character_columns(
    *,
    snap: SaveSnapshot,
    char: CharacterSnapshot,
    cid: int,
    raw_save_data: dict[str, Any] | None,
    name_lookup: dict[int, str],
) -> dict[str, Any]:
    """Compute the kwargs for :func:`upsert_character` from a snapshot.

    Returns a dict containing all columns common to the import + save-
    tail paths: first_name, nickname, female, birth_date, death_date,
    dynasty_name, house_name, culture, faith, save_snapshot_json,
    coa_json, primary_title_json.

    ``raw_save_data`` is optional — when omitted (or None), the JSON
    columns derived from the raw save (save_snapshot_json, coa_json)
    are returned as None so the caller's upsert_character leaves any
    previously-persisted value intact.

    See ck3_chronicler-667 (house/culture/faith names), -45i (dynasty
    chain via house_to_dynasty), -6d1c (decode_house_name strips
    prefixes), -te8s (house-name fallback when dynasty chain breaks),
    -7ao (refresh CoA), -zx2l (primary-title persist).
    """
    culture_name = (
        snap.cultures_lookup.get(char.culture_id) if char.culture_id is not None else None
    )
    # ck3_chronicler-3kyg: the house → dynasty walk + te8s house-name
    # fallback lives in one place (resolve_dynasty_name) so it can't
    # drift between here and adoption.py.
    dynasty_name, house_name = resolve_dynasty_name(
        dynasty_house_id=char.dynasty_house_id,
        houses_lookup=snap.houses_lookup,
        house_to_dynasty=snap.house_to_dynasty,
        dynasties_lookup=snap.dynasties_lookup,
        culture=culture_name,
    )
    faith_name = snap.faiths_lookup.get(char.faith_id) if char.faith_id is not None else None

    raw_record_json: str | None = None
    coa_json: str | None = None
    if raw_save_data is not None:
        # Import / adoption path: resolve from the raw dict in-process.
        raw_record = extract_character_record(
            raw_save_data,
            cid,
            name_lookup=name_lookup,
            traits_lookup=snap.traits_lookup or None,
        )
        if raw_record is not None:
            raw_record_json = json.dumps(raw_record, separators=(",", ":"))
        coa = resolve_character_coa(raw_save_data, cid)
        if coa is not None:
            coa_json = json.dumps(coa, separators=(",", ":"))
    else:
        # ck3_chronicler-j86v save-tail path: extractions were resolved in
        # the parse worker and ride on the snapshot. Missing key => leave
        # the column None so upsert_character preserves any prior value.
        raw_record = snap.tracked_raw_records.get(cid)
        if raw_record is not None:
            raw_record_json = json.dumps(raw_record, separators=(",", ":"))
        coa = snap.tracked_coa.get(cid)
        if coa is not None:
            coa_json = json.dumps(coa, separators=(",", ":"))

    # ck3_chronicler-9ngy: persist EVERY directly-held title (grandest-first)
    # so the Biographies sidebar/overview can list all titles held at death,
    # not just the single primary. primary_title_json stays as held[0] for
    # back-compat (role grouping, existing consumers).
    held_titles = get_held_titles_sorted(snap, cid)
    held_titles_json = (
        json.dumps(
            [{"key": t.key, "name": t.name, "tier": t.tier} for t in held_titles],
            separators=(",", ":"),
        )
        if held_titles
        else None
    )
    primary_title = held_titles[0] if held_titles else None
    primary_title_json = (
        json.dumps(
            {
                "key": primary_title.key,
                "name": primary_title.name,
                "tier": primary_title.tier,
            },
            separators=(",", ":"),
        )
        if primary_title is not None
        else None
    )

    return {
        "ck3_id": cid,
        "first_name": char.first_name,
        "nickname": char.nickname,
        "female": char.female,
        "birth_date": char.birth_date,
        "death_date": char.death_date,
        "dynasty_name": dynasty_name,
        "house_name": house_name,
        "culture": culture_name,
        "faith": faith_name,
        "save_snapshot_json": raw_record_json,
        "coa_json": coa_json,
        "primary_title_json": primary_title_json,
        "held_titles_json": held_titles_json,
    }
