"""Extract structured data from a parsed CK3 save (rakaly JSON output).

Takes the dict returned by :func:`chronicler.save.rakaly.convert_save_to_json`
and pulls out:

- top-level metadata (campaign UUID, CK3 version, dates, player ID)
- characters (alive + dead-unprunable; CK3 prunes deeply-historical NPCs to
  save space, so we only see "important" dead chars from the engine's POV)
- family relationships (mother/father/spouses/children)
- vanilla character memories — the curated narrative records keyed by
  ``memory_type`` (e.g. ``memory_grand_wedding``, ``memory_won_battle``)

The result is detached from the source dict — plain frozen dataclasses
that survive any further mutation of the input. Caller passes a
:class:`SaveSnapshot` to downstream code (V06-P02 diff, biographies,
auto-track) without needing the 70+ MB raw JSON.

Save JSON shape (CK3 1.19, post-rakaly v0.8.15):

- ``meta_data.version`` — game version string
- ``meta_data.meta_date`` — current in-game date (YYYY.M.D)
- ``meta_data.meta_main_portrait.id`` — player's character ID
- ``playthrough_id`` — campaign UUID
- ``bookmark_date`` — campaign start date
- ``living[<id>]`` — dict of ID-keyed character records (alive)
- ``dead_unprunable[<id>]`` — same shape, dead chars CK3 hasn't pruned
- ``character_memory_manager.database[<id>]`` — vanilla memory records
- Each character has ``alive_data.memories`` (or ``dead_data.memories``)
  as a list of memory IDs into the database

Limitations: ironman saves, dynamically-pruned NPC histories, and
character names from `dynasty_house` localization are out of scope at
this layer (callers can join in via ``dynasties[<house_id>]`` lookups
on the source dict if needed).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, NamedTuple

# --- M-S4 split (ck3_chronicler-27ov.36): four concerns moved to sibling
# modules. parse.py keeps parse_save + the _parse_* extractors and re-exports
# the moved public symbols so `from chronicler.save.parse import X` keeps
# working for the ~60 existing call sites. __all__ marks them as re-exports.
from chronicler.save.autotrack import (
    AUTO_TRACK_RULES_DEFAULT,
    _resolve_county_vassals,
    auto_track_candidates,
    resolve_auto_track_rules,
)
from chronicler.save.ck3_names import resolve_character_name
from chronicler.save.localization import decode_ck3_name, strip_loca_markup
from chronicler.save.raw_record import extract_character_record
from chronicler.save.snapshot import (
    ActivitySnapshot,
    ArtifactSnapshot,
    CharacterSnapshot,
    ConstructionSnapshot,
    ContractSnapshot,
    CourtPositionSnapshot,
    DomicileSnapshot,
    EpidemicSnapshot,
    FamilySnapshot,
    InspirationSnapshot,
    MemorySnapshot,
    SaveSnapshot,
    TitleSnapshot,
    WarSnapshot,
    _tier_from_key,
)
from chronicler.save.titles import (
    _PRIMARY_TIER_RANK,
    get_held_titles_sorted,
    get_primary_title_held_by,
    get_titles_held_by,
)

log = logging.getLogger(__name__)

__all__ = [
    "AUTO_TRACK_RULES_DEFAULT",
    "ActivitySnapshot",
    "ArtifactSnapshot",
    "CharacterSnapshot",
    "ConstructionSnapshot",
    "ContractSnapshot",
    "CourtPositionSnapshot",
    "DomicileSnapshot",
    "EpidemicSnapshot",
    "FamilySnapshot",
    "InspirationSnapshot",
    "MemorySnapshot",
    "SaveSnapshot",
    "TitleSnapshot",
    "WarSnapshot",
    "_PRIMARY_TIER_RANK",
    "_as_int_tuple",
    "_resolve_county_vassals",
    "_tier_from_key",
    "auto_track_candidates",
    "extract_character_record",
    "get_held_titles_sorted",
    "get_primary_title_held_by",
    "get_titles_held_by",
    "parse_save",
    "resolve_auto_track_rules",
]


# --- internal helpers ---


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_playthrough_id(data: dict[str, Any]) -> str:
    """Return a stable per-playthrough identifier.

    **CK3 1.19 exposes a top-level ``playthrough_id`` and it is the live
    path.** (Issue #6. The text here used to say 1.18.3 does not expose
    one and describe the canonical branch as speculative — "in case CK3
    reintroduces it". That was measured true on 2026-05-04 and is false
    now; every save this code sees on 1.19 takes the canonical branch.)

    Measured 2026-08-13 against one 867 campaign on 1.19.0.6, converted
    with rakaly 0.8.15:

    ==========================  ============  ====================================
    save                        in-game date  ``playthrough_id``
    ==========================  ============  ====================================
    Merovingian_start (fresh)   867.1.1       ``00000000-0000-4000-859f-47fe4aa6a23c``
    autosave_2                  873.1.1       *same*
    autosave_1                  873.2.1       *same*
    autosave                    873.3.1       *same*
    autosave_exit               873.3.16      *same*
    ==========================  ============  ====================================

    The twelve leading hex zeros make the id *look* like a
    partially-initialised placeholder (a random UUID lands there about
    once in 10^15), and the worry was that CK3 would fill it in once the
    campaign was played — every later autosave would then mismatch the
    campaign pin, ``_is_advance_candidate`` in :mod:`chronicler.save.tick`
    would publish ``save_dropped_foreign`` for each one, and the app would
    record nothing behind the red "wrong game" badge.

    It does not. The id is **stable for the life of the playthrough**,
    identical from the never-played start through six in-game years. So a
    campaign pinned from a fresh-start save keeps matching, no degenerate-id
    heuristic is warranted, and none is implemented: whatever the field
    holds is used verbatim. Rejecting placeholder-*shaped* ids would break
    this campaign for no gain, and re-pinning on mismatch would defeat the
    foreign-save protection the pin exists for.

    A 1.19.0.5 played campaign measured 2026-07-28 carried a full random
    UUID (``3ad8d836-…``), so both shapes occur in the wild and both are
    treated the same.

    **The ``synth:`` fallback below is effectively dead on current CK3.**
    It remains reachable only for a save with no canonical field —
    pre-1.19 saves, or a mod that strips it — so it is kept, not deleted.
    Do not assume it is exercised: it is not, on any save this project has
    seen since 1.19. It synthesises an identifier from three values that
    are stable across saves of the same playthrough but distinct between
    playthroughs:

    - ``random_seed`` (top-level int): CK3's RNG seed, fixed at game
      start, propagated unchanged across every save of the same run
    - ``bookmark_date`` (top-level str): campaign start date
    - ``played_character.legacy[0].character`` (int): the founding
      ruler ID

    The synthesised form is prefixed ``synth:`` so future code can tell
    a synthesized id from a canonical one without parsing it. Returns an
    empty string only if all three fall through, which would mean an
    extremely malformed save — caller (registry.resolve_campaign_for_save)
    treats empty as "unknown playthrough" and forces a manual --campaign.
    """
    canonical = data.get("playthrough_id")
    if isinstance(canonical, str) and canonical:
        return canonical
    seed = data.get("random_seed")
    bookmark = data.get("bookmark_date")
    pc = data.get("played_character")
    founding_id: int | None = None
    if isinstance(pc, dict):
        legacy = pc.get("legacy")
        if isinstance(legacy, list) and legacy and isinstance(legacy[0], dict):
            founding_id = _coerce_int(legacy[0].get("character"))
    parts = [
        str(seed) if seed is not None else "",
        str(bookmark) if bookmark else "",
        str(founding_id) if founding_id is not None else "",
    ]
    if not any(parts):
        return ""
    return "synth:" + ":".join(parts)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ck3_chronicler-27ov.35 (audit M-S1): the shared skeleton of every
# database-table extractor — eight functions repeated the same guarded
# root walk, the per-row int-key + dict-value guards (18x), the
# triple-evaluation string-field idiom (15x), and the reverse-index
# freeze closer (8x). Three helpers; each extractor keeps only its
# domain field list.


def _iter_db_records(data: dict[str, Any], *path: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Walk ``data[path[0]][path[1]]…`` (every step guarded — a missing
    key or non-dict node yields nothing) and yield ``(id, record)``
    pairs from the table at the end of the path. Rows whose key doesn't
    coerce to int or whose value isn't a dict are skipped — CK3 emits
    bare string/int sentinels alongside real records (observed live in
    v09-smoke: ``epidemics.database`` with 3 dicts and 1 bare string).
    """
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return
        node = node.get(key) or {}
    if not isinstance(node, dict):
        return
    for raw_id, raw in node.items():
        if not isinstance(raw, dict):
            continue
        rid = _coerce_int(raw_id)
        if rid is None:
            continue
        yield rid, raw


def _opt_str(raw: dict[str, Any], key: str) -> str | None:
    """``raw[key]`` when it's a non-empty string, else None."""
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def _freeze_index(builder: dict[int, set[int]]) -> dict[int, frozenset[int]]:
    """Freeze a reverse-index builder into the immutable per-character
    lookup shape SaveSnapshot carries."""
    return {cid: frozenset(items) for cid, items in builder.items()}


def _parse_currency(raw: Any, *, prefer: str = "current") -> float | None:
    """ck3_chronicler-wdhe: extract a CK3 currency value from a raw save
    field. Currencies (gold / prestige / piety on characters; prestige
    on dynasties for renown) are stored either as a flat scalar (older
    saves, edge cases) or as a dict with ``currency`` / ``value`` for
    the running balance and ``accrued`` for the lifetime total.

    ``prefer="current"`` returns the running balance; ``prefer="accrued"``
    returns the lifetime accumulation, falling back to the running
    balance when ``accrued`` is missing (the field is optional in some
    save shapes). None when the value can't be coerced to a float.
    """
    if isinstance(raw, dict):
        if prefer == "accrued":
            accrued = _coerce_float(raw.get("accrued"))
            if accrued is not None:
                return accrued
        # The dict shape uses "currency" on dynasty records and "value"
        # on character alive_data. Try both before giving up.
        for key in ("currency", "value"):
            balance = _coerce_float(raw.get(key))
            if balance is not None:
                return balance
        return None
    return _coerce_float(raw)


def _as_int_tuple(value: Any) -> tuple[int, ...]:
    """Accept either a single int or a list of ints (rakaly emits both
    depending on cardinality), return a tuple."""
    if value is None:
        return ()
    if isinstance(value, list):
        # Permissive: skip entries that can't be coerced rather than raising —
        # CK3 has been observed emitting sentinel strings inside lists (see the
        # epidemics handling below). One junk entry must not kill ingest for the
        # whole campaign (ck3_chronicler-27ov.7 / audit M-S2).
        return tuple(c for c in (_coerce_int(x) for x in value) if c is not None)
    if isinstance(value, int):
        return (value,)
    # rakaly may emit single-value as scalar; handle gracefully
    coerced = _coerce_int(value)
    return (coerced,) if coerced is not None else ()


def _parse_family(raw: Any) -> FamilySnapshot:
    """Convert the family_data dict to a FamilySnapshot.

    Empty list (rakaly's "no family at all" case) and dict are both
    accepted. Missing keys yield None / empty-tuple defaults.
    """
    if not isinstance(raw, dict):
        return FamilySnapshot()
    return FamilySnapshot(
        mother=_coerce_int(raw.get("mother")),
        father=_coerce_int(raw.get("father")),
        primary_spouse=_coerce_int(raw.get("primary_spouse")),
        spouses=_as_int_tuple(raw.get("spouse")),
        former_spouses=_as_int_tuple(raw.get("former_spouses")),
        concubinist=_coerce_int(raw.get("concubinist")),
        concubines=_as_int_tuple(raw.get("concubine")),
        children=_as_int_tuple(raw.get("child")),
        betrothed=_as_int_tuple(raw.get("betrothed")),
    )


def _parse_memory(memory_id: int, raw: dict[str, Any]) -> MemorySnapshot:
    participants_raw = raw.get("participants") or {}
    participants: list[tuple[str, int]] = []
    if isinstance(participants_raw, dict):
        for role, char_id in participants_raw.items():
            cid = _coerce_int(char_id)
            if cid is not None:
                participants.append((str(role), cid))
    return MemorySnapshot(
        memory_id=memory_id,
        memory_type=str(raw.get("type", "")),
        creation_date=str(raw.get("creation_date", "")),
        end_date=str(raw["end_date"]) if "end_date" in raw else None,
        participants=tuple(participants),
    )


def _build_memory_index(memory_db: Any) -> dict[int, MemorySnapshot]:
    """Build {memory_id: MemorySnapshot} from the engine's memory database."""
    if not isinstance(memory_db, dict):
        return {}
    out: dict[int, MemorySnapshot] = {}
    for raw_id, raw in memory_db.items():
        cid = _coerce_int(raw_id)
        if cid is None or not isinstance(raw, dict):
            continue
        out[cid] = _parse_memory(cid, raw)
    return out


def _parse_character(
    ck3_id: int,
    raw: dict[str, Any],
    *,
    is_dead: bool,
    memory_index: dict[int, MemorySnapshot],
    cultures_lookup: dict[int, str] | None = None,
) -> CharacterSnapshot:
    """Pull one character record into a CharacterSnapshot.

    ``cultures_lookup`` (ck3_chronicler-57j): when provided, the parser
    resolves the character's culture template name and threads it into
    :func:`decode_ck3_name` so capital escapes mid-word (``HoE_l``,
    ``GuilhE_m``, ``StanisL_aw``, ``MarI_a``, …) disambiguate to the
    culture's actual diacritic instead of falling through to the lossy
    strip. ``None`` keeps the v0.5/63y culture-blind behaviour.
    """
    alive_data = raw.get("alive_data") if not is_dead else raw.get("dead_data") or {}
    memory_ids: list[int] = []
    location_id: int | None = None
    gold: float | None = None
    prestige: float | None = None
    prestige_lifetime: float | None = None
    piety: float | None = None
    piety_lifetime: float | None = None
    if isinstance(alive_data, dict):
        raw_mems = alive_data.get("memories")
        if isinstance(raw_mems, list):
            memory_ids = [_coerce_int(m) for m in raw_mems]
            memory_ids = [m for m in memory_ids if m is not None]
        # location is nested: alive_data.location = {"location": <province_id>}
        loc_wrapper = alive_data.get("location")
        if isinstance(loc_wrapper, dict):
            location_id = _coerce_int(loc_wrapper.get("location"))
        # ck3_chronicler-wdhe: gold/prestige/piety. Only meaningful for
        # alive characters — dead_data drops these — but the read is
        # cheap and defensive enough to run unconditionally.
        if not is_dead:
            gold = _parse_currency(alive_data.get("gold"))
            prestige = _parse_currency(alive_data.get("prestige"))
            prestige_lifetime = _parse_currency(alive_data.get("prestige"), prefer="accrued")
            piety = _parse_currency(alive_data.get("piety"))
            piety_lifetime = _parse_currency(alive_data.get("piety"), prefer="accrued")

    # ck3_chronicler-rgay: alive_data.perk is a flat list of lifestyle
    # perk engine keys. Only meaningful for living characters
    # (dead_data drops the field). Defensive: tolerate missing field
    # entirely and tolerate non-string entries (the engine occasionally
    # writes placeholder ints in mod-affected saves).
    perk_keys: list[str] = []
    if isinstance(alive_data, dict) and not is_dead:
        raw_perks = alive_data.get("perk")
        if isinstance(raw_perks, list):
            for entry in raw_perks:
                if isinstance(entry, str) and entry:
                    perk_keys.append(entry)

    death_date = None
    death_cause: str | None = None
    death_killer: int | None = None
    dead_data = raw.get("dead_data")
    if isinstance(dead_data, dict):
        date = dead_data.get("date")
        if date is not None:
            death_date = str(date)
        # ck3_chronicler-caxv: surface dead_data.reason (string like
        # 'death_old_age', 'death_apoplexy', 'death_battle') and
        # dead_data.killer (int char id) so DeathPayload can carry them
        # forward to the biography pipeline. Both are optional in the
        # save tree.
        #
        # Issue #9 proposed rejecting reasons without a ``death_`` prefix,
        # on the theory that a value like ``blind`` was a trait name
        # bleeding in. Measured against 1.19 (2026-08-13): ``blind`` is a
        # death reason in its own right — ``common/deathreasons/
        # 00_natural_deaths.txt``, MISC section, triggered by
        # ``has_trait = blind`` OR ``clouded_eyes``, which is why it reads
        # like a trait. Vanilla defines 313 reasons and exactly two skip
        # the prefix (``blind``, ``debug``). One real save carried 156
        # distinct reasons, 1,106 of them ``blind``.
        #
        # So the value is taken verbatim: the prefix is not a validity
        # rule, and filtering on it would blank real causes silently,
        # which is worse than the cosmetic oddity it was meant to fix.
        # tests/unit/test_save_parse.py pins this.
        reason = dead_data.get("reason")
        if isinstance(reason, str) and reason:
            death_cause = reason
        death_killer = _coerce_int(dead_data.get("killer"))

    nickname = raw.get("nickname_text") or None
    if nickname == "":
        nickname = None

    # ck3_chronicler-mcu: government string for adventurer-mode detection.
    # ck3_chronicler-jrwe: decision_cooldowns dict surfaces freshly-taken
    # decisions to the diff layer. Both live under landed_data so the
    # block is parsed in one pass.
    landed_data = raw.get("landed_data")
    government: str | None = None
    decisions_taken: tuple[tuple[str, str], ...] = ()
    if isinstance(landed_data, dict):
        gov = landed_data.get("government")
        if isinstance(gov, str) and gov:
            government = gov
        cooldowns = landed_data.get("decision_cooldowns")
        if isinstance(cooldowns, dict):
            entries: list[tuple[str, str]] = []
            for did, end_date in cooldowns.items():
                if isinstance(did, str) and did:
                    entries.append((did, str(end_date) if end_date is not None else ""))
            decisions_taken = tuple(sorted(entries))

    # Decode CK3's letter+underscore name escapes (Æ, þ, ð, etc.) at
    # parse time so SaveSnapshot and downstream Character DB rows store
    # readable Unicode — see chronicler.save.localization. nickname goes
    # through the same decoder defensively even though nickname_text is
    # usually engine-localized.
    culture_id = _coerce_int(raw.get("culture"))
    culture_name: str | None = None
    if cultures_lookup is not None and culture_id is not None:
        culture_name = cultures_lookup.get(culture_id)
    # ck3_chronicler-n0s4: character_modifier is a list of
    # {modifier: <key>, date: <YYYY.M.D>} records. We only capture the
    # engine key (the date is informational — diff fires on
    # set-membership change, not on date). Defensive against malformed
    # entries: skip records without a string modifier field.
    raw_mods = raw.get("character_modifier")
    modifier_keys: list[str] = []
    if isinstance(raw_mods, list):
        for entry in raw_mods:
            if not isinstance(entry, dict):
                continue
            mod = entry.get("modifier")
            if isinstance(mod, str) and mod:
                modifier_keys.append(mod)
    # ck3_chronicler-r3fs: prefer CK3's own name localization, which
    # disambiguates the lossy letter+underscore escape (e.g. O_lafr→Ólafr
    # vs O_rvar→Örvar — same escape, different glyph) that no per-culture
    # map can resolve. rakaly returns the escape token verbatim, so it keys
    # directly into the loca map. Fall back to the heuristic decoder on a
    # miss (mod names, no game install reachable).
    first_name_token = raw.get("first_name") or None
    first_name = resolve_character_name(first_name_token, culture=culture_name)
    return CharacterSnapshot(
        ck3_id=ck3_id,
        first_name=first_name,
        nickname=decode_ck3_name(nickname, culture=culture_name),
        is_dead=is_dead,
        female=bool(raw.get("female", False)),
        birth_date=str(raw["birth"]) if "birth" in raw else None,
        death_date=death_date,
        death_cause=death_cause,
        death_killer=death_killer,
        culture_id=culture_id,
        faith_id=_coerce_int(raw.get("faith")),
        dynasty_house_id=_coerce_int(raw.get("dynasty_house")),
        ethnicity=raw.get("ethnicity") or None,
        traits=_as_int_tuple(raw.get("traits")),
        family=_parse_family(raw.get("family_data")),
        location_id=location_id,
        memories=tuple(memory_index[m] for m in memory_ids if m in memory_index),
        government=government,
        decisions_taken=decisions_taken,
        gold=gold,
        prestige=prestige,
        prestige_lifetime=prestige_lifetime,
        piety=piety,
        piety_lifetime=piety_lifetime,
        modifiers=tuple(modifier_keys),
        perks=tuple(perk_keys),
    )


def _parse_alliances(data: dict[str, Any]) -> dict[int, frozenset[int]]:
    """Extract per-character alliance sets from relations.active_relations.

    Each relation dict has ``first`` and ``second`` character IDs and an
    optional ``alliances`` list. Presence of the alliances key marks an
    active alliance between the two parties. The save lists each
    alliance bidirectionally — recorded in both directions in the
    output for symmetric diff lookups.
    """
    relations = (data.get("relations") or {}).get("active_relations") or []
    if not isinstance(relations, list):
        return {}
    builder: dict[int, set[int]] = {}
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if "alliances" not in rel:
            continue
        first = _coerce_int(rel.get("first"))
        second = _coerce_int(rel.get("second"))
        if first is None or second is None:
            continue
        builder.setdefault(first, set()).add(second)
        builder.setdefault(second, set()).add(first)
    return _freeze_index(builder)


def _parse_artifacts(
    data: dict[str, Any],
) -> tuple[dict[int, ArtifactSnapshot], dict[int, frozenset[int]]]:
    """ck3_chronicler-d83: build (artifacts, character_to_artifacts).

    Source: ``artifacts.artifacts[<id>]`` in the rakaly JSON. Each
    record has ``name``, ``type``, ``rarity``, and ``owner`` (a bare
    character_id int). The character_to_artifacts inverse is built so
    the diff layer can do set-diffs per tracked character without
    scanning all 700+ artifacts on every diff tick."""
    out: dict[int, ArtifactSnapshot] = {}
    by_owner: dict[int, set[int]] = {}
    for aid, raw in _iter_db_records(data, "artifacts", "artifacts"):
        # ck3_chronicler-4u14: artifact names occasionally carry CK3
        # tooltip/link markup (referencing creators / inscriptions); strip
        # before storing so prose isn't broken by raw \x15 markup.
        name = strip_loca_markup(raw.get("name"))
        owner = _coerce_int(raw.get("owner"))
        out[aid] = ArtifactSnapshot(
            artifact_id=aid,
            name=name if isinstance(name, str) and name else None,
            type=_opt_str(raw, "type"),
            rarity=_opt_str(raw, "rarity"),
            owner_id=owner,
        )
        if owner is not None:
            by_owner.setdefault(owner, set()).add(aid)
    return out, _freeze_index(by_owner)


def _parse_war_side_participants(side: Any) -> set[int]:
    """Extract character IDs from a war's ``attacker`` or ``defender``
    block. Each side is a dict with ``participants`` (a list of dicts
    with ``character`` int) plus aggregated casualty stats. Defensive
    against rakaly emitting a list directly when the side has exactly
    one participant — the same shape rule applies."""
    if not isinstance(side, dict):
        return set()
    raw = side.get("participants")
    if not isinstance(raw, list):
        return set()
    out: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        cid = _coerce_int(entry.get("character"))
        if cid is not None:
            out.add(cid)
    return out


def _parse_wars(
    data: dict[str, Any],
) -> tuple[dict[int, WarSnapshot], dict[int, frozenset[int]]]:
    """Build (wars, character_to_wars) from ``wars.active_wars``
    (ck3_chronicler-o7j).

    Returns two parallel structures:

    - ``wars[war_id] -> WarSnapshot`` carries CB metadata + side
      participant sets; the diff layer reads it to resolve which
      side a character is on and to extract narrative context.
    - ``character_to_wars[character_id] -> frozenset(war_ids)``
      drives the per-character set-diff that emits Joined/Left
      events (parallel to ``alliances`` shape — see
      :func:`_parse_alliances`).
    """
    wars: dict[int, WarSnapshot] = {}
    builder: dict[int, set[int]] = {}
    for wid, raw in _iter_db_records(data, "wars", "active_wars"):
        attacker_side = raw.get("attacker")
        defender_side = raw.get("defender")
        attackers = _parse_war_side_participants(attacker_side)
        defenders = _parse_war_side_participants(defender_side)

        cb = raw.get("casus_belli") or {}
        cb_type: str | None = None
        primary_attacker_id: int | None = None
        primary_defender_id: int | None = None
        claimant_id: int | None = None
        targeted_titles: tuple[int, ...] = ()
        if isinstance(cb, dict):
            t = cb.get("type")
            if isinstance(t, str) and t:
                cb_type = t
            primary_attacker_id = _coerce_int(cb.get("attacker"))
            primary_defender_id = _coerce_int(cb.get("defender"))
            claimant_id = _coerce_int(cb.get("claimant"))
            tt = cb.get("targeted_titles")
            if isinstance(tt, list):
                targeted_titles = tuple(int(x) for x in tt if isinstance(x, int))

        wars[wid] = WarSnapshot(
            war_id=wid,
            name=_opt_str(raw, "name"),
            start_date=_opt_str(raw, "start_date"),
            casus_belli_type=cb_type,
            targeted_titles=targeted_titles,
            primary_attacker_id=primary_attacker_id,
            primary_defender_id=primary_defender_id,
            claimant_id=claimant_id,
            attacker_participants=frozenset(attackers),
            defender_participants=frozenset(defenders),
        )
        for cid in attackers | defenders:
            builder.setdefault(cid, set()).add(wid)

    return wars, _freeze_index(builder)


def _parse_epidemics(
    data: dict[str, Any],
) -> tuple[dict[int, EpidemicSnapshot], dict[int, frozenset[int]]]:
    """ck3_chronicler-9wrd: build (epidemics, character_to_epidemics)
    from ``epidemics.database``.

    CK3 stores active epidemics at ``epidemics.database[<id>]`` with a
    ``characters`` list of currently-infected character IDs. We mirror
    the wars shape: a metadata dict keyed by epidemic_id and a per-
    character set lookup so the diff can do alliance-style set-diffs
    without scanning every epidemic for every tracked character.

    Concluded / pruned epidemics simply disappear from the database, so
    new-vs-old set membership at the per-character level produces the
    "tracked char newly infected" signal the diff layer emits.

    Defensive: ``epidemics.database`` sometimes contains string or int
    sentinel entries (observed live in v09-smoke autosave: 4 entries
    where 3 were dicts and 1 was a bare string) — non-dict values are
    silently skipped.
    """
    epidemics: dict[int, EpidemicSnapshot] = {}
    builder: dict[int, set[int]] = {}
    for eid, raw in _iter_db_records(data, "epidemics", "database"):
        chars_raw = raw.get("characters")
        affected: set[int] = set()
        if isinstance(chars_raw, list):
            for c in chars_raw:
                cid = _coerce_int(c)
                if cid is not None:
                    affected.add(cid)
        epidemics[eid] = EpidemicSnapshot(
            epidemic_id=eid,
            epidemic_type=_opt_str(raw, "type"),
            name=_opt_str(raw, "name"),
            intensity=_opt_str(raw, "intensity"),
            creation_date=_opt_str(raw, "creation_date"),
            start_province=_coerce_int(raw.get("start_province")),
            num_infected_provinces=_coerce_int(raw.get("num_infected_provinces")) or 0,
            num_infected_characters=_coerce_int(raw.get("num_infected_characters")) or 0,
            num_character_deaths=_coerce_int(raw.get("num_character_deaths")) or 0,
        )
        for cid in affected:
            builder.setdefault(cid, set()).add(eid)

    return epidemics, _freeze_index(builder)


def _parse_activities(
    data: dict[str, Any],
) -> tuple[dict[int, ActivitySnapshot], dict[int, frozenset[int]]]:
    """ck3_chronicler-qx7n (8aie slice 1): build
    (activities, character_to_activities) from ``activity_manager.database``.

    Mirrors :func:`_parse_epidemics` end-to-end. Each entry in the
    database is keyed by activity_id; the record carries ``type``,
    ``host``, optional ``attending`` (list of char ids), date fields,
    and ``phases`` (list of {phase, province}). Concluded activities
    simply disappear from the database, so new-vs-old set membership
    at the per-character level produces the "activity ended" signal
    the diff layer emits.

    The per-character reverse index covers BOTH the host and every
    char in ``attending`` so the diff can detect either kind of
    participation ending without re-checking the host field.

    Defensive: non-dict database entries are silently skipped (same
    pattern as _parse_epidemics — CK3 has been observed to emit
    sentinel string values alongside the real records).
    """
    activities: dict[int, ActivitySnapshot] = {}
    builder: dict[int, set[int]] = {}
    for aid, raw in _iter_db_records(data, "activity_manager", "database"):
        host_id = _coerce_int(raw.get("host"))
        # Province from the first phase entry. Multi-phase activities
        # (long pilgrimages) walk multiple provinces; the first is the
        # narratively load-bearing "where it started" anchor.
        start_province_id: int | None = None
        phases_raw = raw.get("phases")
        if isinstance(phases_raw, list) and phases_raw:
            first_phase = phases_raw[0]
            if isinstance(first_phase, dict):
                start_province_id = _coerce_int(first_phase.get("province"))
        # Attendees: ``attending`` is a plain list of char ids on real
        # CK3 records. Missing field → empty set (some activity types
        # like activity_adult_education don't surface attending).
        attendees: set[int] = set()
        attending_raw = raw.get("attending")
        if isinstance(attending_raw, list):
            for c in attending_raw:
                cid = _coerce_int(c)
                if cid is not None:
                    attendees.add(cid)
        activities[aid] = ActivitySnapshot(
            activity_id=aid,
            activity_type=_opt_str(raw, "type"),
            host_id=host_id,
            creation_date=_opt_str(raw, "creation_date"),
            active_start_date=_opt_str(raw, "active_start_date"),
            start_province_id=start_province_id,
            attendees=frozenset(attendees),
        )
        # Reverse index: host + attendees, both reach the diff layer.
        if host_id is not None:
            builder.setdefault(host_id, set()).add(aid)
        for cid in attendees:
            builder.setdefault(cid, set()).add(aid)

    return activities, _freeze_index(builder)


def _parse_inspirations(
    data: dict[str, Any],
) -> tuple[dict[int, InspirationSnapshot], dict[int, frozenset[int]]]:
    """ck3_chronicler-m658 (8aie slice 2): build
    (inspirations, character_to_sponsored_inspirations) from CK3's
    ``inspirations_manager.database`` plus the per-character
    ``landed_data.sponsored_inspirations`` reverse lookup.

    Field map confirmed against a live Sleggja save (autosave_exit,
    2026-05-28):

    - ``inspirations_manager.database[<id>]`` — the inspiration record
      (type, total_cost, progress, created, sponsored, base). Non-dict
      values like ``'none'`` are skipped (CK3 sentinels alongside real
      records, same pattern as _parse_activities).
    - ``living[<char_id>].alive_data.inspiration`` — singular int
      pointing at the inspiration_id this character carries (the
      ARTISAN). Used to populate ``artisan_character_id`` on the
      snapshot record.
    - ``living[<char_id>].landed_data.sponsored_inspirations`` — list
      of inspiration_ids this character SPONSORS. The reverse index
      keyed by char_id. The diff layer's set-membership transitions
      surface as inspiration_sponsored events.

    The asymmetry between artisan (alive_data) and sponsor (landed_data)
    reflects CK3's data model: an artisan can be any living character;
    a sponsor must be landed (only landed rulers can spend the gold).
    """
    # No inspiration database → nothing to record; skip the (expensive)
    # living walks below rather than building a sponsor index that
    # points at records we don't carry.
    insp_root = data.get("inspirations_manager") or {}
    if not isinstance(insp_root, dict) or not isinstance(insp_root.get("database") or {}, dict):
        return {}, {}

    # First pass: artisan reverse-lookup. Walk living once, collect
    # inspiration_id -> artisan_character_id.
    artisan_for: dict[int, int] = {}
    living = data.get("living") or {}
    if isinstance(living, dict):
        for raw_cid, char in living.items():
            if not isinstance(char, dict):
                continue
            alive = char.get("alive_data")
            if not isinstance(alive, dict):
                continue
            iid = _coerce_int(alive.get("inspiration"))
            if iid is None:
                continue
            cid = _coerce_int(raw_cid)
            if cid is None:
                continue
            artisan_for[iid] = cid

    # Second pass: build the inspiration records.
    inspirations: dict[int, InspirationSnapshot] = {}
    for iid, raw in _iter_db_records(data, "inspirations_manager", "database"):
        inspirations[iid] = InspirationSnapshot(
            inspiration_id=iid,
            inspiration_type=_opt_str(raw, "type"),
            sponsored=_opt_str(raw, "sponsored"),
            total_cost=_coerce_int(raw.get("total_cost")) or 0,
            progress=_coerce_int(raw.get("progress")) or 0,
            artisan_character_id=artisan_for.get(iid),
        )

    # Third pass: sponsor reverse-lookup via landed_data.
    builder: dict[int, set[int]] = {}
    if isinstance(living, dict):
        for raw_cid, char in living.items():
            if not isinstance(char, dict):
                continue
            landed = char.get("landed_data")
            if not isinstance(landed, dict):
                continue
            si = landed.get("sponsored_inspirations")
            if not isinstance(si, list):
                continue
            cid = _coerce_int(raw_cid)
            if cid is None:
                continue
            for raw_iid in si:
                iid = _coerce_int(raw_iid)
                if iid is None:
                    continue
                builder.setdefault(cid, set()).add(iid)

    return inspirations, _freeze_index(builder)


def _parse_contracts(
    data: dict[str, Any],
) -> tuple[dict[int, ContractSnapshot], dict[int, frozenset[int]]]:
    """ck3_chronicler-621o (8aie slice 6): build
    (task_contracts, character_to_contracts) from
    ``task_contracts.database``.

    Mirrors :func:`_parse_activities` end-to-end. Each entry in the
    database is keyed by contract_id; the record carries ``type``,
    ``name``, ``tier``, ``employer``, ``owner``, ``location``,
    ``status``, and (when present) ``acceptance_date`` /
    ``completion_date``. Unlike activities — which prune on
    conclusion — completed contracts persist in the database long
    enough that the diff catches the status transition directly.

    The per-character reverse index covers only ``owner_id`` (the
    adventurer holding the contract). ``employer_id`` is informational
    payload data; the contract isn't a participation of the employer.

    Defensive: non-dict database entries are silently skipped (same
    pattern as _parse_activities).
    """
    contracts: dict[int, ContractSnapshot] = {}
    builder: dict[int, set[int]] = {}
    for cid, raw in _iter_db_records(data, "task_contracts", "database"):
        status_val = _opt_str(raw, "status")
        if status_val is None:
            # status is the load-bearing field — skip records without one
            continue
        owner_id = _coerce_int(raw.get("owner"))
        contracts[cid] = ContractSnapshot(
            contract_id=cid,
            contract_type=_opt_str(raw, "type"),
            name=_opt_str(raw, "name"),
            tier=_coerce_int(raw.get("tier")),
            employer_id=_coerce_int(raw.get("employer")),
            owner_id=owner_id,
            location_province_id=_coerce_int(raw.get("location")),
            status=status_val,
            acceptance_date=_opt_str(raw, "acceptance_date"),
            completion_date=_opt_str(raw, "completion_date"),
        )
        if owner_id is not None:
            builder.setdefault(owner_id, set()).add(cid)
    return contracts, _freeze_index(builder)


def _parse_court_positions(
    data: dict[str, Any],
) -> tuple[dict[int, CourtPositionSnapshot], dict[int, frozenset[int]]]:
    """ck3_chronicler-mke9 (8aie slice 7): build
    (court_positions, character_to_court_positions) from
    ``court_positions.database``.

    Each entry binds an employee to an employer in a named court
    position. The reverse index keys by ``employer_id`` so the diff
    layer can answer "which positions does the tracked character
    currently fill in their camp/court" without scanning the whole
    database every tick.

    Defensive: skip records without an employer (orphaned slot
    entries) or with non-string court_position keys. Records with
    no employee_id are kept — they represent vacant slots, and the
    diff layer filters them out by sentinel value rather than parser.
    """
    positions: dict[int, CourtPositionSnapshot] = {}
    builder: dict[int, set[int]] = {}
    for pid, raw in _iter_db_records(data, "court_positions", "database"):
        employer_id = _coerce_int(raw.get("employer"))
        if employer_id is None:
            # No employer to anchor the reverse index against — skip.
            continue
        positions[pid] = CourtPositionSnapshot(
            position_id=pid,
            court_position=_opt_str(raw, "court_position"),
            employee_id=_coerce_int(raw.get("employee")),
            employer_id=employer_id,
            hire_date=_opt_str(raw, "hire_date"),
        )
        builder.setdefault(employer_id, set()).add(pid)
    return positions, _freeze_index(builder)


def _parse_domiciles(
    data: dict[str, Any],
    titles: dict[int, TitleSnapshot],
) -> tuple[dict[int, DomicileSnapshot], dict[int, int]]:
    """ck3_chronicler-r343 (8aie slice 5): build
    (domiciles, character_to_domicile) from ``domiciles.database``.

    Each domicile entry carries ``province``, ``owner_title``, and
    ``domicile_type``. The ``character_to_domicile`` reverse index
    joins through the owner_title -> title-holder chain so the diff
    layer can answer "which domicile does this character currently
    occupy" in O(1).

    Defensive: records without an owner_title can't anchor a
    character — skipped silently (no diff-relevant signal). Domiciles
    whose owner_title doesn't resolve to a current holder are still
    parsed (the title may have just been vacated) but they don't
    contribute to character_to_domicile.
    """
    domiciles: dict[int, DomicileSnapshot] = {}
    char_to_domicile: dict[int, int] = {}
    for did, raw in _iter_db_records(data, "domiciles", "database"):
        owner_title_id = _coerce_int(raw.get("owner_title"))
        if owner_title_id is None:
            continue
        domiciles[did] = DomicileSnapshot(
            domicile_id=did,
            owner_title_id=owner_title_id,
            domicile_type=_opt_str(raw, "domicile_type"),
            province_id=_coerce_int(raw.get("province")),
        )
        # Join through the title -> holder. If the title isn't in the
        # title index (unusual but possible), skip the reverse-index
        # entry; the domicile is still parsed for completeness.
        title = titles.get(owner_title_id)
        if title is not None and title.holder_id is not None:
            char_to_domicile[title.holder_id] = did
    return domiciles, char_to_domicile


def _parse_constructions_and_buildings(
    data: dict[str, Any],
) -> tuple[
    dict[tuple[int, int], ConstructionSnapshot],
    dict[int, frozenset[tuple[int, int]]],
    dict[tuple[int, int], str],
]:
    """ck3_chronicler-2ur: walk ``provinces[pid].holding`` to extract
    in-flight constructions and the completed-buildings slot map.

    Returns a tuple of three:

    - ``in_flight``: ``{(province_id, slot_index): ConstructionSnapshot}``
      — every active construction across the world, keyed for O(1)
      lookup by the diff layer.
    - ``character_to_constructions``: ``{constructor_id: frozenset[(pid, slot)]}``
      — the per-character set the diff iterates per tracked character.
      Mirrors :data:`character_to_wars` / :data:`character_to_epidemics`.
    - ``holding_buildings_by_slot``: ``{(province_id, slot_index): building_type}``
      — completed-buildings flat lookup. Empty / placeholder entries
      (CK3 emits ``[]`` and ``{}`` for unfilled slots) are skipped so a
      slot only appears here when something is actually built. The diff
      uses this to distinguish a shipped construction (slot now matches
      the in-flight ``building``) from a cancelled one (slot still
      empty / different type).

    Permissive: malformed province / holding / construction entries are
    skipped without raising — chronicler's "never stop on bad data"
    rule. Only the regular ``buildings[]`` slot is captured here;
    ``great_building`` / ``special_building`` / ``duchy_capital_building``
    are deferred to a future slice when we have a live save with active
    wonder construction to test against."""
    in_flight: dict[tuple[int, int], ConstructionSnapshot] = {}
    char_to_cons: dict[int, set[tuple[int, int]]] = {}
    buildings_by_slot: dict[tuple[int, int], str] = {}

    provinces = data.get("provinces") or {}
    if not isinstance(provinces, dict):
        return {}, {}, {}

    for raw_pid, pdata in provinces.items():
        if not isinstance(pdata, dict):
            continue
        pid = _coerce_int(raw_pid)
        if pid is None:
            continue
        holding = pdata.get("holding")
        if not isinstance(holding, dict):
            continue

        # Completed buildings: enumerate buildings[] and record the type
        # at each non-empty slot. Empty slots are emitted by CK3 as []
        # or {} (no ``type`` key); skip those silently.
        buildings = holding.get("buildings")
        if isinstance(buildings, list):
            for slot_index, b in enumerate(buildings):
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if isinstance(btype, str) and btype:
                    buildings_by_slot[(pid, slot_index)] = btype

        # In-flight constructions: a single dict on the holding (not a
        # list — the user's live save has one record per holding even
        # for tribal holdings with multiple slot upgrades possible).
        # Defensive against future shape drift: tolerate either a dict
        # (single record) or a list (parallel records).
        cons_raw = holding.get("constructions")
        if isinstance(cons_raw, dict):
            cons_iter: list[dict[str, Any]] = [cons_raw]
        elif isinstance(cons_raw, list):
            cons_iter = [c for c in cons_raw if isinstance(c, dict)]
        else:
            cons_iter = []

        for cons in cons_iter:
            slot = _coerce_int(cons.get("index"))
            if slot is None:
                continue
            building = cons.get("building")
            if not isinstance(building, str) or not building:
                continue
            character_id = _coerce_int(cons.get("character"))
            start_time = cons.get("start_time")
            start_date_str = str(start_time) if isinstance(start_time, str) and start_time else None
            key = (pid, slot)
            in_flight[key] = ConstructionSnapshot(
                province_id=pid,
                slot_index=slot,
                building=building,
                start_date=start_date_str,
                character_id=character_id,
            )
            if character_id is not None:
                char_to_cons.setdefault(character_id, set()).add(key)

    character_to_constructions = {cid: frozenset(keys) for cid, keys in char_to_cons.items()}
    return in_flight, character_to_constructions, buildings_by_slot


# A real rakaly-converted save has ~75 top-level keys; synthetic test
# dicts have ~6-10. Below this we assume a fixture and stay quiet.
_REAL_SAVE_MIN_TOP_LEVEL_KEYS = 20


def _sanity_log(data: dict[str, Any], snap: SaveSnapshot) -> None:
    """Warn when a real-looking save parses hollow (M-S3, 27ov.18).

    Every section read in this module is permissive (``data.get(...) or
    {}``), so a CK3 patch renaming a database root yields a structurally
    valid but empty snapshot whose only downstream symptom is "tracked
    characters stopped updating" — with nothing in the logs. This is the
    one place parse drift becomes observable.
    """
    if len(data) < _REAL_SAVE_MIN_TOP_LEVEL_KEYS:
        return
    problems: list[str] = []
    if not snap.characters:
        problems.append("0 characters ('living'/'dead_unprunable' roots)")
    if not snap.titles:
        problems.append("0 titles ('landed_titles' root)")

    # A missing player is NOT evidence of drift on its own: CK3 writes
    # autosave_exit.ck3 with no `played_character` root, and an
    # observer-mode game never has one. Only call drift when a root that
    # every save must carry came back empty. Conflating the two sent one
    # investigation looking for a renamed save format that did not exist.
    if snap.player_character_id is None and not problems:
        log.info(
            "parse_save: no player character in this save "
            "(autosave_exit.ck3 and observer-mode games have none); "
            "ck3_version=%r date=%r",
            snap.ck3_version,
            snap.current_date,
        )
        return

    if snap.player_character_id is None:
        problems.append("no player_character_id")

    if problems:
        log.warning(
            "parse_save: save with %d top-level keys parsed hollow: %s. "
            "Likely CK3 format drift (renamed save-file roots); "
            "ck3_version=%r date=%r",
            len(data),
            "; ".join(problems),
            snap.ck3_version,
            snap.current_date,
        )
    else:
        log.debug(
            "parse_save: %d characters, %d titles, %d wars, %d artifacts, player=%s, date=%s",
            len(snap.characters),
            len(snap.titles),
            len(snap.wars),
            len(snap.artifacts),
            snap.player_character_id,
            snap.current_date,
        )


def parse_save(data: dict[str, Any]) -> SaveSnapshot:
    """Convert a rakaly-JSON dict into a :class:`SaveSnapshot`.

    Permissive — missing fields produce ``None`` / empty defaults rather
    than raising. The save format is large and multi-version; we extract
    what we know and leave anything unfamiliar untouched.
    """
    meta = data.get("meta_data") or {}
    main_portrait = meta.get("meta_main_portrait") or {}

    memory_db = (data.get("character_memory_manager") or {}).get("database") or {}
    memory_index = _build_memory_index(memory_db)

    # ck3_chronicler-57j: parse cultures_lookup BEFORE the character loop
    # so _parse_character can resolve culture template names and feed
    # them into decode_ck3_name. Culture-aware decoding disambiguates the
    # mid-word capital escapes that the culture-blind path lossily strips.
    cultures_lookup = _parse_cultures_lookup(data)

    characters: dict[int, CharacterSnapshot] = {}

    living = data.get("living") or {}
    if isinstance(living, dict):
        for raw_id, raw in living.items():
            cid = _coerce_int(raw_id)
            if cid is None or not isinstance(raw, dict):
                continue
            characters[cid] = _parse_character(
                cid,
                raw,
                is_dead=False,
                memory_index=memory_index,
                cultures_lookup=cultures_lookup,
            )

    dead = data.get("dead_unprunable") or {}
    if isinstance(dead, dict):
        for raw_id, raw in dead.items():
            cid = _coerce_int(raw_id)
            if cid is None or not isinstance(raw, dict):
                continue
            # If a char somehow appears in both (shouldn't), prefer dead
            characters[cid] = _parse_character(
                cid,
                raw,
                is_dead=True,
                memory_index=memory_index,
                cultures_lookup=cultures_lookup,
            )

    # ck3_chronicler-izgr: freshly-dead characters land in the NESTED
    # characters.dead_prunable bucket — distinct from the top-level
    # dead_unprunable — until CK3 garbage-collects them. A just-succeeded
    # player ruler dies here, so omitting this bucket meant the diff never
    # saw them as dead (curr.characters.get(cid) was None → diff.py treated
    # the disappearance as routine pruning), silently dropping the
    # DeathEvent / death_date / biography. Root-caused live on campaign
    # cf5201ff (Nobuhiro #33618293, died 1142.3.31).
    dead_prunable = (data.get("characters") or {}).get("dead_prunable") or {}
    if isinstance(dead_prunable, dict):
        for raw_id, raw in dead_prunable.items():
            cid = _coerce_int(raw_id)
            if cid is None or not isinstance(raw, dict):
                continue
            characters[cid] = _parse_character(
                cid,
                raw,
                is_dead=True,
                memory_index=memory_index,
                cultures_lookup=cultures_lookup,
            )

    raw_traits_lookup = data.get("traits_lookup")
    traits_lookup: tuple[str, ...] = ()
    if isinstance(raw_traits_lookup, list):
        traits_lookup = tuple(str(t) for t in raw_traits_lookup)

    wars, character_to_wars = _parse_wars(data)
    artifacts, character_to_artifacts = _parse_artifacts(data)
    dynasty_tbl = _parse_dynasties(data)
    house_tbl = _parse_houses(data)
    epidemics, character_to_epidemics = _parse_epidemics(data)
    (
        in_flight_constructions,
        character_to_constructions,
        holding_buildings_by_slot,
    ) = _parse_constructions_and_buildings(data)
    activities, character_to_activities = _parse_activities(data)
    inspirations, character_to_sponsored_inspirations = _parse_inspirations(data)
    task_contracts, character_to_contracts = _parse_contracts(data)
    court_positions, character_to_court_positions = _parse_court_positions(data)
    titles = _parse_titles(data)
    domiciles, character_to_domicile = _parse_domiciles(data, titles)
    title_holders = _build_title_holders(titles)
    kingdoms_by_de_jure_empire = _build_kingdoms_by_de_jure_empire(titles)

    snap = SaveSnapshot(
        playthrough_id=_resolve_playthrough_id(data),
        ck3_version=str(meta.get("version", "")),
        bookmark_date=str(data["bookmark_date"]) if "bookmark_date" in data else None,
        current_date=str(meta.get("meta_date") or data.get("date") or ""),
        player_character_id=_coerce_int(main_portrait.get("id")),
        characters=characters,
        alliances=_parse_alliances(data),
        traits_lookup=traits_lookup,
        houses_lookup=house_tbl.names,
        cultures_lookup=cultures_lookup,
        faiths_lookup=_parse_faiths_lookup(data),
        dynasties_lookup=dynasty_tbl.names,
        house_to_dynasty=house_tbl.house_to_dynasty,
        titles=titles,
        title_holders=title_holders,
        kingdoms_by_de_jure_empire=kingdoms_by_de_jure_empire,
        wars=wars,
        character_to_wars=character_to_wars,
        artifacts=artifacts,
        character_to_artifacts=character_to_artifacts,
        dynasty_perks=dynasty_tbl.perks,
        dynasty_renown=dynasty_tbl.renown_accumulated,
        dynasty_heads=dynasty_tbl.heads,
        epidemics=epidemics,
        character_to_epidemics=character_to_epidemics,
        in_flight_constructions=in_flight_constructions,
        character_to_constructions=character_to_constructions,
        holding_buildings_by_slot=holding_buildings_by_slot,
        activities=activities,
        character_to_activities=character_to_activities,
        inspirations=inspirations,
        character_to_sponsored_inspirations=character_to_sponsored_inspirations,
        task_contracts=task_contracts,
        character_to_contracts=character_to_contracts,
        court_positions=court_positions,
        character_to_court_positions=character_to_court_positions,
        domiciles=domiciles,
        character_to_domicile=character_to_domicile,
        dynasties_renown=dynasty_tbl.renown_currency,
    )
    _sanity_log(data, snap)
    return snap


# ck3_chronicler-q1ai: tier inference helpers for runtime-scripted
# titles (``x_script_*`` and similar) whose engine key lacks the
# b_/c_/d_/k_/e_ prefix. Player-formed kingdoms via decision are the
# canonical case — Norse "Form Kingdom of X" creates an x_script title
# with kingdom-tier structure (duchies as de_jure_vassals, sitting under
# an empire as de_jure_liege). _tier_from_key returns "other" for the
# x_ prefix, so without this inference Örvar Sleggja's player-formed
# Kingdom of Norðreyjar lost the primary-title pick to his subordinate
# d_northern_isles duchy.
_TIER_DEMOTE: dict[str, str] = {
    "empire": "kingdom",
    "kingdom": "duchy",
    "duchy": "county",
    "county": "barony",
}


_TIER_PROMOTE: dict[str, str] = {
    "barony": "county",
    "county": "duchy",
    "duchy": "kingdom",
    "kingdom": "empire",
    "empire": "empire",  # already at the cap
}


_TIER_DESCENDING: tuple[str, ...] = ("empire", "kingdom", "duchy", "county", "barony")


def _infer_tier_for_title(
    tid: int,
    raws: dict[int, dict[str, Any]],
    memo: dict[int, str],
    inflight: set[int],
) -> str:
    """Resolve a title's tier, falling back to its position in the
    de_jure hierarchy when the engine key prefix is uninformative.

    Order:

    1. ``_tier_from_key`` (b_/c_/d_/k_/e_) — wins when present.
    2. Walk *up* to the de_jure_liege and demote one tier from there
       (an x_script title under an empire is a kingdom; under a kingdom
       is a duchy; etc.).
    3. If no usable parent (orphan), walk *down* to de_jure_vassals and
       promote one tier above the grandest known vassal.
    4. Otherwise leave as ``"other"`` — non-tiered titles (court
       positions, theocratic specials) shouldn't be ranked alongside
       landed titles in the primary-title pick anyway.

    ``inflight`` guards against pathological cycles in the de_jure
    chain; CK3 *should* present a DAG, but defensive code beats an
    unbounded recursion on malformed saves.
    """
    if tid in memo:
        return memo[tid]
    if tid in inflight:
        # Cycle — break by returning "other"; caller's outer loop will
        # commit a real value once recursion unwinds.
        return "other"
    raw = raws.get(tid)
    if raw is None:
        return "other"
    base = _tier_from_key(raw["key"])
    if base != "other":
        memo[tid] = base
        return base

    inflight.add(tid)
    try:
        parent_id = _coerce_int(raw.get("de_jure_liege"))
        if parent_id is not None and parent_id in raws:
            parent_tier = _infer_tier_for_title(parent_id, raws, memo, inflight)
            demoted = _TIER_DEMOTE.get(parent_tier)
            if demoted is not None:
                memo[tid] = demoted
                return demoted

        vassals = raw.get("de_jure_vassals")
        if isinstance(vassals, list) and vassals:
            vassal_tiers: set[str] = set()
            for v in vassals:
                vid = _coerce_int(v)
                if vid is not None and vid in raws:
                    vassal_tiers.add(_infer_tier_for_title(vid, raws, memo, inflight))
            for t in _TIER_DESCENDING:
                if t in vassal_tiers:
                    promoted = _TIER_PROMOTE[t]
                    memo[tid] = promoted
                    return promoted
    finally:
        inflight.discard(tid)

    # ck3_chronicler-27ov.76 (audit L3): memoise the completed-walk
    # fall-through. By here we've fully explored parent + vassals and
    # inflight is cleared, so "other" is this title's final answer for
    # the pass — caching it stops a re-walk every time the title is
    # referenced as another title's liege/vassal (quadratic on modded
    # saves full of non-tiered scripted titles). The cycle-break return
    # above stays un-memoised: it's provisional until recursion unwinds.
    memo[tid] = "other"
    return "other"


def _parse_titles(data: dict[str, Any]) -> dict[int, TitleSnapshot]:
    """Build {title_id: TitleSnapshot} from landed_titles.landed_titles
    (ck3_chronicler-dr9). Untyped untitled entries (no key field) are
    skipped silently — they're CK3 sentinel rows for the title-history
    backstore.

    Tier is taken from the engine key prefix (b_/c_/d_/k_/e_) and
    falls back to a de_jure-hierarchy walk for runtime-scripted titles
    that don't carry a standard prefix (ck3_chronicler-q1ai)."""
    titles_root = data.get("landed_titles") or {}
    if not isinstance(titles_root, dict):
        return {}
    titles_dict = titles_root.get("landed_titles") or {}
    if not isinstance(titles_dict, dict):
        return {}
    # First pass: collect raw records keyed by int title_id. Skipping
    # entries without a key (CK3 sentinel rows) here so the tier-
    # inference walk can lean on `raws[tid]` being well-formed.
    raws: dict[int, dict[str, Any]] = {}
    for raw_id, raw in titles_dict.items():
        if not isinstance(raw, dict):
            continue
        tid = _coerce_int(raw_id)
        if tid is None:
            continue
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            continue
        raws[tid] = raw

    # Second pass: resolve tiers (memoised) and build TitleSnapshot rows.
    tier_memo: dict[int, str] = {}
    out: dict[int, TitleSnapshot] = {}
    for tid, raw in raws.items():
        name_data = raw.get("title_name_data") or {}
        name = name_data.get("name") if isinstance(name_data, dict) else None
        # ck3_chronicler-4u14: title display names from title_name_data
        # can include holder-tooltip wrappers in the same \x15 format
        # 90tv surfaced for war_name. Strip defensively so prose layers
        # downstream see the clean visible string.
        name = strip_loca_markup(name) if isinstance(name, str) else name
        out[tid] = TitleSnapshot(
            title_id=tid,
            key=raw["key"],
            name=name if isinstance(name, str) and name else None,
            tier=_infer_tier_for_title(tid, raws, tier_memo, set()),
            holder_id=_coerce_int(raw.get("holder")),
            de_jure_liege_id=_coerce_int(raw.get("de_jure_liege")),
        )
    return out


def _build_title_holders(
    titles: dict[int, TitleSnapshot],
) -> dict[int, frozenset[int]]:
    """ck3_chronicler-hk9i: holder_id -> frozenset(title_ids) reverse index.

    Mirrors :data:`SaveSnapshot.character_to_wars` /
    :data:`character_to_artifacts`. Built once at parse time so the diff
    layer turns its O(titles) per-character scan into an O(1) set lookup.
    """
    holders: dict[int, set[int]] = {}
    for tid, t in titles.items():
        if t.holder_id is None:
            continue
        holders.setdefault(t.holder_id, set()).add(tid)
    return {hid: frozenset(tids) for hid, tids in holders.items()}


def _build_kingdoms_by_de_jure_empire(
    titles: dict[int, TitleSnapshot],
) -> dict[int, frozenset[int]]:
    """ck3_chronicler-hk9i: empire_title_id -> frozenset(kingdom_title_ids).

    Walks each kingdom-tier title's ``de_jure_liege`` chain once to find
    the empire it sits inside. Eliminates the twice-per-character full
    ``snap.titles.values()`` scan that ``summarise_region`` previously
    needed for the own_kingdoms + peers loops.
    """
    by_empire: dict[int, set[int]] = {}
    for tid, t in titles.items():
        if t.tier != "kingdom":
            continue
        seen: set[int] = set()
        current: TitleSnapshot | None = t
        empire_id: int | None = None
        while current is not None and current.title_id not in seen:
            if current.tier == "empire":
                empire_id = current.title_id
                break
            seen.add(current.title_id)
            if current.de_jure_liege_id is None:
                break
            current = titles.get(current.de_jure_liege_id)
        if empire_id is not None:
            by_empire.setdefault(empire_id, set()).add(tid)
    return {eid: frozenset(kids) for eid, kids in by_empire.items()}


def _resolve_dynasty_or_house_name(raw: dict[str, Any]) -> str | None:
    """Pick the most user-readable name from a dynasty or dynasty_house record.

    Priority (ck3_chronicler-45i):

    1. ``localized_name`` — display string set when the dynasty was renamed
       in-game (player-customised, or some pre-defined cultural houses).
    2. ``custom_name`` — older CK3 variant of the same field.
    3. ``name`` — engine slug (``"dynn_Orsini"``); not localised but at
       least a stable identifier and the only field present on most
       engine-defined houses.
    4. ``key`` — only when it's a *string* slug (``"house_munso"``,
       ``"VIET_dreamer_dynasty"``). Integer ``key`` values are
       localisation-table indices that we cannot resolve here without the
       CK3 gamefiles localisation layer, so they are skipped.

    F-52: this is *not* a duplicate of
    :func:`chronicler.save.localization.decode_house_name`. They are
    complementary: this picks *which field* to read from a save record
    (dict-in, string-out), then ``decode_house_name`` polishes the
    resulting string (string-in, string-out — strips the ``dynn_`` /
    ``house_`` prefix and decodes embedded diacritic escapes). The
    pipeline is parse → resolve_field → decode_for_display.
    """
    for field_name in ("localized_name", "custom_name", "name"):
        v = raw.get(field_name)
        if isinstance(v, str) and v:
            return v
    k = raw.get("key")
    if isinstance(k, str) and k:
        return k
    return None


class _DynastyTables(NamedTuple):
    """Per-dynasty lookups built in a single walk over ``dynasties.dynasties``.

    ck3_chronicler-27ov.76 (audit L1): these five maps used to come from
    five separate full walks over the same (potentially multi-thousand-
    entry) table. :func:`_parse_dynasties` builds them all in one pass.
    """

    names: dict[int, str]  # display name (ck3_chronicler-45i resolution)
    perks: dict[int, frozenset[str]]  # legacy keys (ck3_chronicler-d83)
    heads: dict[int, int]  # current dynasty_head character id (ei8t)
    renown_accumulated: dict[int, float]  # prestige.accumulated — splendor crossings (ei8t)
    renown_currency: dict[int, float]  # prestige.currency — welcome-page renown (wdhe)


class _HouseTables(NamedTuple):
    """Per-house lookups built in a single walk over ``dynasties.dynasty_house``."""

    names: dict[int, str]  # display name for HouseChangeEvent / Character.house_name (667)
    house_to_dynasty: dict[int, int]  # parent-dynasty edge for the importer's name chain


def _parse_dynasties(data: dict[str, Any]) -> _DynastyTables:
    """One walk over ``dynasties.dynasties`` → all per-dynasty lookups.

    Name resolution follows :func:`_resolve_dynasty_or_house_name`
    (localized_name / custom_name / name / string key); dynasties whose
    only identifier is an integer ``key`` (a localisation-table index)
    leave no name entry, so Character.dynasty_name stays NULL until a
    localisation pass is added. Renown is split: ``accumulated`` (lifetime,
    drives splendor-tier crossings) and ``currency`` (current balance, the
    welcome-page figure) — CK3 stores both under the ``prestige`` key (the
    engine name; the UI calls it "renown").
    """
    names: dict[int, str] = {}
    perks: dict[int, frozenset[str]] = {}
    heads: dict[int, int] = {}
    renown_accumulated: dict[int, float] = {}
    renown_currency: dict[int, float] = {}
    for did, raw in _iter_db_records(data, "dynasties", "dynasties"):
        name = _resolve_dynasty_or_house_name(raw)
        if name:
            names[did] = name
        perks_raw = raw.get("perk")
        if isinstance(perks_raw, list):
            perk_set = frozenset(p for p in perks_raw if isinstance(p, str) and p)
            if perk_set:
                perks[did] = perk_set
        head = _coerce_int(raw.get("dynasty_head"))
        if head is not None:
            heads[did] = head
        prestige = raw.get("prestige")
        if isinstance(prestige, dict):
            accumulated = _coerce_float(prestige.get("accumulated"))
            if accumulated is not None:
                renown_accumulated[did] = accumulated
        currency = _parse_currency(prestige)
        if currency is not None:
            renown_currency[did] = currency
    return _DynastyTables(names, perks, heads, renown_accumulated, renown_currency)


def _parse_houses(data: dict[str, Any]) -> _HouseTables:
    """One walk over ``dynasties.dynasty_house`` → name + parent-dynasty maps.

    The dynasty edge lets the importer resolve a character to their parent
    dynasty (one hop past dynasty_house): character.dynasty_house_id →
    house_to_dynasty[house_id] → dynasties.names[dynasty_id]. Houses
    without a ``dynasty`` field or a resolvable name are skipped silently.
    """
    names: dict[int, str] = {}
    house_to_dynasty: dict[int, int] = {}
    for hid, raw in _iter_db_records(data, "dynasties", "dynasty_house"):
        name = _resolve_dynasty_or_house_name(raw)
        if name:
            names[hid] = name
        did = _coerce_int(raw.get("dynasty"))
        if did is not None:
            house_to_dynasty[hid] = did
    return _HouseTables(names, house_to_dynasty)


def _parse_cultures_lookup(data: dict[str, Any]) -> dict[int, str]:
    """Build {culture_id: name} from culture_manager.cultures. CK3's
    culture entries carry both ``name`` and ``culture_template`` —
    they're typically the same string ("akan", "norse"); we prefer
    ``name`` and fall back to ``culture_template``."""
    out: dict[int, str] = {}
    cm = (data.get("culture_manager") or {}).get("cultures") or {}
    if not isinstance(cm, dict):
        return out
    for raw_id, raw in cm.items():
        if not isinstance(raw, dict):
            continue
        cid = _coerce_int(raw_id)
        if cid is None:
            continue
        name = raw.get("name") or raw.get("culture_template")
        if isinstance(name, str) and name:
            out[cid] = name
    return out


def _parse_faiths_lookup(data: dict[str, Any]) -> dict[int, str]:
    """Build {faith_id: faith_type_string} from religion.faiths."""
    out: dict[int, str] = {}
    faiths = (data.get("religion") or {}).get("faiths") or {}
    if not isinstance(faiths, dict):
        return out
    for raw_id, raw in faiths.items():
        if not isinstance(raw, dict):
            continue
        cid = _coerce_int(raw_id)
        if cid is None:
            continue
        name = raw.get("faith_type") or raw.get("tag")
        if isinstance(name, str) and name:
            out[cid] = name
    return out
