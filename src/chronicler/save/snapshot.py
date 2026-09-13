"""SaveSnapshot and its component dataclasses (ck3_chronicler-27ov.36 / M-S4).

The pure data model produced by :func:`chronicler.save.parse.parse_save` —
split out of parse.py as the leaf of the save-parsing module graph. Imported
by parse.py, titles.py, autotrack.py and raw_record.py; re-exported from
parse.py for compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """One vanilla CK3 character memory."""

    memory_id: int
    memory_type: str
    creation_date: str
    end_date: str | None
    # role name (e.g. "spouse", "killer", "host") -> participant character ID
    participants: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class FamilySnapshot:
    """Family relations for a character. All fields are optional — the
    engine only emits keys when the relation exists.
    """

    mother: int | None = None
    father: int | None = None
    primary_spouse: int | None = None
    spouses: tuple[int, ...] = ()
    former_spouses: tuple[int, ...] = ()
    concubinist: int | None = None  # the partner if THIS char is the consubine's partner
    concubines: tuple[int, ...] = ()
    children: tuple[int, ...] = ()
    betrothed: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class CharacterSnapshot:
    """Plain-data snapshot of a CK3 character at one save moment."""

    ck3_id: int
    first_name: str | None
    nickname: str | None
    is_dead: bool
    female: bool
    birth_date: str | None
    death_date: str | None
    culture_id: int | None
    faith_id: int | None
    dynasty_house_id: int | None
    ethnicity: str | None
    traits: tuple[int, ...]
    family: FamilySnapshot
    # Province ID where the character currently resides. Used by diff to
    # detect travel events (location changed between snapshots).
    location_id: int | None = None
    memories: tuple[MemorySnapshot, ...] = ()
    # ck3_chronicler-caxv: dead_data.reason / dead_data.killer when the
    # save tree carries them (every dead char has a `reason` string;
    # `killer` is set on murders / executions / battle deaths). None on
    # alive chars or when CK3 didn't record the field. Default-bearing
    # fields are kept at the bottom of the dataclass so existing
    # positional CharacterSnapshot(...) constructions in tests still
    # work without rewiring.
    death_cause: str | None = None
    death_killer: int | None = None
    # ck3_chronicler-mcu: government type from landed_data.government, if
    # the character holds any title. Most narratively useful as the
    # detector for Roads to Power adventurer mode — value
    # 'landless_adventurer_government' means a wandering character.
    # None when the character is unlanded / has no landed_data block.
    government: str | None = None
    # ck3_chronicler-jrwe: decisions the character is currently on
    # cooldown for, from landed_data.decision_cooldowns. Each entry is
    # (decision_id, cooldown_end_date) — sorted by decision_id for
    # deterministic diff. The diff layer uses set-difference across
    # adjacent ticks to detect freshly-taken decisions and emit
    # DecisionTakenEvent for narratively interesting ones (runestones,
    # etc.). Empty tuple for unlanded characters or those who haven't
    # taken any cooldown-bearing decisions.
    decisions_taken: tuple[tuple[str, str], ...] = ()
    # ck3_chronicler-wdhe: current gold/prestige/piety on the character,
    # plus prestige_lifetime and piety_lifetime (the accrued counters —
    # what the welcome page surfaces as "prestige/piety gathered"). CK3
    # stores these under alive_data.{gold,prestige,piety} either as a
    # flat scalar (older saves) or as a {value, accrued} dict —
    # _parse_currency handles both. None when the field is missing or
    # the character is dead (alive_data is replaced by dead_data, which
    # doesn't carry currency state).
    gold: float | None = None
    prestige: float | None = None
    prestige_lifetime: float | None = None
    piety: float | None = None
    piety_lifetime: float | None = None
    # ck3_chronicler-n0s4: character_modifier engine keys
    # ("devoted_to_ullr", "mourning_son", event-granted mods, etc.).
    # Tuple preserves insertion order from the save; the diff layer
    # treats it as a set for delta computation and emits
    # modifier_acquired events on new entries. Default-bearing so
    # existing positional constructors in tests keep working without
    # rewiring (same convention as the surrounding default fields).
    modifiers: tuple[str, ...] = ()
    # ck3_chronicler-rgay: lifestyle perk engine keys living on
    # ``alive_data.perk`` (e.g. ``bellum_justum_perk``,
    # ``parthian_tactics_perk``, ``schemer_perk``). Same set-diff
    # treatment as modifiers — acquired-only, diff emits
    # perk_acquired on new entries.
    perks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """ck3_chronicler-d83: one CK3 artifact at one save moment.

    Lives at top-level ``artifacts.artifacts[<id>]`` with an owner
    (bare character_id int) and metadata (name, type, rarity).
    Lifecycle is mostly tracked via owner changes — the engine deletes
    the entry on destruction (similar to wars on conclusion).
    """

    artifact_id: int
    name: str | None
    type: str | None
    rarity: str | None
    owner_id: int | None


@dataclass(frozen=True, slots=True)
class ConstructionSnapshot:
    """ck3_chronicler-2ur: one in-flight building construction at one
    save moment.

    CK3 stores per-holding constructions at
    ``provinces[pid].holding.constructions`` (a dict per province) with
    ``building`` (engine type string), ``index`` (slot in
    ``buildings[]`` where the building will land on completion),
    ``start_time``, ``days`` (remaining), ``cost``, and ``character``
    (constructor's ck3_id). The construction record disappears when the
    building ships (its slot in ``buildings[]`` is now filled with that
    type) OR is cancelled (the slot stays empty / unchanged).

    The diff layer keys (province_id, slot_index) so the same holding
    can have parallel constructions on different slots, and successive
    upgrades to the same slot register as separate completions."""

    province_id: int
    slot_index: int
    building: str
    start_date: str | None
    character_id: int | None


@dataclass(frozen=True, slots=True)
class EpidemicSnapshot:
    """ck3_chronicler-9wrd: one CK3 epidemic at one save moment.

    CK3 stores active epidemics at top-level ``epidemics.database[<id>]``
    while they're spreading and prunes them when they conclude — same
    lifecycle shape as wars and alliances. The diff layer infers
    conclusion from epidemic_id disappearing across adjacent snapshots.

    Fields surface the metadata biographies need to anchor the period:
    ``name`` is the in-game name like 'Pope Alexander's Boils' or
    'Yamato Boils', ``epidemic_type`` is the engine string ('measles',
    'consumption', etc.), ``intensity`` is one of CK3's enum values
    ('major', 'minor', 'devastating'). The numerical breakdown
    (``num_infected_provinces``, ``num_infected_characters``,
    ``num_character_deaths``) lets prompts say things like 'a plague
    that killed thousands' without inventing scale.
    """

    epidemic_id: int
    epidemic_type: str | None
    name: str | None
    intensity: str | None
    creation_date: str | None
    start_province: int | None
    num_infected_provinces: int
    num_infected_characters: int
    num_character_deaths: int


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """ck3_chronicler-qx7n (8aie slice 1): one CK3 activity at one save moment.

    CK3 stores active activities at top-level
    ``activity_manager.database[<activity_id>]`` while they're
    scheduled or in progress and prunes them on conclusion — same
    lifecycle shape as wars and epidemics. The diff layer infers
    completion from the activity_id disappearing from a tracked
    character's slice of the per-character reverse index.

    ``activity_type`` is the engine string ("activity_pilgrimage",
    "activity_feast", "activity_hunt", "activity_adult_education",
    ...). ``host_id`` is the character who scheduled the activity;
    ``attendees`` is the ``attending`` list verbatim (NPCs included —
    the diff filters to tracked chars at iteration time). The host
    is NOT in ``attendees``; the reverse index built by
    :func:`_parse_activities` covers both host and attendees so the
    diff can answer "did this character's activity participation
    end" without re-checking the host field.

    ``start_province_id`` is the province from the first phase entry
    when present (multi-phase activities like long pilgrimages walk
    multiple provinces, but for narrative purposes "where did it
    start" is the load-bearing field). None when ``phases`` is
    missing or its first entry has no ``province``.
    """

    activity_id: int
    activity_type: str | None
    host_id: int | None
    creation_date: str | None
    active_start_date: str | None
    start_province_id: int | None
    attendees: frozenset[int]


@dataclass(frozen=True, slots=True)
class InspirationSnapshot:
    """ck3_chronicler-m658 (8aie slice 2): one CK3 inspiration at one
    save moment.

    CK3 stores inspirations at top-level
    ``inspirations_manager.database[<inspiration_id>]``. Each record
    carries ``type`` (engine string — ``weapon_inspiration``,
    ``book_inspiration``, ``adventure_inspiration``, etc.),
    ``total_cost``, ``progress``, ``created`` and ``sponsored`` date
    strings. The ``sponsored`` field is the load-bearing signal: it
    holds CK3's '1.1.1' sentinel while the artisan self-funds, and
    transitions to a real date the moment a sponsor (typically the
    player) commits gold.

    ``artisan_character_id`` is derived at parse-time from the reverse
    lookup: each living character's ``alive_data.inspiration`` (singular)
    points at one inspiration_id. The artisan is the NPC carrying out
    the work; the sponsor is identified separately via
    ``character_to_sponsored_inspirations``.
    """

    inspiration_id: int
    inspiration_type: str | None
    sponsored: str | None  # CK3 '1.1.1' sentinel or real date string
    total_cost: int
    progress: int
    artisan_character_id: int | None


@dataclass(frozen=True, slots=True)
class DomicileSnapshot:
    """ck3_chronicler-r343 (8aie slice 5): one occupied domicile at
    one save moment.

    CK3 stores domiciles at top-level ``domiciles.database[<domicile_id>]``.
    Each entry has a ``province`` (the current location), ``owner_title``
    (the title binding it to a character), and ``domicile_type``
    (``camp`` for mobile RtP adventurer camps, ``east_asian_estate`` /
    ``herd`` etc. for stationary or culture-specific forms).

    The diff layer fires ``domicile_moved`` when a tracked character's
    domicile's ``province`` changes between snapshots — capturing the
    picaresque "she ranged from Iberia to the Rus" arc that adventurer
    obituaries today lose entirely.

    The ``character_to_domicile`` reverse index (top-level on
    SaveSnapshot) joins via ``owner_title`` → title-holder lookup, so
    consumers don't have to walk landed_data.domain themselves.
    """

    domicile_id: int
    owner_title_id: int | None
    domicile_type: str | None
    province_id: int | None


@dataclass(frozen=True, slots=True)
class CourtPositionSnapshot:
    """ck3_chronicler-mke9 (8aie slice 7): one occupied court position
    at one save moment.

    CK3 stores court positions at top-level
    ``court_positions.database[<position_id>]``. Each entry binds an
    ``employee`` (the named character serving) to an ``employer`` (the
    holder of the court — for adventurers, this is the player's
    landless-adventurer-camp owner; for landed characters this is the
    landed ruler). ``court_position`` is the engine key
    (``travel_leader_court_position``, ``bodyguard_court_position``,
    ``second_camp_officer``, ``stooge_camp_officer``, etc.). RtP
    camp-officer roles drop the ``_court_position`` suffix —
    renderers must tolerate both shapes.

    The same employee can hold multiple positions simultaneously
    (observed live: Shigemoto_3 holding both second_camp_officer and
    bodyguard_court_position). Each instance has its own position_id,
    so the diff dedups by position_id rather than employee_id.
    """

    position_id: int
    court_position: str | None
    employee_id: int | None
    employer_id: int | None
    hire_date: str | None


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    """ck3_chronicler-621o (8aie slice 6): one RtP adventurer
    contract at one save moment.

    CK3 stores active and recently-completed contracts at top-level
    ``task_contracts.database[<contract_id>]``. Lifecycle: ``available``
    (offered but not accepted) → ``accepted`` (in progress, with
    ``acceptance_date``) → ``completed`` | ``invalidated`` (terminal,
    with ``completion_date``). The diff layer emits
    ``contract_completed`` on transitions into a terminal state.

    ``contract_type`` is the engine string (e.g. ``laamp_base_6021``
    — RtP's internal ``laamp`` prefix is "landless-adventurer-mp"
    codename); ``name`` is the display string (e.g. "Perform in a
    Play"); ``employer_id`` is the issuer; ``owner_id`` is the
    adventurer holding the contract. ``location_province_id`` is the
    target province.
    """

    contract_id: int
    contract_type: str | None
    name: str | None
    tier: int | None
    employer_id: int | None
    owner_id: int | None
    location_province_id: int | None
    status: str
    acceptance_date: str | None
    completion_date: str | None


@dataclass(frozen=True, slots=True)
class WarSnapshot:
    """ck3_chronicler-o7j: one CK3 war at one save moment.

    CK3 stores wars at top-level ``wars.active_wars[<war_id>]`` while
    they're active and deletes the entry on conclusion (no engine-level
    "war ended" record). Save-tail infers conclusion from war_id
    disappearing across adjacent snapshots — same shape as alliances.

    ``primary_attacker_id`` / ``primary_defender_id`` come from the
    casus belli, which CK3 carries on the war record. The primaries
    are who "declared" the war (attacker) or "had it declared on them"
    (defender) — narratively distinct from rank-and-file participants
    pulled in via alliance or vassalage. ``casus_belli_type`` is the
    engine string ("claimant_faction_war", "holy_war", etc.).

    ``participants`` is the union of attacker.participants and
    defender.participants — every character actively fighting. Both
    sides' subsets are also retained so the diff can resolve which
    side a tracked character is on.
    """

    war_id: int
    name: str | None
    start_date: str | None
    casus_belli_type: str | None
    targeted_titles: tuple[int, ...]
    primary_attacker_id: int | None
    primary_defender_id: int | None
    claimant_id: int | None
    attacker_participants: frozenset[int]
    defender_participants: frozenset[int]


@dataclass(frozen=True, slots=True)
class TitleSnapshot:
    """ck3_chronicler-dr9: one CK3 landed title at one save moment.

    The engine ``key`` ("d_munster", "k_france") is the canonical
    identifier; ``name`` is the optionally-localised display name from
    title_name_data when CK3 stored one (most non-trivial titles do —
    duchies and above almost always; baronies and counties sometimes
    don't and the chronicler falls back to the key for those).
    ``tier`` is one of ``barony``, ``county``, ``duchy``, ``kingdom``,
    ``empire``, ``other`` — derived from the key prefix at parse time.

    ``de_jure_liege_id`` (ck3_chronicler-8ek) is the title id of this
    title's de jure parent — the structural ancestor in the kingdom →
    empire chain, not the feudal liege. None for top-level titles
    (empires) or any title where CK3 didn't emit a de_jure_liege field.
    Walking de_jure_liege ids upward from a county yields the de jure
    duchy → kingdom → empire ancestor chain.
    """

    title_id: int
    key: str
    name: str | None
    tier: str
    holder_id: int | None
    de_jure_liege_id: int | None = None


_TIER_PREFIX_MAP: dict[str, str] = {
    "b_": "barony",
    "c_": "county",
    "d_": "duchy",
    "k_": "kingdom",
    "e_": "empire",
}


def _tier_from_key(key: str) -> str:
    if len(key) >= 2 and key[1] == "_":
        return _TIER_PREFIX_MAP.get(key[:2], "other")
    return "other"


@dataclass(frozen=True, slots=True)
class SaveSnapshot:
    """Top-level snapshot extracted from one .ck3 save."""

    playthrough_id: str
    ck3_version: str
    bookmark_date: str | None
    current_date: str
    player_character_id: int | None
    characters: dict[int, CharacterSnapshot] = field(default_factory=dict)
    # ck3_chronicler-cc3: per-character set of allied character IDs,
    # extracted from top-level relations.active_relations. Keyed by
    # character_id; the value is the frozenset of characters they are
    # currently allied with. Bidirectional (if A is allied with B, both
    # entries appear). Absent character_id key means no known alliances.
    alliances: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-nzr: trait ID → trait name lookup, parsed from the
    # save's top-level traits_lookup list. CharacterSnapshot.traits stores
    # int IDs; this resolves them to readable names like 'faltering_heart'
    # / 'diligent' / 'education_diplomacy_3' for diff events and prompts.
    # Direct list-index lookup: traits_lookup[trait_id] -> name.
    traits_lookup: tuple[str, ...] = ()
    # ck3_chronicler-667: name lookups for state-change diff events +
    # tracked-character DB upserts. CK3's localised names live behind
    # the canonical engine key (house "dynn_Orsini", culture "akan",
    # faith_type "akom_pagan") — chronicler stores the engine key as
    # the "name" and lets prompt iteration / future localisation
    # decide how to humanise. Empty dict on missing → diff payloads
    # carry None for the name (the IDs always survive).
    houses_lookup: dict[int, str] = field(default_factory=dict)
    cultures_lookup: dict[int, str] = field(default_factory=dict)
    faiths_lookup: dict[int, str] = field(default_factory=dict)
    # ck3_chronicler-45i: dynasty (parent of dynasty_house) name lookup
    # and house→dynasty edge map. Built from dynasties.dynasties +
    # dynasties.dynasty_house.dynasty. The name resolves through a
    # priority chain (localized_name → custom_name → name → string key)
    # so player-customised dynasties surface their display name and
    # engine-defined ones fall back to slugs like "dynn_Briain". An
    # integer ``key`` (a localisation index) is intentionally skipped —
    # without the gamefiles localisation layer it would be a bare
    # number, less useful than NULL.
    dynasties_lookup: dict[int, str] = field(default_factory=dict)
    house_to_dynasty: dict[int, int] = field(default_factory=dict)
    # ck3_chronicler-dr9: top-level landed_titles indexed by id. Diff
    # layer reads this to emit TitleAcquired/Relinquished/Created
    # events; CharacterSnapshot intentionally does NOT carry per-character
    # title lists since titles can move between characters and the
    # canonical owner is on the title record itself.
    titles: dict[int, TitleSnapshot] = field(default_factory=dict)
    # ck3_chronicler-hk9i: holder_id -> frozenset(title_ids). Built once
    # at parse time so _diff_titles + summarise_region can do per-character
    # set lookups instead of scanning every title row per tracked character.
    # Mirror of the character_to_wars / character_to_artifacts pattern.
    # Populous bookmarks have 5k-15k landed titles × ~30 tracked = ~450k
    # title-row scans per tick before this index existed.
    title_holders: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-hk9i: empire_title_id -> frozenset(kingdom_title_ids)
    # whose de_jure_liege walk reaches that empire. Built once at parse
    # time by walking each kingdom's de_jure chain. summarise_region
    # iterates this instead of scanning every title row twice per
    # character (own_kingdoms + peers loops).
    kingdoms_by_de_jure_empire: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-o7j: active wars indexed by war_id, plus a
    # per-character set lookup so the diff can do alliance-style
    # set-diffs without scanning every war for every tracked
    # character. ``wars`` carries the metadata (CB type, sides);
    # ``character_to_wars[cid]`` is the frozenset of war_ids the
    # character is currently a participant in.
    wars: dict[int, WarSnapshot] = field(default_factory=dict)
    character_to_wars: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-d83: artifact ownership state. ``artifacts`` is
    # the metadata index (name, type, rarity); ``character_to_artifacts``
    # is the per-character set used by the diff to emit
    # ArtifactAcquired / ArtifactLost.
    artifacts: dict[int, ArtifactSnapshot] = field(default_factory=dict)
    character_to_artifacts: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-d83: per-dynasty unlocked legacy perks ("blood_legacy_1",
    # "ep1_culture_legacy_3", ...). Diff layer compares the set per
    # dynasty + emits DynastyLegacyUnlocked when the player's dynasty
    # crosses a tier — keyed to every tracked character in that dynasty.
    dynasty_perks: dict[int, frozenset[str]] = field(default_factory=dict)
    # ck3_chronicler-ei8t: per-dynasty lifetime accumulated renown.
    # Source: ``dynasties.dynasties[id].prestige.accumulated``. Drives
    # the splendor-tier derivation in :mod:`chronicler.save.dynasty_splendor`;
    # diff layer emits splendor_increased when the derived tier moves
    # upward between snapshots.
    dynasty_renown: dict[int, float] = field(default_factory=dict)
    # ck3_chronicler-ei8t: per-dynasty current head character id.
    # Source: ``dynasties.dynasties[id].dynasty_head``. Carried on the
    # splendor_increased payload for narrative context.
    dynasty_heads: dict[int, int] = field(default_factory=dict)
    # ck3_chronicler-9wrd: epidemics state. ``epidemics`` is the
    # metadata index (one per active epidemic with name + intensity +
    # numbers); ``character_to_epidemics[cid]`` is the frozenset of
    # epidemic_ids the character is currently infected by — the
    # per-character lookup the diff uses to emit epidemic_outbreak when
    # a tracked char newly appears in an epidemic's character list.
    # Mirror of the wars / character_to_wars + alliances shape.
    epidemics: dict[int, EpidemicSnapshot] = field(default_factory=dict)
    character_to_epidemics: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-2ur: in-flight building constructions indexed by
    # (province_id, slot_index). ``character_to_constructions[cid]`` is
    # the frozenset of (province_id, slot_index) pairs the constructor
    # is currently building — the per-character lookup the diff uses to
    # emit building_completed when an in-flight pair disappears AND the
    # corresponding slot in ``holding_buildings_by_slot`` matches the
    # tracked construction's building type. ``holding_buildings_by_slot``
    # is the snapshot's flat (province_id, slot_index) → building_type
    # lookup of *completed* buildings (skipping empty / placeholder
    # entries) — the disambiguator between a completed construction and
    # a cancelled one.
    in_flight_constructions: dict[tuple[int, int], ConstructionSnapshot] = field(
        default_factory=dict
    )
    character_to_constructions: dict[int, frozenset[tuple[int, int]]] = field(default_factory=dict)
    holding_buildings_by_slot: dict[tuple[int, int], str] = field(default_factory=dict)
    # ck3_chronicler-qx7n (8aie slice 1): active activities indexed by
    # activity_id. ``character_to_activities[cid]`` is the frozenset of
    # activity_ids the character is currently participating in (host
    # OR attendee). Diff layer emits activity_completed when an id
    # disappears from a tracked char's slice between adjacent
    # snapshots. Mirror of the wars / epidemics / constructions shape.
    activities: dict[int, ActivitySnapshot] = field(default_factory=dict)
    character_to_activities: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-m658 (8aie slice 2): inspirations indexed by
    # inspiration_id. ``character_to_sponsored_inspirations[cid]`` maps
    # a sponsor character_id -> the frozenset of inspiration_ids in
    # their landed_data.sponsored_inspirations list. The diff layer
    # emits inspiration_sponsored when a new inspiration_id enters a
    # tracked char's slice between adjacent snapshots.
    inspirations: dict[int, InspirationSnapshot] = field(default_factory=dict)
    character_to_sponsored_inspirations: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-621o (8aie slice 6): RtP adventurer contracts.
    # ``task_contracts`` keyed by contract_id; ``character_to_contracts``
    # maps owner_id (the adventurer) -> frozenset of contract_ids. The
    # diff layer joins prev/curr on contract_id to detect status
    # transitions into terminal states (completed | invalidated).
    task_contracts: dict[int, ContractSnapshot] = field(default_factory=dict)
    character_to_contracts: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-mke9 (8aie slice 7): occupied court positions
    # (camp companions for adventurers, court staff for landed
    # rulers). Diff layer set-diffs the per-employer position_id set
    # between snapshots; appearances emit CampCompanionJoinedEvent,
    # disappearances emit CampCompanionLeftEvent.
    court_positions: dict[int, CourtPositionSnapshot] = field(default_factory=dict)
    character_to_court_positions: dict[int, frozenset[int]] = field(default_factory=dict)
    # ck3_chronicler-r343 (8aie slice 5): domiciles indexed by
    # domicile_id. ``character_to_domicile`` maps a character_id
    # to their occupied domicile_id (joined at parse-time via the
    # domicile's owner_title -> title-holder chain). The diff layer
    # fires DomicileMovedEvent when a tracked character's domicile's
    # province_id changes between snapshots.
    domiciles: dict[int, DomicileSnapshot] = field(default_factory=dict)
    character_to_domicile: dict[int, int] = field(default_factory=dict)
    # ck3_chronicler-wdhe: per-dynasty current renown. CK3 surfaces this
    # on the dynasty record as ``prestige.currency`` (the engine still
    # calls it "prestige" internally; the in-game UI is "renown"). Only
    # consumed by the campaign-overview welcome page today, which keys
    # on the player's dynasty via house_to_dynasty + dynasty_house_id.
    dynasties_renown: dict[int, float] = field(default_factory=dict)
    # ck3_chronicler-j86v: per-tracked-character raw extractions resolved
    # INSIDE the parse worker process, so the consumer never needs the
    # 127 MB raw save dict back over the pool boundary. Keyed by ck3_id.
    # Empty on the import/sync paths (which still hold the raw dict and
    # pass it to hydrate_character_columns directly).
    tracked_raw_records: dict[int, dict] = field(default_factory=dict)
    tracked_coa: dict[int, dict] = field(default_factory=dict)
