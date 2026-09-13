"""Canonical event models for the chronicle pipeline.

The compact field names (``v``/``t``/``d``/``c``/``p``) date from the
original v0.1–0.4 transport: the CK3 mod printed one ``CHRONICLER|`` line
per event to debug.log, e.g.::

    CHRONICLER|v=1|t=death|d=16th of September, 1066 AD

The debug.log path still exists, but from v0.6 the bulk of events are
synthesised by the **save-state diff layer** (:mod:`chronicler.save.diff`)
rather than emitted by the mod — it diffs adjacent save snapshots and
constructs these same models directly. The shape is shared so both
sources land in one event store and one discriminated union.

Fields:

- ``v`` schema version — defaults to :data:`SCHEMA_VERSION`; bumped on any
  breaking change to a payload shape
- ``t`` event type discriminator — each model defaults it to its own
  literal, so construction sites needn't repeat it
- ``d`` in-game date string (CK3's GetCurrentDate.GetStringLong form)
- ``c`` primary character — CK3-assigned ID; for debug.log events from the
  paired scope dump (see :mod:`chronicler.tailer.parser`), for save-diff
  events the tracked character the diff is about
- ``p`` payload, shape depends on ``t``
v0.2 added birth, marriage, divorce, title_gain, title_lost, war_started,
war_won_attacker, war_won_defender, imprison, release (empty payloads —
participants come from the scope dump, not inline). v0.6+ added the
save-diff events (travel, traits, titles, wars, artifacts, …).

See docs/schema_versions.md for the per-version log.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _EmptyPayload(_StrictModel):
    """Placeholder payload for v0.2 events whose data is purely scope-dump-side.

    Empty by design — birth/marriage/etc. don't need inline payload fields
    because the engine scope dump provides participant IDs (mother, father,
    spouse, etc.) via :class:`chronicler.tailer.parser.IncrementalParser`.
    Future versions may add inline fields here when CK3 globals can supply
    them; that will require bumping ``v`` if existing fields shift shape.
    """


class DeathPayload(_StrictModel):
    killer: int | None = None
    cause: str | None = None
    # ck3_chronicler-caxv: deceased's age at death in whole years, computed
    # from birth_date + death_date when both are present on the snapshot.
    # None when either date is missing or unparseable. Surfaced to biography
    # prompts so they don't fabricate ages from the in-game date alone.
    death_age: int | None = None


class DeathEvent(_StrictModel):
    v: int = Field(SCHEMA_VERSION, description="Schema version")
    t: Literal["death"] = "death"
    d: str = Field(..., description="In-game date string")
    c: int = Field(..., description="Deceased character CK3 ID")
    p: DeathPayload


class BirthEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["birth"] = "birth"
    d: str
    c: int  # the newborn child
    p: _EmptyPayload


class MarriagePayload(_StrictModel):
    """ck3_chronicler-dnb: spouse identity for save-diff marriage events.

    Without this, the diff layer's marriage event was dedup-collisional
    on (event_type, event_date, primary_character_id, payload_json) —
    a polygamous tracked character marrying two spouses on the same
    in-game date would have the second event silently dropped — and
    biography prompts could not name the spouse without a hallucination
    risk. The participants tuple still carries ("spouse", spouse_id) for
    DB-level joins; this field surfaces it inside the canonical payload
    so dedup distinguishes per-spouse marriages and downstream prompts
    have a structured handle."""

    spouse_character_id: int | None = None


class MarriageEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["marriage"] = "marriage"
    d: str
    c: int  # ROOT scope (one of the spouses; the other lives in payload + participants)
    p: MarriagePayload


class DivorcePayload(_StrictModel):
    """ck3_chronicler-dnb: former-spouse identity for save-diff divorce events.

    Same rationale as :class:`MarriagePayload` — without a payload field,
    a tracked character divorcing two former spouses in one in-game
    month would dedup-collide and only the first divorce would persist.
    """

    former_spouse_character_id: int | None = None


class DivorceEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["divorce"] = "divorce"
    d: str
    c: int
    p: DivorcePayload


class TitleGainEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["title_gain"] = "title_gain"
    d: str
    c: int  # the character receiving the title
    p: _EmptyPayload


class TitleLostEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["title_lost"] = "title_lost"
    d: str
    c: int  # the character losing the title
    p: _EmptyPayload


class WarStartedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["war_started"] = "war_started"
    d: str
    c: int  # ROOT scope (war attacker or defender, depending on hook)
    p: _EmptyPayload


class WarWonAttackerEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["war_won_attacker"] = "war_won_attacker"
    d: str
    c: int
    p: _EmptyPayload


class WarWonDefenderEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["war_won_defender"] = "war_won_defender"
    d: str
    c: int
    p: _EmptyPayload


class ImprisonEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["imprison"] = "imprison"
    d: str
    c: int  # the imprisoned character
    p: _EmptyPayload


class ReleaseEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["release"] = "release"
    d: str
    c: int  # the released character
    p: _EmptyPayload


# v0.6 — events recovered from save state diffs (no debug_log needed).


class TravelPayload(_StrictModel):
    """Travel event from save state diff: character's location_id changed."""

    from_location: int | None = None
    to_location: int | None = None


class TravelEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["travel"] = "travel"
    d: str
    c: int  # the character who travelled
    p: TravelPayload


class TraitPayload(_StrictModel):
    """Trait change payload. trait_name resolved from the save's
    traits_lookup table at parse time; None if the ID was out of range
    (shouldn't happen with a well-formed save but defensively kept
    optional)."""

    trait_id: int
    trait_name: str | None = None


class TraitGainedEvent(_StrictModel):
    """A character gained a trait between adjacent snapshots. Could be
    a personality trait (Bold, Wrathful), lifestyle trait (Schemer),
    education trait (one-time at adulthood), or stress-induced trait
    (Lustful, Faltering Heart). Live-test caught Toirrdelbach gaining
    'faltering_heart' which the user explicitly wanted surfaced."""

    v: int = SCHEMA_VERSION
    t: Literal["trait_gained"] = "trait_gained"
    d: str
    c: int
    p: TraitPayload


class TraitLostEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["trait_lost"] = "trait_lost"
    d: str
    c: int
    p: TraitPayload


class AdventurerStartedEvent(_StrictModel):
    """A character transitioned into Roads to Power's landless-adventurer
    state — they lost (or renounced) all titled holdings and are now
    a wanderer with a single camp province. Detected via
    landed_data.government becoming 'landless_adventurer_government'.
    Narratively a major arc shift (became exile / pilgrim / sellsword)."""

    v: int = SCHEMA_VERSION
    t: Literal["adventurer_started"] = "adventurer_started"
    d: str
    c: int
    p: _EmptyPayload


class AdventurerEndedEvent(_StrictModel):
    """A landless adventurer transitioned back to a regular government
    type (settled, was granted land, conquered a holding). Detected via
    landed_data.government changing FROM 'landless_adventurer_government'
    to something else."""

    v: int = SCHEMA_VERSION
    t: Literal["adventurer_ended"] = "adventurer_ended"
    d: str
    c: int
    p: _EmptyPayload


class GovernmentChangedPayload(_StrictModel):
    """A character's CK3 government type changed (e.g. tribal_government
    → feudal_government). The string IDs are CK3's canonical engine
    values; biographers can localise known IDs and treat unknown ones
    as 'changed regime'.

    Adventurer-mode transitions (into/out of landless_adventurer_government)
    are surfaced via the dedicated AdventurerStarted/Ended events and are
    NOT duplicated here."""

    previous_government: str
    new_government: str


class GovernmentChangedEvent(_StrictModel):
    """A non-adventurer government transition — most often a deliberate
    decision (e.g. adopt_feudal_decision), occasionally an engine-forced
    change. Detected via landed_data.government value changes that don't
    involve landless_adventurer_government on either side."""

    v: int = SCHEMA_VERSION
    t: Literal["government_changed"] = "government_changed"
    d: str
    c: int
    p: GovernmentChangedPayload


class AlliancePayload(_StrictModel):
    """Alliance event from save state diff: a tracked character gained or
    lost an ally between adjacent snapshots. The ally's CK3 ID is the
    only payload field — the ally's name flows through the participants
    tuple so the prompt's names_map glossary picks it up.
    """

    ally_character_id: int


class AllianceFormedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["alliance_formed"] = "alliance_formed"
    d: str
    c: int  # the tracked character whose ally list grew
    p: AlliancePayload


class AllianceBrokenEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["alliance_broken"] = "alliance_broken"
    d: str
    c: int  # the tracked character whose ally list shrank
    p: AlliancePayload


class NicknamePayload(_StrictModel):
    """Nickname change from save state diff: ``character.nickname`` differs
    between adjacent snapshots. Either side may be ``None`` (gain or loss
    of an epithet); both cannot be ``None`` simultaneously since that would
    not be a change."""

    from_nickname: str | None = None
    to_nickname: str | None = None


class NicknameEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["nickname"] = "nickname"
    d: str
    c: int  # the character whose nickname changed
    p: NicknamePayload


class TitleAcquiredPayload(_StrictModel):
    """ck3_chronicler-dr9: a tracked character gained a title between
    snapshots. ``title_key`` is the engine identifier (e.g. ``d_munster``);
    ``title_name`` is the localised name when CK3 stored one
    (``Duchy of Munster``), else ``None``. ``tier`` is derived from the
    key prefix (e_/k_/d_/c_/b_) and reads as the human word.
    ``from_holder_id`` is the previous holder if known (None for newly
    created titles or untracked predecessors)."""

    title_id: int
    title_key: str
    title_name: str | None = None
    tier: str | None = None
    from_holder_id: int | None = None


class TitleAcquiredEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["title_acquired"] = "title_acquired"
    d: str
    c: int  # the character receiving the title
    p: TitleAcquiredPayload


class TitleRelinquishedPayload(_StrictModel):
    """ck3_chronicler-dr9: tracked character no longer holds a title.
    Inverse of :class:`TitleAcquiredPayload`. ``to_holder_id`` is the new
    holder if known (None for titles destroyed entirely or transferred to
    NPCs the snapshot doesn't have)."""

    title_id: int
    title_key: str
    title_name: str | None = None
    tier: str | None = None
    to_holder_id: int | None = None


class TitleRelinquishedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["title_relinquished"] = "title_relinquished"
    d: str
    c: int  # the character who lost the title
    p: TitleRelinquishedPayload


class TitleCreatedPayload(_StrictModel):
    """ck3_chronicler-dr9: a title that didn't exist in prev appears in
    curr with this character as its holder. The decision-driven 'unify
    the petty kingdom of X' / empire-creation cases."""

    title_id: int
    title_key: str
    title_name: str | None = None
    tier: str | None = None


class TitleCreatedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["title_created"] = "title_created"
    d: str
    c: int  # the character who is the founding holder
    p: TitleCreatedPayload


class HouseChangePayload(_StrictModel):
    """ck3_chronicler-667: a character's dynasty_house changed between
    snapshots. Cadet-branch founding is the canonical case (player
    leaves parent house to head a newly-created house). Both name
    fields can be None when the lookup table didn't have an entry —
    the IDs are always present.
    """

    from_house_id: int | None = None
    from_house_name: str | None = None
    to_house_id: int | None = None
    to_house_name: str | None = None


class HouseChangeEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["house_change"] = "house_change"
    d: str
    c: int
    p: HouseChangePayload


class CultureChangePayload(_StrictModel):
    """ck3_chronicler-667: character's culture_id changed (cultural
    conversion / hybrid culture / etc.). Names are the localised culture
    template strings ('akan', 'norse', etc.) — the canonical IDs CK3
    keys cultures by."""

    from_culture_id: int | None = None
    from_culture_name: str | None = None
    to_culture_id: int | None = None
    to_culture_name: str | None = None


class CultureChangeEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["culture_change"] = "culture_change"
    d: str
    c: int
    p: CultureChangePayload


class FaithChangePayload(_StrictModel):
    """ck3_chronicler-667: character's faith_id changed (religious
    conversion). Names are the faith_type strings ('catholic',
    'akom_pagan', etc.)."""

    from_faith_id: int | None = None
    from_faith_name: str | None = None
    to_faith_id: int | None = None
    to_faith_name: str | None = None


class FaithChangeEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["faith_change"] = "faith_change"
    d: str
    c: int
    p: FaithChangePayload


class WarSidePayload(_StrictModel):
    """ck3_chronicler-o7j: shared payload for save-diff war events.

    Distinct from the older v0.4 ``war_started`` / ``war_won_*`` events
    (debug-log path with empty payloads). Save-diff war events carry
    rich CB metadata + which side the tracked character is on so the
    consolidation prompt can write "Erik joined the holy war for
    Aquitaine on the attacker's side" instead of "war_id=67108864".

    ``side`` is None on ``war_concluded`` because the concluded war
    has been deleted from the active_wars dict — we resolve it from
    the previous tick's snapshot, but only the participant set
    survives, not always the side breakdown. Conservative null-out
    keeps the schema honest.
    """

    war_id: int
    war_name: str | None = None
    casus_belli_type: str | None = None
    targeted_titles: tuple[int, ...] = ()
    side: Literal["attacker", "defender"] | None = None
    primary_attacker_id: int | None = None
    primary_defender_id: int | None = None
    claimant_id: int | None = None


class WarDeclaredEvent(_StrictModel):
    """The tracked character is the cb.attacker or cb.defender of a
    newly-appeared war. "Declared" semantically — the war was just
    started and this character is the principal driver, not someone
    pulled in via vassalage or alliance (those get
    :class:`WarJoinedEvent`)."""

    v: int = SCHEMA_VERSION
    t: Literal["war_declared"] = "war_declared"
    d: str
    c: int
    p: WarSidePayload


class WarJoinedEvent(_StrictModel):
    """The tracked character became a participant in a war. Either an
    existing war they were called into, or a freshly-declared war
    where they are not the principal (a called-in vassal or ally)."""

    v: int = SCHEMA_VERSION
    t: Literal["war_joined"] = "war_joined"
    d: str
    c: int
    p: WarSidePayload


class WarLeftEvent(_StrictModel):
    """The tracked character was a participant in a war last tick and
    isn't this tick — but the war is still active. Likely separate
    peace, side switch, or being released from the call to war."""

    v: int = SCHEMA_VERSION
    t: Literal["war_left"] = "war_left"
    d: str
    c: int
    p: WarSidePayload


class WarConcludedEvent(_StrictModel):
    """The war the tracked character was in has ended (war_id absent
    from this tick's active_wars dict entirely). CK3 deletes concluded
    wars from the save rather than retaining a final record, so we
    cannot know the outcome (winner/loser) from the snapshot — we
    only know it ended."""

    v: int = SCHEMA_VERSION
    t: Literal["war_concluded"] = "war_concluded"
    d: str
    c: int
    p: WarSidePayload


class ArtifactPayload(_StrictModel):
    """ck3_chronicler-d83: artifact ownership change.

    ``name`` and ``type`` come from the snapshot's ArtifactSnapshot
    (CK3 stores them on the artifact record). ``rarity`` is the
    engine string ("famed", "illustrious"). The acquired/lost split
    is conveyed by the event class, not the payload."""

    artifact_id: int
    name: str | None = None
    type: str | None = None
    rarity: str | None = None


class ArtifactAcquiredEvent(_StrictModel):
    """The tracked character became the owner of an artifact they
    didn't own in the prior tick. Engine sources include forging,
    inheritance, gift, theft, claim resolution, and quest reward — we
    can't distinguish from snapshot-only data."""

    v: int = SCHEMA_VERSION
    t: Literal["artifact_acquired"] = "artifact_acquired"
    d: str
    c: int
    p: ArtifactPayload


class ArtifactLostEvent(_StrictModel):
    """The tracked character was an artifact's owner last tick and
    isn't this tick. Engine sources include gifting, theft, death-
    transfer, and destruction — same caveat."""

    v: int = SCHEMA_VERSION
    t: Literal["artifact_lost"] = "artifact_lost"
    d: str
    c: int
    p: ArtifactPayload


class DynastyLegacyPayload(_StrictModel):
    """ck3_chronicler-d83: a dynasty has unlocked a new legacy perk
    (engine key like ``"blood_legacy_1"`` or ``"ep1_culture_legacy_3"``).
    Emitted to every tracked character of the dynasty so the
    consolidator surfaces it as a dynasty-level milestone in their
    arc, not buried in a single character's events."""

    dynasty_id: int
    dynasty_name: str | None = None
    legacy_key: str


class DynastyLegacyUnlockedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["dynasty_legacy_unlocked"] = "dynasty_legacy_unlocked"
    d: str
    c: int  # the tracked character whose dynasty advanced
    p: DynastyLegacyPayload


class SplendorPayload(_StrictModel):
    """ck3_chronicler-ei8t: a dynasty crossed a splendor tier upward.
    Tier integers are 0..N — names live in the renderer (sourced from
    CK3's game_concepts_l_english.yml). Mirrors the DynastyLegacy
    pattern; emitted to every tracked character of the dynasty so the
    consolidator surfaces it in their arc, not buried on a single
    member's stream."""

    dynasty_id: int
    dynasty_name: str | None = None
    dynasty_head_id: int | None = None
    old_tier: int
    new_tier: int
    total_renown_at_change: float


class SplendorIncreasedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["splendor_increased"] = "splendor_increased"
    d: str
    c: int  # the tracked character whose dynasty crossed
    p: SplendorPayload


class ConcubinePayload(_StrictModel):
    """ck3_chronicler-gu7j: a tracked character has a new concubine
    on their family_data.concubine list. ``concubine_id`` is the
    new partner's CK3 character id; ``concubine_name`` is the
    diff-time decoded name (optional — populated when the namer can
    resolve it from the current snapshot, ``None`` otherwise so the
    renderer falls back to ``"id N"``)."""

    concubine_id: int
    concubine_name: str | None = None


class ConcubineTakenEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["concubine_taken"] = "concubine_taken"
    d: str
    c: int  # the tracked character who took the concubine
    p: ConcubinePayload


class ModifierPayload(_StrictModel):
    """ck3_chronicler-n0s4: an engine ``character_modifier`` key just
    became active on a tracked character. ``modifier_key`` is the
    engine string (e.g. ``'devoted_to_ullr'``, ``'mourning_son'``,
    event-granted IDs). The renderer is responsible for any cosmetic
    transformation (insert spaces, drop boring prefixes, etc.) — the
    event store keeps the raw key so a future allowlist/blocklist
    pass can be applied at the briefing layer without re-ingesting."""

    modifier_key: str


class ModifierAcquiredEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["modifier_acquired"] = "modifier_acquired"
    d: str
    c: int  # the tracked character who acquired the modifier
    p: ModifierPayload


class PerkPayload(_StrictModel):
    """ck3_chronicler-rgay: a lifestyle perk just appeared on the
    character's ``alive_data.perk`` list. ``perk_key`` is the engine
    string (e.g. ``'bellum_justum_perk'``, ``'parthian_tactics_perk'``,
    ``'schemer_perk'``). The renderer is responsible for any cosmetic
    transformation — same convention as modifier_acquired."""

    perk_key: str


class PerkAcquiredEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["perk_acquired"] = "perk_acquired"
    d: str
    c: int  # the tracked character who acquired the perk
    p: PerkPayload


class LifestyleCommittedPayload(_StrictModel):
    """ck3_chronicler-9p2g: a character just picked up their first perk
    in a previously-empty lifestyle. ``lifestyle_key`` is the engine
    key matching :data:`chronicler.save.lifestyle_perks.KNOWN_LIFESTYLES`
    (e.g. ``'martial_lifestyle'``, ``'wanderer_lifestyle'``).
    ``first_perk_key`` is the lex-smallest known perk in that lifestyle
    that appeared in the diff window — deterministic for replay."""

    lifestyle_key: str
    first_perk_key: str


class LifestyleCommittedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["lifestyle_committed"] = "lifestyle_committed"
    d: str
    c: int  # the tracked character who committed to the lifestyle
    p: LifestyleCommittedPayload


class ContractPayload(_StrictModel):
    """ck3_chronicler-621o (8aie slice 6): one RtP adventurer
    contract just transitioned into a terminal state. ``contract_type``
    is the engine key (e.g. ``'laamp_base_6021'`` — RtP's ``laamp``
    prefix is the landless-adventurer-mp codename). ``name`` is the
    display string ("Perform in a Play", "Hobnob with Ruler"). ``tier``
    is the difficulty/reward tier. ``employer_id`` is the issuer; the
    event's ``c`` field holds the adventurer (owner). ``outcome``
    is mapped from the final status: ``'completed'`` for success,
    ``'invalidated'`` for failure / cancellation."""

    contract_type: str | None
    name: str | None
    tier: int | None
    employer_id: int | None
    location_province_id: int | None
    outcome: Literal["completed", "invalidated"]
    acceptance_date: str | None
    completion_date: str | None


class ContractCompletedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["contract_completed"] = "contract_completed"
    d: str
    c: int  # the adventurer who held the contract (owner)
    p: ContractPayload


class CampCompanionPayload(_StrictModel):
    """ck3_chronicler-mke9 (8aie slice 7): one occupied court position
    in the tracked character's camp/court (joined OR left between
    snapshots).

    ``court_position`` is the engine key
    (``travel_leader_court_position``, ``bodyguard_court_position``,
    ``second_camp_officer``, ``stooge_camp_officer``, …). RtP
    camp-officer roles drop the ``_court_position`` suffix —
    renderers must tolerate both shapes.

    ``employee_id`` is the new (or departing) character. ``employee_name``
    is resolved at diff time from the curr snapshot's characters dict
    (None when the lookup misses — the renderer falls back to the
    names map then ``"id N"``). ``hire_date`` is informational; missing
    on the left-event payload when CK3 didn't record it.
    """

    position_id: int
    court_position: str | None
    employee_id: int | None
    employee_name: str | None
    hire_date: str | None


class CampCompanionJoinedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["camp_companion_joined"] = "camp_companion_joined"
    d: str
    c: int  # the camp owner (employer)
    p: CampCompanionPayload


class CampCompanionLeftEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["camp_companion_left"] = "camp_companion_left"
    d: str
    c: int  # the camp owner (employer)
    p: CampCompanionPayload


class DomicilePayload(_StrictModel):
    """ck3_chronicler-r343 (8aie slice 5): a tracked character's
    domicile (RtP camp, herd, or stationary estate) just changed
    province between snapshots.

    ``from_province_id`` and ``to_province_id`` carry the IDs as
    stored in CK3's province table; renderers may resolve them
    through their own province-name lookup (deferred — adventurers
    move through hundreds of provinces, name-resolution belongs
    in a separate pass). ``domicile_type`` lets the briefing layer
    distinguish a mobile camp move from a stationary-estate
    relocation (the second is more narratively meaningful)."""

    domicile_id: int
    domicile_type: str | None
    from_province_id: int | None
    to_province_id: int | None


class DomicileMovedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["domicile_moved"] = "domicile_moved"
    d: str
    c: int  # the domicile-holding character
    p: DomicilePayload


class VanillaMemoryPayload(_StrictModel):
    """Wraps one record from CK3's character_memory_manager database.

    The engine's curated narrative log (memory_grand_wedding,
    memory_won_battle, memory_pilgrimage_completed, memory_imprisoned,
    memory_assassination_*, ascended_throne_memory, relative_died, ...)
    Each record has a memory_type (string), an optional end_date, and a
    participants dict mapping role names (string) to character IDs (int).
    """

    memory_type: str
    end_date: str | None = None
    # role -> character_id; not strictly typed because CK3 uses many role
    # names across memory types
    participants: dict[str, int] = Field(default_factory=dict)
    # ck3_chronicler-7jwu: when memory_type == "relative_died" and
    # participants["dead_relation"] resolves to a character in the
    # snapshot, the diff layer threads the deceased's cause-of-death,
    # age-at-death, and first name into the payload so biographies for
    # the surviving relative can weave the death into prose ("a fever
    # passed through the household; three of his kin were carried off,
    # the chronicles naming the affliction typhus"). All three are None
    # for memory_types other than relative_died, or when the deceased's
    # row has been pruned from the snapshot.
    deceased_cause: str | None = None
    deceased_age: int | None = None
    deceased_first_name: str | None = None
    # ck3_chronicler-dn7a (sister to 7jwu): when the deceased was
    # murdered / executed / killed-in-battle, CK3 records dead_data.
    # killer as a character id. The diff layer threads it through here
    # so biographies for the surviving relative can name the killer
    # rather than write around the murder ("his brother was killed by
    # Eirik the Red, after a feud over the northern marches" instead of
    # "his brother died"). Both fields are None when the deceased
    # had no killer recorded (the common case — natural-cause deaths)
    # or when the killer's row has been pruned from the snapshot.
    deceased_killer_id: int | None = None
    deceased_killer_first_name: str | None = None


class VanillaMemoryEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["vanilla_memory"] = "vanilla_memory"
    d: str
    c: int  # the character who owns this memory
    p: VanillaMemoryPayload


class EpidemicPayload(_StrictModel):
    """ck3_chronicler-9wrd: tracked character was newly infected by a
    plague / disease.

    ``epidemic_type`` is the engine string ('measles', 'consumption',
    'smallpox', etc.); ``intensity`` is one of CK3's enum values
    ('major', 'minor', 'devastating'). ``name`` is the in-game display
    name (e.g. 'Pope Alexander's Boils', 'Yamato Boils') — biographies
    surface this verbatim to anchor the period. The numerical
    breakdown (``num_character_deaths``, ``num_infected_provinces``)
    is captured at outbreak-detection time so prompts can convey scale
    even after the epidemic concludes and CK3 prunes the record."""

    epidemic_id: int
    epidemic_type: str | None = None
    name: str | None = None
    intensity: str | None = None
    start_date: str | None = None
    num_infected_provinces: int = 0
    num_character_deaths: int = 0


class EpidemicOutbreakEvent(_StrictModel):
    """A tracked character is newly infected by an active epidemic.
    Fires once per (character, epidemic) pair the first time the diff
    sees the character in the epidemic's ``characters`` list."""

    v: int = SCHEMA_VERSION
    t: Literal["epidemic_outbreak"] = "epidemic_outbreak"
    d: str
    c: int  # the tracked character who became infected
    p: EpidemicPayload


class ActivityPayload(_StrictModel):
    """ck3_chronicler-qx7n (8aie slice 1): a tracked character's
    participation in a CK3 activity (tournament, hunt, pilgrimage,
    feast, hold-court, etc.) just ended — the activity_id disappeared
    from activity_manager.database. Indistinguishable from save state
    whether the activity completed successfully or was cancelled
    (host died, attendee was waylaid en route); the LLM phrases
    conservatively.

    ``activity_type`` is the engine string (``activity_pilgrimage``,
    ``activity_feast``, ``activity_hunt``, ``activity_adult_education``,
    ...). ``role`` distinguishes "the character hosted this" from "the
    character attended this" — narratively the same beat with different
    verbs, mirrors how WarSidePayload's ``side`` field carries the
    attacker/defender role instead of splitting the event into two
    sibling types.

    ``host_id`` is the character who scheduled the activity; populated
    only when ``role == "attendee"`` so renderers can produce "attended
    X hosted by Y" prose. ``start_province_id`` is the province from
    the first phase entry — narratively "where it started" (multi-phase
    activities like long pilgrimages walk multiple provinces; first is
    the anchor). ``start_date`` is the activity's ``active_start_date``
    (when CK3 transitioned it from scheduled to running).
    """

    activity_id: int
    activity_type: str | None = None
    role: Literal["host", "attendee"]
    host_id: int | None = None
    start_province_id: int | None = None
    start_date: str | None = None


class ActivityCompletedEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["activity_completed"] = "activity_completed"
    d: str
    c: int  # the tracked character whose participation just ended
    p: ActivityPayload


class InspirationSponsoredPayload(_StrictModel):
    """ck3_chronicler-m658 (8aie slice 2): a tracked character just
    began sponsoring an NPC artisan's inspiration. The signal: a
    new inspiration_id appeared in this character's
    ``landed_data.sponsored_inspirations`` list between adjacent
    snapshots, while the matching
    ``inspirations_manager.database[id].sponsored`` date transitioned
    from CK3's '1.1.1' sentinel to a real date.

    ``inspiration_type`` is the engine string (``weapon_inspiration``,
    ``book_inspiration``, ``adventure_inspiration``, ``smith_inspiration``,
    ``weaver_inspiration``, ``armor_inspiration``, ``artisan_inspiration``,
    ``bow_inspiration``, ...). ``artisan_character_id`` is the NPC who
    has the inspiration on their ``alive_data.inspiration`` — resolved
    via reverse-index at parse-time. ``total_cost`` is the gold goal
    the inspiration progresses toward.

    The realization beat (artisan finishes, artifact spawns) is already
    captured by ck3_chronicler-d83's artifact_acquired; we don't fire a
    separate inspiration_realized event."""

    inspiration_id: int
    inspiration_type: str | None = None
    artisan_character_id: int | None = None
    total_cost: int | None = None


class InspirationSponsoredEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["inspiration_sponsored"] = "inspiration_sponsored"
    d: str
    c: int  # the sponsor — tracked character who funded the inspiration
    p: InspirationSponsoredPayload


class MiscarriagePayload(_StrictModel):
    """ck3_chronicler-4pj: a tracked character lost the ``pregnant`` trait
    between snapshots without a corresponding ``child_born`` vanilla
    memory landing in the same diff window — i.e. the pregnancy ended
    in miscarriage rather than a successful birth.

    ``assumed_father_id`` is the character's primary_spouse at the time
    the pregnancy ended (the same heuristic CK3 itself uses for
    paternity assumption when the user hasn't toggled the witcher
    interaction). None when the character had no primary spouse on
    record (e.g. unwed mistresses)."""

    assumed_father_id: int | None = None


class MiscarriageEvent(_StrictModel):
    v: int = SCHEMA_VERSION
    t: Literal["miscarriage"] = "miscarriage"
    d: str
    c: int  # the tracked character who was pregnant
    p: MiscarriagePayload


class DecisionTakenPayload(_StrictModel):
    """ck3_chronicler-jrwe: a tracked character newly took a CK3
    decision that's narratively interesting to the chronicler.

    Detected via ``landed_data.decision_cooldowns`` on the character's
    save record — when a decision_id appears in the dict that wasn't
    there last tick, the diff layer emits this event. ``decision_id``
    is the engine string (e.g. ``raise_stele_decision``).
    ``cooldown_end_date`` is the date CK3 will allow the character to
    take the decision again — the in-game equivalent of "this happened
    recently".

    The actual date the decision was *taken* is roughly
    cooldown_end_date - cooldown_duration; for ``raise_stele_decision``
    the cooldown is 10 years, so a ``cooldown_end_date`` of 1095.4.1
    means the decision was taken around 1085.4.1. Biographies should
    interpret the field as "decision was taken; now on cooldown until
    this date."
    """

    decision_id: str
    cooldown_end_date: str | None = None


class DecisionTakenEvent(_StrictModel):
    """ck3_chronicler-jrwe: chronicler-synthesised event for narratively
    significant CK3 decisions. CK3 doesn't emit a vanilla memory for
    most decisions (the Norse runestone-raise being the live signal —
    confirmed via fp1 game files: zero ``add_character_memory`` calls
    in the decision's effect chain), so the chronicler synthesises one
    from the ``decision_cooldowns`` transition.

    Filtered through a curated allowlist
    (:data:`chronicler.save.diff._NARRATIVELY_INTERESTING_DECISIONS`)
    so administrative decisions (hold court, train for tournament,
    extract gold from treasury) don't drown out the prose-relevant
    beats. Extend the allowlist when a real save shows a new decision
    that biographies should weave."""

    v: int = SCHEMA_VERSION
    t: Literal["decision_taken"] = "decision_taken"
    d: str
    c: int  # the character who took the decision
    p: DecisionTakenPayload


class BuildingCompletedPayload(_StrictModel):
    """ck3_chronicler-2ur: a tracked character's holding finished
    construction of a building between snapshots.

    Detected by walking ``provinces[pid].holding.constructions`` per
    snapshot, keyed by (province_id, slot_index). When an in-flight
    construction the tracked character initiated disappears from the
    in-flight set AND the slot in ``buildings[]`` now carries the same
    ``building`` type, the construction shipped (vs being cancelled —
    cancellations are a v2 follow-up).

    Fields are biographically load-bearing: ``building`` is the engine
    type string (``longhouses_01``, ``hospices_01``, ``tribe_02``);
    ``province_id`` lets a future enrichment pass resolve the holding /
    barony / county name; ``slot_index`` distinguishes upgrades on the
    same holding. ``start_date`` is when construction began so prompts
    can frame the duration (``"three years' labour at Tartu"``)."""

    building: str
    province_id: int
    slot_index: int
    start_date: str | None = None


class BuildingCompletedEvent(_StrictModel):
    """A construction the tracked character initiated finished. Fires
    once per (character, province_id, slot_index, building) tuple — the
    first save where the in-flight record is gone and the slot's
    ``buildings[]`` entry matches the construction's ``building`` type."""

    v: int = SCHEMA_VERSION
    t: Literal["building_completed"] = "building_completed"
    d: str
    c: int  # the constructor character (holding owner who initiated the build)
    p: BuildingCompletedPayload


EventPayload = Annotated[
    DeathEvent
    | BirthEvent
    | MarriageEvent
    | DivorceEvent
    | TitleGainEvent
    | TitleLostEvent
    | WarStartedEvent
    | WarWonAttackerEvent
    | WarWonDefenderEvent
    | ImprisonEvent
    | ReleaseEvent
    | TravelEvent
    | NicknameEvent
    | AllianceFormedEvent
    | AllianceBrokenEvent
    | AdventurerStartedEvent
    | AdventurerEndedEvent
    | GovernmentChangedEvent
    | TraitGainedEvent
    | TraitLostEvent
    | TitleAcquiredEvent
    | TitleRelinquishedEvent
    | TitleCreatedEvent
    | HouseChangeEvent
    | CultureChangeEvent
    | FaithChangeEvent
    | WarDeclaredEvent
    | WarJoinedEvent
    | WarLeftEvent
    | WarConcludedEvent
    | ArtifactAcquiredEvent
    | ArtifactLostEvent
    | DynastyLegacyUnlockedEvent
    | SplendorIncreasedEvent
    | ConcubineTakenEvent
    | ModifierAcquiredEvent
    | PerkAcquiredEvent
    | LifestyleCommittedEvent
    | ContractCompletedEvent
    | CampCompanionJoinedEvent
    | CampCompanionLeftEvent
    | DomicileMovedEvent
    | VanillaMemoryEvent
    | EpidemicOutbreakEvent
    | MiscarriageEvent
    | DecisionTakenEvent
    | BuildingCompletedEvent
    | ActivityCompletedEvent
    | InspirationSponsoredEvent,
    Field(discriminator="t"),
]
EventAdapter: TypeAdapter[EventPayload] = TypeAdapter(EventPayload)
