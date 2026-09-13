"""Resolve a character to their coat-of-arms structural definition.

CK3 stores CoAs at the top level of the rakaly JSON, indexed by an
integer id, and references them indirectly through the character's
dynasty_house. The resolution chain:

::

    living[<character_id>].dynasty_house  →  int (house id)
    dynasties.dynasty_house[<house_id>].coat_of_arms_id  →  int (coa id)
    coat_of_arms.coat_of_arms_manager_database[<coa_id>]  →  dict (the CoA structure)

The CoA structure itself is recursive — see :func:`resolve_character_coa`'s
docstring for the schema.

Designed to be called at save-tail time alongside ``extract_character_record``
so each tracked character's CoA can be persisted to ``Character.coa_json``
for the API to surface to the frontend renderer.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def resolve_character_coa(
    raw_save_data: dict[str, Any],
    character_id: int,
) -> dict[str, Any] | None:
    """Walk character → dynasty_house → coat_of_arms_id → CoA structure.

    Returns the full nested CoA dict (the structure CK3 emits, ready to
    feed to the SVG composition renderer), or ``None`` if any link in
    the chain is missing.

    The CoA structure looks like::

        {
          "pattern": "pattern_solid.dds",
          "color1": "black", "color2": "green", "color3": "yellow",
          "sub": {                          # optional nested sub-shield
            "pattern": "...",
            "color1": "...", "color2": "...",
            "instance": {"scale": [...], "offset": [...]},
            "colored_emblem": {             # optional charge texture
              "texture": "ce_leopard_passant_guardant.dds",
              "color1": "...", "color2": "...", "color3": "...",
              "mask": [0, 2, 0],            # which color slots apply
              "instance": {"position": [...], "scale": [...]}
            }
          }
        }

    Permissive — returns ``None`` for missing chains rather than raising,
    so save-tail's per-character refresh keeps going on data quirks
    (e.g. a character whose dynasty_house was just created and isn't in
    the database yet).
    """
    # 1. Character → dynasty_house id
    char = _lookup_character(raw_save_data, character_id)
    if char is None:
        return None
    house_id = char.get("dynasty_house")
    if not isinstance(house_id, int):
        return None

    # 2. dynasty_house → coat_of_arms_id (with dynasty fallback)
    #
    # CK3 stores arms at one of two levels:
    #   - the *house* (cadet branch override)
    #   - the *dynasty* (inherited default)
    # If the house declares no coat_of_arms_id, walk house.dynasty into
    # the parallel dynasties.dynasties table. ck3_chronicler-ytn.
    dynasties_root = raw_save_data.get("dynasties") or {}
    if not isinstance(dynasties_root, dict):
        return None
    houses = dynasties_root.get("dynasty_house") or {}
    if not isinstance(houses, dict):
        return None
    house = houses.get(str(house_id))
    if not isinstance(house, dict):
        return None
    coa_id = house.get("coat_of_arms_id")
    if not isinstance(coa_id, int):
        coa_id = _dynasty_coa_id(dynasties_root, house.get("dynasty"))
    if not isinstance(coa_id, int):
        return None

    # 3. coat_of_arms_id → CoA dict
    coa_db = (raw_save_data.get("coat_of_arms") or {}).get("coat_of_arms_manager_database") or {}
    if not isinstance(coa_db, dict):
        return None
    coa = coa_db.get(str(coa_id))
    if not isinstance(coa, dict):
        return None
    # ck3_chronicler-aerw slice 3: build the normalized CoA directly
    # instead of copy.deepcopy + in-place mutate. The deepcopy ran for
    # every tracked character on every save-tail tick (Pattern A) and
    # only one key (colored_emblem → colored_emblems) actually needed
    # rewriting — the rest of the dict is reused by reference, which
    # is safe because the caller persists ``coa_json`` immediately and
    # never mutates the result.
    return _normalize_coa(coa)


def _normalize_coa(node: Any) -> Any:
    """Return a copy of ``node`` with ``colored_emblem: [array]`` rewritten
    into ``colored_emblems`` (plural).

    Rakaly groups duplicate keys into lists; when a CoA node carries
    multiple charges under the singular ``colored_emblem`` key, that
    arrives as a list. Move the contents to the canonical plural key so
    every downstream consumer (frontend renderer, PDF export) sees one
    shape. ck3_chronicler-wo20.

    Non-dict values are returned as-is (shared by reference — they're
    leaves that the caller doesn't mutate). Dict values are returned as
    a shallow copy with the emblem-merge applied and ``sub`` / ``subs``
    recursed into. ck3_chronicler-aerw slice 3: replaces the previous
    deepcopy + in-place mutate path, removing a per-tick allocation
    storm proportional to CoA size × tracked-character count.
    """
    if not isinstance(node, dict):
        return node
    # Shallow-copy so the rewrite doesn't touch the input dict. Leaves
    # (strings, ints, untouched lists) are shared by reference.
    out: dict[str, Any] = dict(node)
    emblem = out.get("colored_emblem")
    if isinstance(emblem, list):
        existing = out.get("colored_emblems")
        merged: list[Any] = list(existing) if isinstance(existing, list) else []
        for e in emblem:
            if isinstance(e, dict):
                merged.append(e)
        out["colored_emblems"] = merged
        out.pop("colored_emblem", None)
    sub = out.get("sub")
    if isinstance(sub, dict):
        out["sub"] = _normalize_coa(sub)
    subs = out.get("subs")
    if isinstance(subs, list):
        out["subs"] = [_normalize_coa(s) for s in subs]
    return out


def _dynasty_coa_id(dynasties_root: dict[str, Any], dynasty_id: Any) -> int | None:
    """Look up the parent dynasty's coat_of_arms_id, or None."""
    if not isinstance(dynasty_id, int):
        return None
    dynasties = dynasties_root.get("dynasties") or {}
    if not isinstance(dynasties, dict):
        return None
    dyn = dynasties.get(str(dynasty_id))
    if not isinstance(dyn, dict):
        return None
    coa_id = dyn.get("coat_of_arms_id")
    return coa_id if isinstance(coa_id, int) else None


def _lookup_character(raw_save_data: dict[str, Any], character_id: int) -> dict[str, Any] | None:
    """Return the raw character record from living, dead_unprunable, or
    characters.dead_prunable, or None."""
    key = str(character_id)
    living = raw_save_data.get("living") or {}
    if isinstance(living, dict) and isinstance(living.get(key), dict):
        return living[key]
    dead = raw_save_data.get("dead_unprunable") or {}
    if isinstance(dead, dict) and isinstance(dead.get(key), dict):
        return dead[key]
    dead_prunable = (raw_save_data.get("characters") or {}).get("dead_prunable") or {}
    if isinstance(dead_prunable, dict) and isinstance(dead_prunable.get(key), dict):
        return dead_prunable[key]
    return None
