"""LLM raw-character-record extraction (ck3_chronicler-27ov.36 / M-S4).

Flattens a parsed :class:`CharacterSnapshot` (plus decoded traits and
resolved family names) into the dict the narrative layer persists. Split
out of parse.py; re-exported there for compatibility.
"""

from __future__ import annotations

from typing import Any

# --- public API ---

# Fields stripped from the raw character record before persisting it as
# `Character.save_snapshot_json` (ck3_chronicler-6ui / 60m).
#
# `dna` is the worst bloat offender — a 100+ element trait-ID array
# with no narrative value.
#
# `first_name`, `nickname_text`, `female` are dropped per ck3_chronicler-60m:
# the structured prompt header already surfaces these (Known names, called
# the X, Gender). Leaving them in the raw record let the LLM trust the
# raw form (which is the un-decoded "E_lla" or the unset female=No) over
# the resolved header — observed in the 2026-05-02 live A/B against
# Ælla 12267 where include_raw_record=True caused the model to revert
# to "E_lla" and use they/their pronouns.
_RAW_RECORD_DROP_FIELDS: frozenset[str] = frozenset(
    {"dna", "first_name", "nickname_text", "female"}
)


# Keys inside `family_data` whose values are character IDs (int or list[int]).
# Used by ck3_chronicler-60m to expand "spouse: 38379" → "spouse: {id: 38379,
# name: Beorhtgyth}" so the LLM has the name inline and doesn't write
# "married 38379" in the biography.
_FAMILY_CHAR_ID_KEYS: frozenset[str] = frozenset(
    {
        "mother",
        "father",
        "primary_spouse",
        "spouse",
        "former_spouses",
        "concubinist",
        "concubine",
        "child",
        "betrothed",
    }
)


def extract_character_record(
    data: dict[str, Any],
    ck3_id: int,
    *,
    name_lookup: dict[int, str] | None = None,
    traits_lookup: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Pull one character's raw rakaly record out of a parsed save dict.

    Returns the trimmed raw dict ready to be JSON-serialized as
    ``Character.save_snapshot_json``. Used by ck3_chronicler-6ui to give
    the LLM access to fields the structured parser doesn't extract
    (culture-specific data, adventurer state, title hierarchies, etc.)
    without forcing every such field through schema migrations.

    Returns ``None`` if the character isn't present in ``living``,
    ``dead_unprunable``, or ``characters.dead_prunable``.

    ``name_lookup`` (ck3_chronicler-60m): optional ``{character_id: name}``
    mapping. When provided, character IDs in ``family_data`` are
    expanded to ``{"id": X, "name": "..."}`` dicts so the LLM has names
    inline and doesn't write raw IDs into the biography. The structured
    parser already populates SaveSnapshot.characters which is the
    natural source for this mapping.

    ``traits_lookup`` (ck3_chronicler-5ty): optional save-level trait ID →
    trait name list (SaveSnapshot.traits_lookup). When provided, a
    parallel ``traits_named`` array is added alongside the raw integer
    ``traits`` field — readable strings like ``"diligent"``,
    ``"education_diplomacy_3"`` so the consolidation prompt can surface
    a character's trait set without re-parsing the save. Bare integers
    in the source ``traits`` array remain so ID-driven downstream code
    (rare) keeps working.
    """
    key = str(ck3_id)
    raw: dict[str, Any] | None = None
    living = data.get("living") or {}
    if isinstance(living, dict) and key in living and isinstance(living[key], dict):
        raw = living[key]
    else:
        dead = data.get("dead_unprunable") or {}
        if isinstance(dead, dict) and key in dead and isinstance(dead[key], dict):
            raw = dead[key]
    if raw is None:
        dead_prunable = (data.get("characters") or {}).get("dead_prunable") or {}
        if (
            isinstance(dead_prunable, dict)
            and key in dead_prunable
            and isinstance(dead_prunable[key], dict)
        ):
            raw = dead_prunable[key]
    if raw is None:
        return None

    out = {k: v for k, v in raw.items() if k not in _RAW_RECORD_DROP_FIELDS}

    # ck3_chronicler-63yw slice 1: rakaly emits family_data as an empty
    # list ([]) when a character has no recorded family relations (common
    # for young children). Canonicalise to {} so every downstream reader
    # (tree.py, prompts, briefing) can rely on .get('child') / .get('mother')
    # without re-checking the shape.
    if isinstance(out.get("family_data"), list):
        out["family_data"] = {}

    if name_lookup is not None and isinstance(out.get("family_data"), dict):
        out["family_data"] = _resolve_family_names(out["family_data"], name_lookup)

    if traits_lookup is not None:
        traits_value = out.get("traits")
        decoded = _decode_traits(traits_value, traits_lookup)
        if decoded is not None:
            out["traits_named"] = decoded

    return out


def _decode_traits(traits_value: Any, traits_lookup: tuple[str, ...]) -> list[str] | None:
    """Resolve a raw traits payload to a list of decoded names.

    Accepts either a list-of-ints (CK3's normal shape) or a single int
    (the engine emits a scalar when there's exactly one trait). Returns
    ``None`` when there is nothing to decode so the caller can skip
    adding the ``traits_named`` key entirely. Out-of-range trait IDs
    fall through as ``"trait_<id>"`` placeholders rather than dropping
    silently — useful when a mod adds traits beyond the base lookup.
    """
    if traits_value is None:
        return None
    if isinstance(traits_value, int):
        ids: list[int] = [traits_value]
    elif isinstance(traits_value, list):
        ids = [t for t in traits_value if isinstance(t, int)]
    else:
        return None
    if not ids:
        return None
    out: list[str] = []
    for tid in ids:
        if 0 <= tid < len(traits_lookup):
            out.append(traits_lookup[tid])
        else:
            out.append(f"trait_{tid}")
    return out


def _resolve_family_names(family: dict[str, Any], name_lookup: dict[int, str]) -> dict[str, Any]:
    """Expand character IDs in family_data to {id, name} dicts.

    Unknown IDs (not in ``name_lookup``) are still expanded to a dict
    with name=None — the LLM at least sees the structure and can write
    around the unknown party rather than emitting a raw int.
    """
    resolved: dict[str, Any] = {}
    for k, v in family.items():
        if k not in _FAMILY_CHAR_ID_KEYS:
            resolved[k] = v
            continue
        if isinstance(v, int):
            resolved[k] = {"id": v, "name": name_lookup.get(v)}
        elif isinstance(v, list):
            resolved[k] = [
                {"id": item, "name": name_lookup.get(item)} if isinstance(item, int) else item
                for item in v
            ]
        else:
            resolved[k] = v
    return resolved
