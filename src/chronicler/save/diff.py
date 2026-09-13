"""Emit events from snapshot deltas — the core of the v0.6 architecture.

The save-parse layer (V06-P01) produces :class:`SaveSnapshot` instances.
This module compares two adjacent snapshots and emits events the user
cares about: deaths, marriages, travel, vanilla memory accumulations.

For monthly autosaves, the snapshot pair represents one in-game month
of changes. Sub-month chronology is intentionally collapsed (per the
v0.6 architectural-pivot trade-off documented in ``v0-6-architectural-
pivot`` memory). Each emitted event is dated to the *current*
snapshot's date — the tightest bound we have on when the event
happened without finer-grained information.

Every event constructed here is a variant of the
:data:`chronicler.schema.events.EventPayload` discriminated union — the
same models the debug.log transport produces — so downstream consumers
(biographies, repository inserts) work uniformly across debug_log-sourced
and save-diff-sourced events. That union is the authoritative,
non-drifting catalogue of what can be emitted; this module constructs
most of its variants (deaths, marriages, divorces, travel, vanilla
memories, titles acquired/relinquished/created, alliances, traits,
house/culture/faith changes, wars declared/joined/left/concluded,
artifacts, dynasty legacy / splendor, concubines, modifiers, perks,
lifestyles, epidemics, miscarriages, decisions, buildings, and the RtP
camp/contract/domicile/activity/inspiration beats). Each ``_make_*`` /
``_diff_*`` helper below owns one beat and is the source of truth for
that event's trigger condition.

What remains explicitly skipped:
- Birth events for proc-gen NPCs (the population is huge; rely on
  vanilla memories' relative_died / relative_born for player-relevant
  births).
- Imprison / release (no clean state field on the prisoner; vanilla
  memory_imprisoned covers important cases).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from chronicler.save.localization import strip_loca_markup
from chronicler.save.parse import (
    CharacterSnapshot,
    ConstructionSnapshot,
    ContractSnapshot,
    DomicileSnapshot,
    MemorySnapshot,
    SaveSnapshot,
    WarSnapshot,
)
from chronicler.schema import (
    ActivityCompletedEvent,
    ActivityPayload,
    AdventurerEndedEvent,
    AdventurerStartedEvent,
    AllianceBrokenEvent,
    AllianceFormedEvent,
    AlliancePayload,
    ArtifactAcquiredEvent,
    ArtifactLostEvent,
    ArtifactPayload,
    BuildingCompletedEvent,
    BuildingCompletedPayload,
    CampCompanionJoinedEvent,
    CampCompanionLeftEvent,
    CampCompanionPayload,
    ConcubinePayload,
    ConcubineTakenEvent,
    ContractCompletedEvent,
    ContractPayload,
    CultureChangeEvent,
    CultureChangePayload,
    DeathEvent,
    DeathPayload,
    DecisionTakenEvent,
    DecisionTakenPayload,
    DivorceEvent,
    DivorcePayload,
    DomicileMovedEvent,
    DomicilePayload,
    DynastyLegacyPayload,
    DynastyLegacyUnlockedEvent,
    EpidemicOutbreakEvent,
    EpidemicPayload,
    EventPayload,
    FaithChangeEvent,
    FaithChangePayload,
    GovernmentChangedEvent,
    GovernmentChangedPayload,
    HouseChangeEvent,
    HouseChangePayload,
    InspirationSponsoredEvent,
    InspirationSponsoredPayload,
    LifestyleCommittedEvent,
    LifestyleCommittedPayload,
    MarriageEvent,
    MarriagePayload,
    MiscarriageEvent,
    MiscarriagePayload,
    ModifierAcquiredEvent,
    ModifierPayload,
    NicknameEvent,
    NicknamePayload,
    PerkAcquiredEvent,
    PerkPayload,
    SplendorIncreasedEvent,
    SplendorPayload,
    TitleAcquiredEvent,
    TitleAcquiredPayload,
    TitleCreatedEvent,
    TitleCreatedPayload,
    TitleRelinquishedEvent,
    TitleRelinquishedPayload,
    TraitGainedEvent,
    TraitLostEvent,
    TraitPayload,
    TravelEvent,
    TravelPayload,
    VanillaMemoryEvent,
    VanillaMemoryPayload,
    WarConcludedEvent,
    WarDeclaredEvent,
    WarJoinedEvent,
    WarLeftEvent,
    WarSidePayload,
)
from chronicler.schema.events import _EmptyPayload
from chronicler.util.dates import parse_ck3_date

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiffEvent:
    """One event emitted by :func:`diff_snapshots`.

    Wraps the Pydantic event payload with the participant tuple
    (compatible with the existing
    :class:`chronicler.tailer.parser.ParsedEvent` shape so the
    repository layer ingests both transports identically).
    """

    event: EventPayload
    # role -> character_id pairs from named scopes (similar to the
    # debug_log scope-dump participants but derived from the save state)
    participants: tuple[tuple[str, int], ...]


# ck3_chronicler-27ov.11: declarative set-diff registry. Most snapshot-
# level streams share one skeleton — dead-guard, set-diff over a
# character_to_X reverse index, sorted added/removed loops, None-record
# skip. A SetDiffSpec declares the index accessor and per-transition
# event builders; make_set_differ runs the skeleton once. Bespoke streams
# (wars/contracts/constructions/domicile/dynasty_*) keep custom bodies and
# join the registry via thin uniform-signature wrappers in STREAM_DIFFERS.

# (cid, char, prev_snapshot, curr_snapshot, date) -> events
StreamDiffer = Callable[[int, CharacterSnapshot, SaveSnapshot, SaveSnapshot, str], list[DiffEvent]]

# (cid, element_id, prev_snapshot, curr_snapshot, date) -> one event or None.
# Returning None skips this element (e.g. a missing record, or a transition
# this stream doesn't emit on). Receives BOTH snapshots so the builder reads
# metadata from whichever side carries it (curr for acquisitions, prev for
# completions where CK3 pruned the record).
SetDiffBuilder = Callable[[int, int, SaveSnapshot, SaveSnapshot, str], DiffEvent | None]


@dataclass(frozen=True)
class SetDiffSpec:
    """One per pure character_to_X set-diff stream. ``on_added`` /
    ``on_removed`` are None when the stream emits nothing on that
    transition (e.g. epidemics: outbreak only; activities: completion
    only)."""

    name: str
    index: Callable[[SaveSnapshot], dict[int, frozenset[int]]]
    on_added: SetDiffBuilder | None = None
    on_removed: SetDiffBuilder | None = None


def make_set_differ(spec: SetDiffSpec) -> StreamDiffer:
    """Build a StreamDiffer that runs the shared set-diff skeleton for
    ``spec``. Behavior matches the hand-written _diff_* helpers exactly:
    dead characters short-circuit, an unchanged index short-circuits,
    added/removed ids are processed in sorted() order, and a builder that
    returns None contributes no event."""

    def differ(
        cid: int,
        char: CharacterSnapshot,
        prev: SaveSnapshot,
        curr: SaveSnapshot,
        date: str,
    ) -> list[DiffEvent]:
        if char.is_dead:
            return []
        prev_set = spec.index(prev).get(cid, frozenset())
        curr_set = spec.index(curr).get(cid, frozenset())
        if prev_set == curr_set:
            return []
        events: list[DiffEvent] = []
        if spec.on_added is not None:
            for eid in sorted(curr_set - prev_set):
                ev = spec.on_added(cid, eid, prev, curr, date)
                if ev is not None:
                    events.append(ev)
        if spec.on_removed is not None:
            for eid in sorted(prev_set - curr_set):
                ev = spec.on_removed(cid, eid, prev, curr, date)
                if ev is not None:
                    events.append(ev)
        return events

    return differ


def _alliance_added(cid, ally_id, _prev, _curr, date):
    return DiffEvent(
        event=AllianceFormedEvent(d=date, c=cid, p=AlliancePayload(ally_character_id=ally_id)),
        participants=(("ally", ally_id),),
    )


def _alliance_removed(cid, ally_id, _prev, _curr, date):
    return DiffEvent(
        event=AllianceBrokenEvent(d=date, c=cid, p=AlliancePayload(ally_character_id=ally_id)),
        participants=(("former_ally", ally_id),),
    )


_ALLIANCES_SPEC = SetDiffSpec(
    name="alliances",
    index=lambda s: s.alliances,
    on_added=_alliance_added,
    on_removed=_alliance_removed,
)


def _artifact_added(cid, art_id, _prev, curr, date):
    art = curr.artifacts.get(art_id)
    if art is None:
        return None
    return DiffEvent(
        event=ArtifactAcquiredEvent(
            d=date,
            c=cid,
            p=ArtifactPayload(artifact_id=art_id, name=art.name, type=art.type, rarity=art.rarity),
        ),
        participants=(),
    )


def _artifact_removed(cid, art_id, prev, curr, date):
    art = prev.artifacts.get(art_id) or curr.artifacts.get(art_id)
    if art is None:
        return None
    return DiffEvent(
        event=ArtifactLostEvent(
            d=date,
            c=cid,
            p=ArtifactPayload(artifact_id=art_id, name=art.name, type=art.type, rarity=art.rarity),
        ),
        participants=(),
    )


_ARTIFACTS_SPEC = SetDiffSpec(
    name="artifacts",
    index=lambda s: s.character_to_artifacts,
    on_added=_artifact_added,
    on_removed=_artifact_removed,
)


def _epidemic_added(cid, ep_id, _prev, curr, date):
    ep = curr.epidemics.get(ep_id)
    if ep is None:
        return None
    return DiffEvent(
        event=EpidemicOutbreakEvent(
            d=date,
            c=cid,
            p=EpidemicPayload(
                epidemic_id=ep.epidemic_id,
                epidemic_type=ep.epidemic_type,
                name=ep.name,
                intensity=ep.intensity,
                start_date=ep.creation_date,
                num_infected_provinces=ep.num_infected_provinces,
                num_character_deaths=ep.num_character_deaths,
            ),
        ),
        participants=(),
    )


_EPIDEMICS_SPEC = SetDiffSpec(
    name="epidemics",
    index=lambda s: s.character_to_epidemics,
    on_added=_epidemic_added,
    on_removed=None,
)


def _activity_removed(cid, activity_id, prev, _curr, date):
    snap = prev.activities.get(activity_id)
    if snap is None:
        return None
    role: str = "host" if snap.host_id == cid else "attendee"
    return DiffEvent(
        event=ActivityCompletedEvent(
            d=date,
            c=cid,
            p=ActivityPayload(
                activity_id=snap.activity_id,
                activity_type=snap.activity_type,
                role=role,  # type: ignore[arg-type]
                host_id=snap.host_id,
                start_province_id=snap.start_province_id,
                start_date=snap.active_start_date,
            ),
        ),
        participants=(),
    )


_ACTIVITIES_SPEC = SetDiffSpec(
    name="activities",
    index=lambda s: s.character_to_activities,
    on_added=None,
    on_removed=_activity_removed,
)


def _inspiration_added(cid, inspiration_id, _prev, curr, date):
    snap = curr.inspirations.get(inspiration_id)
    if snap is None:
        return None
    return DiffEvent(
        event=InspirationSponsoredEvent(
            d=date,
            c=cid,
            p=InspirationSponsoredPayload(
                inspiration_id=snap.inspiration_id,
                inspiration_type=snap.inspiration_type,
                artisan_character_id=snap.artisan_character_id,
                total_cost=snap.total_cost,
            ),
        ),
        participants=(),
    )


_INSPIRATIONS_SPEC = SetDiffSpec(
    name="inspirations",
    index=lambda s: s.character_to_sponsored_inspirations,
    on_added=_inspiration_added,
    on_removed=None,
)


def _court_position_added(cid, pid, _prev, curr, date):
    snap = curr.court_positions.get(pid)
    if snap is None:
        return None
    employee_name = _resolve_employee_name(snap.employee_id, curr.characters)
    return DiffEvent(
        event=CampCompanionJoinedEvent(
            d=date,
            c=cid,
            p=CampCompanionPayload(
                position_id=snap.position_id,
                court_position=snap.court_position,
                employee_id=snap.employee_id,
                employee_name=employee_name,
                hire_date=snap.hire_date,
            ),
        ),
        participants=(),
    )


def _court_position_removed(cid, pid, prev, curr, date):
    snap = prev.court_positions.get(pid)
    if snap is None:
        return None
    # Departing employee may still be alive in curr.characters — same
    # lookup as the original (curr_characters for the left branch too).
    employee_name = _resolve_employee_name(snap.employee_id, curr.characters)
    return DiffEvent(
        event=CampCompanionLeftEvent(
            d=date,
            c=cid,
            p=CampCompanionPayload(
                position_id=snap.position_id,
                court_position=snap.court_position,
                employee_id=snap.employee_id,
                employee_name=employee_name,
                hire_date=snap.hire_date,
            ),
        ),
        participants=(),
    )


_COURT_POSITIONS_SPEC = SetDiffSpec(
    name="court_positions",
    index=lambda s: s.character_to_court_positions,
    on_added=_court_position_added,
    on_removed=_court_position_removed,
)


# ck3_chronicler-27ov.11: the snapshot-level diff streams, in dispatch
# order. Six are generic set-diffs (Tasks 3-8 swap these wrappers for
# make_set_differ specs); six are bespoke and keep their hand-written
# bodies, adapted here to the uniform StreamDiffer signature. Output is
# order-independent (diff_snapshots sorts by (c, t) at the end), but the
# original order is preserved for review clarity.
STREAM_DIFFERS: list[StreamDiffer] = [
    make_set_differ(_ALLIANCES_SPEC),
    lambda cid, c, prev, curr, date: _diff_wars(
        cid,
        c,
        prev.wars,
        curr.wars,
        prev.character_to_wars,
        curr.character_to_wars,
        date,
    ),
    make_set_differ(_ARTIFACTS_SPEC),
    lambda cid, c, prev, curr, date: _diff_dynasty_legacies(
        cid,
        c,
        prev.dynasty_perks,
        curr.dynasty_perks,
        house_to_dynasty=curr.house_to_dynasty,
        dynasties_lookup=curr.dynasties_lookup,
        date=date,
    ),
    lambda cid, c, prev, curr, date: _diff_dynasty_splendor(
        cid,
        c,
        prev.dynasty_renown,
        curr.dynasty_renown,
        house_to_dynasty=curr.house_to_dynasty,
        dynasties_lookup=curr.dynasties_lookup,
        dynasty_heads=curr.dynasty_heads,
        date=date,
    ),
    make_set_differ(_EPIDEMICS_SPEC),
    make_set_differ(_ACTIVITIES_SPEC),
    make_set_differ(_INSPIRATIONS_SPEC),
    lambda cid, c, prev, curr, date: _diff_contracts(
        cid,
        c,
        prev.task_contracts,
        curr.task_contracts,
        prev.character_to_contracts,
        curr.character_to_contracts,
        date,
    ),
    make_set_differ(_COURT_POSITIONS_SPEC),
    lambda cid, c, prev, curr, date: _diff_domicile(
        cid,
        c,
        prev.domiciles,
        curr.domiciles,
        prev.character_to_domicile,
        curr.character_to_domicile,
        date,
    ),
    lambda cid, c, prev, curr, date: _diff_constructions(
        cid,
        c,
        prev.in_flight_constructions,
        prev.character_to_constructions,
        curr.character_to_constructions,
        curr.holding_buildings_by_slot,
        date,
    ),
]


def diff_snapshots(
    prev: SaveSnapshot,
    curr: SaveSnapshot,
    *,
    tracked_filter: set[int] | None = None,
) -> list[DiffEvent]:
    """Compare two snapshots; return events that occurred between them.

    Both snapshots must come from the same campaign (same
    ``playthrough_id``). The caller is responsible for pairing
    chronologically-adjacent saves; this function does not validate
    ordering or contiguity.

    ``tracked_filter`` is a set of character IDs the caller cares
    about. When provided, events whose primary character is not in the
    set are dropped at source — load-bearing for save-tail against
    bookmarks with tens of thousands of characters where the per-month
    diff produces ~2000 travel/title events (most of which are NPCs the
    user will never look at). Pass ``None`` (the default) to retain
    every event — that's right for ``import-save``-style explicit
    backfills where the user is opting into full historical context.

    The filter applies to the primary character ID only. Tracked
    characters' family relationships are still recorded as event
    participants regardless of whether each family member is itself
    tracked.
    """
    if prev.playthrough_id and curr.playthrough_id and prev.playthrough_id != curr.playthrough_id:
        raise ValueError(
            f"playthrough_id mismatch: {prev.playthrough_id!r} vs {curr.playthrough_id!r}"
        )

    out: list[DiffEvent] = []
    date = curr.current_date

    # ck3_chronicler-27ov.38 (audit M-D3): resolve the pregnant trait id
    # once per diff — _diff_miscarriage used to linear-scan the
    # ~600-entry traits_lookup per character pair, ≈24M string compares
    # over a 40k-character full diff during an import backlog drain.
    pregnant_trait_id = _trait_id_by_name(_PREGNANT_TRAIT_NAME, curr.traits_lookup)

    # Iterate over the union of character IDs in both snapshots — but
    # short-circuit to the tracked set when one is provided. With ~40k
    # characters in a populous bookmark, this turns the per-pair work
    # from O(world) into O(tracked) which is what makes save-tail
    # tractable on large worlds.
    if tracked_filter is None:
        all_ids: set[int] | frozenset[int] = set(prev.characters) | set(curr.characters)
    else:
        all_ids = tracked_filter
    for cid in all_ids:
        p = prev.characters.get(cid)
        c = curr.characters.get(cid)
        if c is None:
            # Character disappeared from both living + dead_unprunable.
            # CK3 prunes minor dead NPCs to manage save size — not an
            # event, just GC.
            continue
        if p is None:
            # ck3_chronicler-dwg0: Character is in curr but not prev.
            # When the character is on the tracked filter (the only ones
            # we iterate when tracked_filter is set), this is the first
            # observation — likely _auto_track_new_candidates added them
            # mid-campaign and they're missing from the in-memory
            # baseline. We skip the per-character diff (no prev to
            # compare against) but log it so silent first-tick event
            # loss has a search-engine hit.
            log.info(
                "diff_snapshots: character %d in curr but not prev — first "
                "observation; events on this tick are skipped (tracked=%s)",
                cid,
                tracked_filter is not None,
            )
            continue

        # ck3_chronicler-27ov.38: the newly-visible memory window is
        # needed by both the vanilla-memory emission and miscarriage
        # detection — compute it once per pair instead of twice.
        prev_mem_ids = {m.memory_id for m in p.memories}
        new_memories = [m for m in c.memories if m.memory_id not in prev_mem_ids]

        out.extend(
            _diff_one_character(p, c, date, characters=curr.characters, new_memories=new_memories)
        )
        # ck3_chronicler-dr9: title-held deltas. Uses the top-level
        # snapshot.titles indexed by holder rather than per-character
        # state — titles are first-class records in CK3 and ownership
        # changes appear as holder field flips on the title row.
        # ck3_chronicler-hk9i: pass the precomputed holder->titles reverse
        # index so the per-character lookup is O(1) instead of O(titles).
        out.extend(_diff_titles(cid, prev, curr, date))
        # ck3_chronicler-27ov.11: snapshot-level streams via the registry.
        for differ in STREAM_DIFFERS:
            out.extend(differ(cid, c, prev, curr, date))
        # ck3_chronicler-nzr: trait diffs need access to the traits_lookup
        # (top-level on SaveSnapshot, not on CharacterSnapshot) for name
        # resolution at event-emit time.
        out.extend(_diff_traits(p, c, curr.traits_lookup, date))
        # ck3_chronicler-gu7j: concubine taken — diff family.concubines
        # set between snapshots. Partner name resolved via curr.characters
        # so the renderer can read it from the payload without a second
        # lookup. Taken-only by design (release covered by death event).
        out.extend(_diff_concubines(p, c, curr.characters, date))
        # ck3_chronicler-n0s4: character_modifier acquisitions —
        # set-diff over CharacterSnapshot.modifiers. Captures every
        # transition; the renderer + briefing layer apply the
        # narrative-value filter at display time.
        out.extend(_diff_modifiers(p, c, date))
        # ck3_chronicler-rgay: lifestyle perk acquisitions — same
        # set-diff treatment as modifiers. Acquired-only; perk
        # losses happen via tree-reset which is structurally
        # different and not narratively meaningful per-perk.
        out.extend(_diff_perks(p, c, date))
        # ck3_chronicler-9p2g: a perk_acquired in a previously-unrepresented
        # lifestyle (one of {martial, diplomacy, stewardship, intrigue,
        # learning, wanderer}) fires a single LifestyleCommittedEvent for
        # that lifestyle. Replaces ej1a (focus tracking impossible on
        # modern CK3 saves) — the perk-list signal is the cleanest
        # commitment beat we can derive.
        out.extend(_diff_lifestyle_commits(p, c, date))
        # ck3_chronicler-4pj: miscarriage detection — the 'pregnant'
        # trait was lost without a paired child_born vanilla memory in
        # the same diff window. Trait id resolved once above (27ov.38).
        out.extend(_diff_miscarriage(p, c, pregnant_trait_id, date, new_memories=new_memories))
        # ck3_chronicler-667: cadet-branch / culture-conversion / faith-
        # conversion deltas. Lookups live on SaveSnapshot to resolve
        # numeric IDs to engine name strings inside the payload.
        out.extend(
            _diff_state_changes(
                p,
                c,
                houses_lookup=curr.houses_lookup,
                cultures_lookup=curr.cultures_lookup,
                faiths_lookup=curr.faiths_lookup,
                date=date,
            )
        )

    # Stable order: by character ID, then event type. Makes replay
    # deterministic for testing and for the biography prompt.
    out.sort(key=lambda d: (d.event.c, d.event.t))
    return out


def _diff_one_character(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    date: str,
    *,
    characters: dict[int, CharacterSnapshot],
    new_memories: list[MemorySnapshot],
) -> list[DiffEvent]:
    """Emit events for a single character's state delta.

    ``characters`` is the curr-snapshot's full character map; per-char
    code reaches into it for cross-references (e.g. resolving the
    deceased relative on a relative_died memory; ck3_chronicler-7jwu).
    ``new_memories`` is the precomputed newly-visible memory window
    (curr minus prev by memory_id) — diff_snapshots computes it once
    per pair and shares it with _diff_miscarriage (27ov.38).
    """
    events: list[DiffEvent] = []

    # 1. Death — newly dead in curr. Two signals can mark a death and they
    #    don't always move together: `is_dead` flips when CK3 moves the char
    #    into a dead collection, while `death_date` is populated from
    #    dead_data regardless of which collection holds the char. At
    #    game-exit CK3 can record the dying ruler with dead_data set (so
    #    death_date appears) while still listing them in `living`
    #    (is_dead=False). Emit on either newly-appearing signal so a
    #    death-at-exit still fires (ck3_chronicler-l8h2) — death_date is what
    #    the DB column and startup catch-up already trust. `_make_death`
    #    prefers death_date, so the payload is correct either way.
    #    ck3_chronicler-27ov.37 (audit M-D1): became_dead additionally
    #    requires prev.death_date to be unset — in the l8h2 sequence,
    #    tick N already fired via death_date_appeared (death_date set
    #    while still listed living) and tick N+1's is_dead flip must not
    #    fire a second DeathEvent. The DB UNIQUE absorbed the duplicate,
    #    but dedup-free consumers (per-tick tally, smoke tooling) counted
    #    the death twice.
    became_dead = not prev.is_dead and curr.is_dead and prev.death_date is None
    death_date_appeared = prev.death_date is None and curr.death_date is not None
    if became_dead or death_date_appeared:
        events.append(_make_death(curr, date))

    # 2. Travel — location changed (only for living chars; dead chars
    #    don't travel and a None→None or whatever-→None transition on
    #    death is noise we don't want)
    if (
        not curr.is_dead
        and prev.location_id is not None
        and curr.location_id is not None
        and prev.location_id != curr.location_id
    ):
        events.append(_make_travel(curr, prev.location_id, curr.location_id, date))

    # 3. Marriage — new spouse appeared
    new_spouses = set(curr.family.spouses) - set(prev.family.spouses)
    # primary_spouse counts too; but only emit one event per unique new ID.
    # ck3_chronicler-bcbx: don't fire when primary_spouse is just being
    # *promoted* from the existing spouses tuple — e.g. polygamist's
    # primary spouse dies and CK3 promotes a non-primary spouse to
    # primary. That's not a new marriage; it's bookkeeping.
    if (
        curr.family.primary_spouse
        and curr.family.primary_spouse != prev.family.primary_spouse
        and curr.family.primary_spouse not in prev.family.spouses
    ):
        new_spouses.add(curr.family.primary_spouse)
    for spouse_id in sorted(new_spouses):
        events.append(_make_marriage(curr, spouse_id, date))

    # 4. Divorce — spouse moved to former_spouses
    new_former = set(curr.family.former_spouses) - set(prev.family.former_spouses)
    for former_id in sorted(new_former):
        events.append(_make_divorce(curr, former_id, date))

    # 5. Nickname change — gained, lost, or replaced an epithet. Skipped
    #    on the death-snapshot since the change couldn't have happened
    #    between the moment of death and the next save; either it was
    #    caught earlier or it's CK3 retroactively writing data.
    if not curr.is_dead and prev.nickname != curr.nickname:
        events.append(_make_nickname(curr, prev.nickname, curr.nickname, date))

    # 6. Adventurer mode transitions — government type changing into or
    #    out of 'landless_adventurer_government' (Roads to Power DLC).
    #    Skipped on dead chars (death may clear landed_data).
    if not curr.is_dead and prev.government != curr.government:
        was_adv = prev.government == _LANDLESS_ADVENTURER_GOVERNMENT
        is_adv = curr.government == _LANDLESS_ADVENTURER_GOVERNMENT
        if not was_adv and is_adv:
            events.append(_make_adventurer_started(curr, date))
        elif was_adv and not is_adv:
            events.append(_make_adventurer_ended(curr, date))
        elif (
            not was_adv
            and not is_adv
            and prev.government is not None
            and curr.government is not None
        ):
            # ck3_chronicler-3v0s follow-up: non-adventurer government
            # transition (tribal→feudal, feudal→administrative, etc.).
            # Both branches above guard the adventurer-mode beats; this
            # branch covers everything else. None-on-either-side is
            # skipped because that's typically the engine clearing
            # landed_data on a non-landed character, not a regime shift.
            events.append(_make_government_changed(curr, prev.government, curr.government, date))

    # 7. Vanilla memories — new entries in the memory list (precomputed
    #    by diff_snapshots, see the docstring).
    for mem in new_memories:
        events.append(_make_vanilla_memory(curr, mem, characters, date))

    # 8. ck3_chronicler-jrwe: narratively interesting decisions taken
    #    since the previous tick. CK3 doesn't emit a vanilla memory for
    #    most decisions (the runestone-raise live signal is one
    #    confirmed example — the fp1 event chain has zero
    #    ``add_character_memory`` calls). Synthesise from the
    #    decision_cooldowns set delta, filtered through the
    #    _NARRATIVELY_INTERESTING_DECISIONS allowlist so administrative
    #    decisions don't drown the event log.
    # ck3_chronicler-70e8: key on (decision_id, end_date) so a re-take
    # that lengthens / resets the cooldown isn't silently absorbed as
    # "already taken". CK3 overwrites the cooldown entry on a re-take.
    prev_decisions = {(did, end) for did, end in prev.decisions_taken}
    for decision_id, end_date in curr.decisions_taken:
        if (decision_id, end_date) in prev_decisions:
            continue
        if decision_id not in _NARRATIVELY_INTERESTING_DECISIONS:
            continue
        events.append(_make_decision_taken(curr, decision_id, end_date, date))

    return events


# --- event constructors ---


def _make_death(c: CharacterSnapshot, date: str) -> DiffEvent:
    # The death_date from the snapshot is more accurate than the diff
    # window's current_date; prefer it.
    death_date = c.death_date or date
    # ck3_chronicler-caxv: cause + killer come from CharacterSnapshot's
    # death_cause / death_killer fields, which the parser pulls from
    # dead_data.reason / dead_data.killer. cause is engine-string form
    # ('death_old_age', 'death_battle', 'death_assassination', etc. —
    # 137 distinct values observed live); biography prompts can map
    # these to flavour copy. killer is a char id when CK3 records one
    # (battle deaths, executions, murders) — None for natural causes.
    participants = list(_family_participants(c))
    if c.death_killer is not None and not any(
        role == "killer" and pid == c.death_killer for role, pid in participants
    ):
        participants.append(("killer", c.death_killer))
    return DiffEvent(
        event=DeathEvent(
            d=death_date,
            c=c.ck3_id,
            p=DeathPayload(
                cause=c.death_cause,
                killer=c.death_killer,
                death_age=_compute_death_age(c.birth_date, death_date),
            ),
        ),
        participants=tuple(participants),
    )


def _compute_death_age(birth_date: str | None, death_date: str | None) -> int | None:
    """ck3_chronicler-caxv: deceased's age at death in whole years.

    Returns None when either date is missing or unparseable. Uses the
    short-form CK3 date (YYYY.M.D) → ISO normaliser, then falls back
    to a year-only subtraction if full-date parse fails (e.g. CK3
    occasionally writes 'AD 1066' without a month — defensive).
    """
    if not birth_date or not death_date:
        return None
    birth_iso = parse_ck3_date(birth_date)
    death_iso = parse_ck3_date(death_date)
    if birth_iso is None or death_iso is None:
        return None
    birth_year = int(birth_iso[:4])
    death_year = int(death_iso[:4])
    age = death_year - birth_year
    # If death MM-DD is before birth MM-DD, subtract one year — they
    # hadn't had their birthday yet that year.
    if death_iso[5:] < birth_iso[5:]:
        age -= 1
    return max(age, 0)


def _make_travel(c: CharacterSnapshot, from_loc: int, to_loc: int, date: str) -> DiffEvent:
    return DiffEvent(
        event=TravelEvent(
            d=date,
            c=c.ck3_id,
            p=TravelPayload(from_location=from_loc, to_location=to_loc),
        ),
        participants=(),
    )


def _make_marriage(c: CharacterSnapshot, spouse_id: int, date: str) -> DiffEvent:
    return DiffEvent(
        event=MarriageEvent(
            d=date,
            c=c.ck3_id,
            p=MarriagePayload(spouse_character_id=spouse_id),
        ),
        participants=(("spouse", spouse_id),),
    )


def _make_divorce(c: CharacterSnapshot, former_id: int, date: str) -> DiffEvent:
    return DiffEvent(
        event=DivorceEvent(
            d=date,
            c=c.ck3_id,
            p=DivorcePayload(former_spouse_character_id=former_id),
        ),
        participants=(("former_spouse", former_id),),
    )


def _diff_modifiers(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-n0s4: emit modifier_acquired when a new engine
    modifier key appears on the character's ``character_modifier``
    list. Acquired-only — modifier removals (event-modifier expiry,
    state-mod end) are out of scope; the noise/value ratio of expiry
    events is poor and the rest of the engine state usually carries
    the same information (e.g. a mourning modifier ends without
    needing a separate event when life moves on).

    Skipped on dead chars — the death tick may reshuffle modifier
    state as the engine cleans up. Same defensive guard as
    :func:`_diff_traits`."""
    if curr.is_dead:
        return []
    new_mods = set(curr.modifiers) - set(prev.modifiers)
    if not new_mods:
        return []
    events: list[DiffEvent] = []
    for key in sorted(new_mods):
        events.append(
            DiffEvent(
                event=ModifierAcquiredEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=ModifierPayload(modifier_key=key),
                ),
                participants=(),
            )
        )
    return events


def _diff_perks(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-rgay: emit perk_acquired when a new lifestyle
    perk key appears on ``alive_data.perk``. Acquired-only — perk
    resets (full tree reroll, in-game refund decision) are structural
    enough to be out of scope here; they fall under their own
    decision-level signal if it surfaces narratively.

    Skipped on dead chars — the death tick may zero perk state as the
    engine cleans up. Same defensive guard as :func:`_diff_modifiers`."""
    if curr.is_dead:
        return []
    new_perks = set(curr.perks) - set(prev.perks)
    if not new_perks:
        return []
    events: list[DiffEvent] = []
    for key in sorted(new_perks):
        events.append(
            DiffEvent(
                event=PerkAcquiredEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=PerkPayload(perk_key=key),
                ),
                participants=(),
            )
        )
    return events


def _diff_lifestyle_commits(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-9p2g: emit ``lifestyle_committed`` when a new
    perk's lifestyle is one the character had no representation in
    previously.

    Builds the set of lifestyles the character had a perk in BEFORE
    this tick, then for every new perk this tick checks whether its
    lifestyle (via :func:`lifestyle_of_perk`) is in that set. New
    lifestyles fire exactly one event each, sorted by lifestyle_key
    for deterministic replay; ``first_perk_key`` is the lex-smallest
    KNOWN perk in that new lifestyle from this tick.

    Unknown perks (not in the static lifestyle_perks map) are silently
    skipped — graceful degrade to "no event" until the map is extended.

    Skipped on dead chars — same defensive guard as :func:`_diff_perks`.
    """
    if curr.is_dead:
        return []
    # Import here to avoid a circular-ish module dependency at top.
    from chronicler.save.lifestyle_perks import lifestyle_of_perk

    new_perks = set(curr.perks) - set(prev.perks)
    if not new_perks:
        return []
    prev_lifestyles = {ls for ls in (lifestyle_of_perk(p) for p in prev.perks) if ls is not None}
    # Group new perks by lifestyle, dropping unknowns.
    new_perks_by_lifestyle: dict[str, list[str]] = {}
    for perk in new_perks:
        ls = lifestyle_of_perk(perk)
        if ls is None or ls in prev_lifestyles:
            continue
        new_perks_by_lifestyle.setdefault(ls, []).append(perk)
    if not new_perks_by_lifestyle:
        return []
    events: list[DiffEvent] = []
    for lifestyle in sorted(new_perks_by_lifestyle.keys()):
        first_perk = sorted(new_perks_by_lifestyle[lifestyle])[0]
        events.append(
            DiffEvent(
                event=LifestyleCommittedEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=LifestyleCommittedPayload(
                        lifestyle_key=lifestyle,
                        first_perk_key=first_perk,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _diff_concubines(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    characters: dict[int, CharacterSnapshot],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-gu7j: emit concubine_taken when a new id appears
    in family.concubines between snapshots. Taken-only — release on
    death is covered by the death event; manumission/repatriation is
    out of scope for v1. Skipped on dead chars defensively (engine
    pruning could re-shuffle relations on the death tick)."""
    if curr.is_dead:
        return []
    new_concubines = set(curr.family.concubines) - set(prev.family.concubines)
    if not new_concubines:
        return []
    events: list[DiffEvent] = []
    for cid in sorted(new_concubines):
        partner = characters.get(cid)
        name = partner.first_name if partner is not None else None
        events.append(
            DiffEvent(
                event=ConcubineTakenEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=ConcubinePayload(
                        concubine_id=cid,
                        concubine_name=name,
                    ),
                ),
                participants=(("concubine", cid),),
            )
        )
    return events


def _diff_traits(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    traits_lookup: tuple[str, ...],
    date: str,
) -> list[DiffEvent]:
    """Emit TraitGainedEvent / TraitLostEvent for trait set deltas.

    Skipped on dead chars — death may clear or reshuffle traits in
    engine bookkeeping (haven't observed it but cheap defense).

    No noise filter at v1 — emit every trait transition. Some are
    obvious noise (educator-level changes once per character) but the
    user explicitly wanted narratively-named traits like 'faltering_heart'
    surfaced and a denylist may filter the wrong things. Defer noise
    control to a follow-up issue once prompt iteration shows what hurts.
    """
    if curr.is_dead:
        return []
    prev_set = set(prev.traits)
    curr_set = set(curr.traits)
    if prev_set == curr_set:
        return []
    events: list[DiffEvent] = []
    for trait_id in sorted(curr_set - prev_set):
        events.append(
            DiffEvent(
                event=TraitGainedEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=TraitPayload(
                        trait_id=trait_id,
                        trait_name=_lookup_trait_name(trait_id, traits_lookup),
                    ),
                ),
                participants=(),
            )
        )
    for trait_id in sorted(prev_set - curr_set):
        events.append(
            DiffEvent(
                event=TraitLostEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=TraitPayload(
                        trait_id=trait_id,
                        trait_name=_lookup_trait_name(trait_id, traits_lookup),
                    ),
                ),
                participants=(),
            )
        )
    return events


# ck3_chronicler-4pj: name of the CK3 trait CK3 attaches to a pregnant
# character. The integer id depends on the save's traits_lookup ordering
# (observed live as 102 in v09-smoke); we resolve by name at runtime so
# the detection survives different mods / DLC orderings.
_PREGNANT_TRAIT_NAME = "pregnant"


def _trait_id_by_name(name: str, traits_lookup: tuple[str, ...]) -> int | None:
    """Reverse-lookup helper: find a trait_id given the engine name.

    Returns None when the lookup table doesn't contain the name (mod
    drift, missing DLC, empty lookup). Linear scan is fine — the lookup
    fires at most once per character per diff and the table is bounded
    (~600 entries in vanilla)."""
    for idx, n in enumerate(traits_lookup):
        if n == name:
            return idx
    return None


def _diff_miscarriage(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    pregnant_trait_id: int | None,
    date: str,
    *,
    new_memories: list[MemorySnapshot],
) -> list[DiffEvent]:
    """ck3_chronicler-4pj: emit a miscarriage event when the ``pregnant``
    trait was lost without a corresponding ``child_born`` vanilla
    memory landing in the same diff window.

    Detection is set-membership based:

    - ``pregnant_trait_id`` is the integer trait id of ``pregnant``,
      resolved once per diff by :func:`diff_snapshots` (27ov.38). None
      means the save's lookup table doesn't include it (e.g.
      minimal-mod test fixtures); silently skip.
    - The trait must have been in ``prev.traits`` and not in
      ``curr.traits`` — i.e. the pregnancy ended this tick.
    - No newly-observed memory of type ``child_born`` for this
      character (``new_memories`` is the precomputed window shared
      with the vanilla_memory emission). CK3 emits the memory on
      successful birth, so its absence (combined with the trait loss)
      is the signal we use to distinguish miscarriage from normal
      birth.

    Skipped on dead chars — the engine clears traits at death, which
    would otherwise spuriously fire on women who happened to be
    pregnant when killed (their death is the dominant event we
    already capture via :func:`_make_death`).

    The payload's ``assumed_father_id`` is the character's
    ``family.primary_spouse`` — same heuristic CK3 uses for default
    paternity. None when there's no recorded primary spouse."""
    if curr.is_dead:
        return []
    if pregnant_trait_id is None:
        return []
    was_pregnant = pregnant_trait_id in prev.traits
    is_pregnant = pregnant_trait_id in curr.traits
    if not (was_pregnant and not is_pregnant):
        return []
    if any(m.memory_type == "child_born" for m in new_memories):
        return []
    return [
        DiffEvent(
            event=MiscarriageEvent(
                d=date,
                c=curr.ck3_id,
                p=MiscarriagePayload(
                    assumed_father_id=curr.family.primary_spouse,
                ),
            ),
            participants=(
                (("assumed_father", curr.family.primary_spouse),)
                if curr.family.primary_spouse is not None
                else ()
            ),
        )
    ]


def _diff_titles(
    character_id: int,
    prev: SaveSnapshot,
    curr: SaveSnapshot,
    date: str,
) -> list[DiffEvent]:
    """Emit TitleAcquired / TitleRelinquished / TitleCreated events for
    one character's holdings between snapshots (ck3_chronicler-dr9).

    For ``TitleAcquired``: title was held by someone else (or no one) in
    prev, by ``character_id`` in curr.
    For ``TitleRelinquished``: title was held by ``character_id`` in
    prev, by someone else (or no one) in curr.
    For ``TitleCreated``: title doesn't exist in prev at all and is held
    by ``character_id`` in curr (decision-driven creation, e.g. unify
    petty kingdom).

    No noise mitigation at this layer per the issue notes — emit per
    title, defer coalescing until live testing shows the firehose hurts.

    ck3_chronicler-hk9i: reads from the precomputed
    :data:`SaveSnapshot.title_holders` reverse index — set lookup per
    character instead of a full scan over every title row in the
    snapshot, which dominated diff cost on populous bookmarks.
    """
    prev_titles = prev.titles
    curr_titles = curr.titles
    # ck3_chronicler-qz5y: when prev has no titles at all the diff
    # window's "baseline" is empty (stale or crash-recovery state). Every
    # currently-held title would otherwise be flagged as freshly-created,
    # generating a phantom title_created storm for titles that have
    # existed since 867 AD.
    if not prev_titles:
        return []
    events: list[DiffEvent] = []
    # ck3_chronicler-hk9i: prefer the precomputed reverse index built at
    # parse time; fall back to a one-shot scan for hand-constructed
    # SaveSnapshot instances in tests where ``title_holders`` is empty.
    if prev.title_holders:
        prev_held = prev.title_holders.get(character_id, frozenset())
    else:
        prev_held = frozenset(tid for tid, t in prev_titles.items() if t.holder_id == character_id)
    if curr.title_holders:
        curr_held = curr.title_holders.get(character_id, frozenset())
    else:
        curr_held = frozenset(tid for tid, t in curr_titles.items() if t.holder_id == character_id)

    for tid in sorted(curr_held - prev_held):
        title = curr_titles[tid]
        prev_title = prev_titles.get(tid)
        if prev_title is None:
            # title didn't exist in prev — decision-driven creation
            events.append(
                DiffEvent(
                    event=TitleCreatedEvent(
                        d=date,
                        c=character_id,
                        p=TitleCreatedPayload(
                            title_id=tid,
                            title_key=title.key,
                            title_name=title.name,
                            tier=title.tier,
                        ),
                    ),
                    participants=(),
                )
            )
        else:
            # title existed; we just took it over
            events.append(
                DiffEvent(
                    event=TitleAcquiredEvent(
                        d=date,
                        c=character_id,
                        p=TitleAcquiredPayload(
                            title_id=tid,
                            title_key=title.key,
                            title_name=title.name,
                            tier=title.tier,
                            from_holder_id=prev_title.holder_id,
                        ),
                    ),
                    participants=(
                        (("former_holder", prev_title.holder_id),)
                        if prev_title.holder_id is not None
                        else ()
                    ),
                )
            )

    for tid in sorted(prev_held - curr_held):
        prev_title = prev_titles[tid]
        curr_title = curr_titles.get(tid)
        events.append(
            DiffEvent(
                event=TitleRelinquishedEvent(
                    d=date,
                    c=character_id,
                    p=TitleRelinquishedPayload(
                        title_id=tid,
                        title_key=prev_title.key,
                        title_name=prev_title.name,
                        tier=prev_title.tier,
                        to_holder_id=curr_title.holder_id if curr_title else None,
                    ),
                ),
                participants=(
                    (("new_holder", curr_title.holder_id),)
                    if curr_title and curr_title.holder_id is not None
                    else ()
                ),
            )
        )

    return events


def _diff_state_changes(
    prev: CharacterSnapshot,
    curr: CharacterSnapshot,
    *,
    houses_lookup: dict[int, str],
    cultures_lookup: dict[int, str],
    faiths_lookup: dict[int, str],
    date: str,
) -> list[DiffEvent]:
    """Emit HouseChangeEvent / CultureChangeEvent / FaithChangeEvent.

    Skipped on dead chars — engine bookkeeping may zero state on death
    (haven't seen it but cheap defense; same as nickname / adventurer
    diffs). Each event carries both numeric IDs (always present) and
    the resolved engine-key names from the lookup tables (None when
    the lookup didn't have an entry — usually means a fresh house ID
    that was just created and the snapshot doesn't index it, in
    which case the next save will fill it in).
    """
    if curr.is_dead:
        return []
    events: list[DiffEvent] = []

    if prev.dynasty_house_id != curr.dynasty_house_id:
        events.append(
            DiffEvent(
                event=HouseChangeEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=HouseChangePayload(
                        from_house_id=prev.dynasty_house_id,
                        from_house_name=(
                            houses_lookup.get(prev.dynasty_house_id)
                            if prev.dynasty_house_id is not None
                            else None
                        ),
                        to_house_id=curr.dynasty_house_id,
                        to_house_name=(
                            houses_lookup.get(curr.dynasty_house_id)
                            if curr.dynasty_house_id is not None
                            else None
                        ),
                    ),
                ),
                participants=(),
            )
        )

    if prev.culture_id != curr.culture_id:
        events.append(
            DiffEvent(
                event=CultureChangeEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=CultureChangePayload(
                        from_culture_id=prev.culture_id,
                        from_culture_name=(
                            cultures_lookup.get(prev.culture_id)
                            if prev.culture_id is not None
                            else None
                        ),
                        to_culture_id=curr.culture_id,
                        to_culture_name=(
                            cultures_lookup.get(curr.culture_id)
                            if curr.culture_id is not None
                            else None
                        ),
                    ),
                ),
                participants=(),
            )
        )

    if prev.faith_id != curr.faith_id:
        events.append(
            DiffEvent(
                event=FaithChangeEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=FaithChangePayload(
                        from_faith_id=prev.faith_id,
                        from_faith_name=(
                            faiths_lookup.get(prev.faith_id) if prev.faith_id is not None else None
                        ),
                        to_faith_id=curr.faith_id,
                        to_faith_name=(
                            faiths_lookup.get(curr.faith_id) if curr.faith_id is not None else None
                        ),
                    ),
                ),
                participants=(),
            )
        )

    return events


def _lookup_trait_name(trait_id: int, traits_lookup: tuple[str, ...]) -> str | None:
    """Resolve a trait_id to its name via the save's traits_lookup list.
    Returns None for out-of-range IDs (defensive — shouldn't happen on
    a well-formed save but the lookup may be empty if the snapshot was
    constructed without one)."""
    if 0 <= trait_id < len(traits_lookup):
        return traits_lookup[trait_id]
    return None


def _war_side_payload(
    war: WarSnapshot,
    *,
    side: str | None,
) -> WarSidePayload:
    """Build the shared WarSidePayload from a WarSnapshot + a resolved
    side. ``side`` is None for ``war_concluded`` events because the
    concluded war's participant breakdown isn't always preserved
    across the diff (CK3 deletes concluded wars from the snapshot)."""
    return WarSidePayload(
        war_id=war.war_id,
        war_name=strip_loca_markup(war.name),
        casus_belli_type=war.casus_belli_type,
        targeted_titles=war.targeted_titles,
        side=side if side in ("attacker", "defender") else None,
        primary_attacker_id=war.primary_attacker_id,
        primary_defender_id=war.primary_defender_id,
        claimant_id=war.claimant_id,
    )


def _resolve_war_side(war: WarSnapshot, ck3_id: int) -> str | None:
    """Decide which side a tracked character is on, given the war
    snapshot's attacker/defender participant sets. Returns None when
    the character isn't in either set — shouldn't normally happen
    since the call site already established membership via
    character_to_wars, but the snapshots can be out of sync if rakaly
    truncates a side mid-parse."""
    if ck3_id in war.attacker_participants:
        return "attacker"
    if ck3_id in war.defender_participants:
        return "defender"
    return None


def _diff_wars(
    ck3_id: int,
    char: CharacterSnapshot,
    prev_wars: dict[int, WarSnapshot],
    curr_wars: dict[int, WarSnapshot],
    prev_char_to_wars: dict[int, frozenset[int]],
    curr_char_to_wars: dict[int, frozenset[int]],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-o7j: emit War[Declared|Joined|Left|Concluded]
    events from per-character war-set deltas + war_id existence check.

    Logic mirrors :func:`_diff_alliances` — set-diff on
    character_to_wars[ck3_id]. The added wrinkle is distinguishing the
    four event types:

    - war_id new in curr_wars + char is the cb.attacker/cb.defender
      → ``war_declared`` (this character is the principal)
    - war_id new in curr_wars + char is a non-principal participant
      → ``war_joined`` (called in via alliance/vassalage)
    - war_id existed in prev + char is newly a participant
      → ``war_joined``
    - war_id present in prev_wars but absent from curr_wars entirely
      → ``war_concluded`` (CK3 deletes concluded wars from active_wars)
    - war_id still in curr_wars + char left the participant set
      → ``war_left`` (separate peace, side switch, or released)

    Skipped on dead characters; deaths produce a flood of
    ``war_left`` events as the engine cleans up participation, all of
    which we'd already capture at the ``DeathEvent`` boundary.
    """
    if char.is_dead:
        return []
    prev_set = prev_char_to_wars.get(ck3_id, frozenset())
    curr_set = curr_char_to_wars.get(ck3_id, frozenset())
    if prev_set == curr_set:
        return []

    events: list[DiffEvent] = []

    for war_id in sorted(curr_set - prev_set):
        war = curr_wars.get(war_id)
        if war is None:
            continue
        side = _resolve_war_side(war, ck3_id)
        is_principal = ck3_id in (
            war.primary_attacker_id,
            war.primary_defender_id,
        )
        # War newly appeared this tick AND character is the principal
        # on either side → declared. Otherwise (existing war, or new
        # war but called-in vassal) → joined.
        if war_id not in prev_wars and is_principal:
            events.append(
                DiffEvent(
                    event=WarDeclaredEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(war, side=side),
                    ),
                    participants=_war_participants(war, side),
                )
            )
        else:
            events.append(
                DiffEvent(
                    event=WarJoinedEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(war, side=side),
                    ),
                    participants=_war_participants(war, side),
                )
            )

    for war_id in sorted(prev_set - curr_set):
        prev_war = prev_wars.get(war_id)
        if prev_war is None:
            # Shouldn't happen — the set was built from prev_wars — but
            # be defensive: skip rather than emit a bogus payload.
            continue
        if war_id not in curr_wars:
            # War vanished — concluded. Side may be unknowable on the
            # current tick (war is gone), but we can still resolve from
            # prev_war's snapshot.
            side = _resolve_war_side(prev_war, ck3_id)
            events.append(
                DiffEvent(
                    event=WarConcludedEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(prev_war, side=side),
                    ),
                    participants=_war_participants(prev_war, side),
                )
            )
        else:
            # War still active, character left it
            side = _resolve_war_side(prev_war, ck3_id)
            events.append(
                DiffEvent(
                    event=WarLeftEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(prev_war, side=side),
                    ),
                    participants=_war_participants(prev_war, side),
                )
            )

    return events


def _diff_dynasty_legacies(
    ck3_id: int,
    char: CharacterSnapshot,
    prev_perks: dict[int, frozenset[str]],
    curr_perks: dict[int, frozenset[str]],
    *,
    house_to_dynasty: dict[int, int],
    dynasties_lookup: dict[int, str],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-d83: emit dynasty_legacy_unlocked when the
    character's dynasty advances a perk between snapshots.

    Resolves the character's dynasty via house_to_dynasty (their
    dynasty_house_id → dynasty_id). When the character has no house
    or the house has no dynasty edge, no events emit. Skipped on dead
    characters as a matter of principle (their dynasty's state
    transitions belong to the living)."""
    if char.is_dead:
        return []
    if char.dynasty_house_id is None:
        return []
    dynasty_id = house_to_dynasty.get(char.dynasty_house_id)
    if dynasty_id is None:
        return []
    prev_set = prev_perks.get(dynasty_id, frozenset())
    curr_set = curr_perks.get(dynasty_id, frozenset())
    new_perks = curr_set - prev_set
    if not new_perks:
        return []
    dynasty_name = dynasties_lookup.get(dynasty_id)
    events: list[DiffEvent] = []
    for legacy_key in sorted(new_perks):
        events.append(
            DiffEvent(
                event=DynastyLegacyUnlockedEvent(
                    d=date,
                    c=ck3_id,
                    p=DynastyLegacyPayload(
                        dynasty_id=dynasty_id,
                        dynasty_name=dynasty_name,
                        legacy_key=legacy_key,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _diff_dynasty_splendor(
    ck3_id: int,
    char: CharacterSnapshot,
    prev_renown: dict[int, float],
    curr_renown: dict[int, float],
    *,
    house_to_dynasty: dict[int, int],
    dynasties_lookup: dict[int, str],
    dynasty_heads: dict[int, int],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-ei8t: emit splendor_increased when the character's
    dynasty crosses a splendor tier upward between snapshots.

    Mirrors :func:`_diff_dynasty_legacies`'s character→dynasty
    resolution via house_to_dynasty. Tier comes from
    :func:`derive_splendor_tier` applied to lifetime accumulated
    renown. Increases-only — tier drops (rare; renown-refund paths)
    produce no event. Skipped on dead characters: their dynasty's
    state transitions belong to the living."""
    from chronicler.save.dynasty_splendor import derive_splendor_tier

    if char.is_dead:
        return []
    if char.dynasty_house_id is None:
        return []
    dynasty_id = house_to_dynasty.get(char.dynasty_house_id)
    if dynasty_id is None:
        return []
    prev_acc = prev_renown.get(dynasty_id, 0.0)
    curr_acc = curr_renown.get(dynasty_id, 0.0)
    prev_tier = derive_splendor_tier(prev_acc)
    curr_tier = derive_splendor_tier(curr_acc)
    if curr_tier <= prev_tier:
        return []
    return [
        DiffEvent(
            event=SplendorIncreasedEvent(
                d=date,
                c=ck3_id,
                p=SplendorPayload(
                    dynasty_id=dynasty_id,
                    dynasty_name=dynasties_lookup.get(dynasty_id),
                    dynasty_head_id=dynasty_heads.get(dynasty_id),
                    old_tier=prev_tier,
                    new_tier=curr_tier,
                    total_renown_at_change=curr_acc,
                ),
            ),
            participants=(),
        )
    ]


_CONTRACT_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "invalidated"})


def _diff_contracts(
    ck3_id: int,
    char: CharacterSnapshot,
    prev_contracts: dict[int, ContractSnapshot],
    curr_contracts: dict[int, ContractSnapshot],
    prev_char_to_contracts: dict[int, frozenset[int]],
    curr_char_to_contracts: dict[int, frozenset[int]],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-621o (8aie slice 6): emit contract_completed
    when a tracked adventurer's contract transitions into a terminal
    status (``completed`` or ``invalidated``).

    Status lifecycle observed live (2026-05-17 Genji smoke):
        available -> accepted -> completed | invalidated

    Diff signal: for each contract_id owned by ck3_id (union of prev
    and curr reverse indexes), compare prev_status with curr_status:
        - prev non-terminal AND curr terminal -> emit
        - prev terminal -> skip (already emitted on the prior tick)
        - curr missing entirely is currently NOT emitted: contracts
          that disappear without ever surfacing a terminal status are
          most likely 'available' offers that expired silently — too
          low-signal to spend an event on. A future enhancement could
          treat ``accepted -> gone`` as an inferred completion if the
          live save shows this pattern.

    Skipped on dead chars (mirrors _diff_activities): death-tick
    cleanup may purge contract participation entries.
    """
    if char.is_dead:
        return []
    prev_ids = prev_char_to_contracts.get(ck3_id, frozenset())
    curr_ids = curr_char_to_contracts.get(ck3_id, frozenset())
    # Examine the union — a contract may transition into terminal in
    # curr without changing reverse-index membership (status flips but
    # owner stays the same).
    candidate_ids = prev_ids | curr_ids
    if not candidate_ids:
        return []
    events: list[DiffEvent] = []
    for contract_id in sorted(candidate_ids):
        curr_snap = curr_contracts.get(contract_id)
        if curr_snap is None:
            continue
        if curr_snap.status not in _CONTRACT_TERMINAL_STATUSES:
            continue
        prev_snap = prev_contracts.get(contract_id)
        if prev_snap is not None and prev_snap.status in _CONTRACT_TERMINAL_STATUSES:
            # Already terminal in prev — emitted on a prior tick (or
            # was terminal at baseline adoption). Don't re-emit.
            continue
        outcome: str = curr_snap.status  # type: ignore[assignment]
        events.append(
            DiffEvent(
                event=ContractCompletedEvent(
                    d=date,
                    c=ck3_id,
                    p=ContractPayload(
                        contract_type=curr_snap.contract_type,
                        name=curr_snap.name,
                        tier=curr_snap.tier,
                        employer_id=curr_snap.employer_id,
                        location_province_id=curr_snap.location_province_id,
                        outcome=outcome,  # type: ignore[arg-type]
                        acceptance_date=curr_snap.acceptance_date,
                        completion_date=curr_snap.completion_date,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _resolve_employee_name(
    employee_id: int | None,
    characters: dict[int, CharacterSnapshot],
) -> str | None:
    """Look up a character's first_name from the snapshot. Returns
    None when the id is missing or the character has no first_name
    (e.g. pruned record). Mirrors gu7j's diff-time name resolution
    convention so the renderer can read the name directly off the
    payload without a second lookup."""
    if employee_id is None:
        return None
    char = characters.get(employee_id)
    if char is None:
        return None
    return char.first_name


def _diff_domicile(
    ck3_id: int,
    char: CharacterSnapshot,
    prev_domiciles: dict[int, DomicileSnapshot],
    curr_domiciles: dict[int, DomicileSnapshot],
    prev_char_to_domicile: dict[int, int],
    curr_char_to_domicile: dict[int, int],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-r343 (8aie slice 5): emit domicile_moved when
    a tracked character's domicile's province_id changes between
    snapshots.

    Look up the domicile via character_to_domicile in BOTH prev and
    curr. If either side has no domicile, skip — the chronicler has
    no narrative beat for "first ever domicile" (game-start) or "lost
    your domicile" (rare; covered by death or government-transition
    events).

    Province changes from None to a value (or vice versa) are also
    skipped — those are usually parse anomalies (CK3 occasionally
    emits a malformed location field mid-recalc).

    Skipped on dead chars (mirrors _diff_activities).
    """
    if char.is_dead:
        return []
    prev_did = prev_char_to_domicile.get(ck3_id)
    curr_did = curr_char_to_domicile.get(ck3_id)
    if prev_did is None or curr_did is None:
        return []
    prev_dom = prev_domiciles.get(prev_did)
    curr_dom = curr_domiciles.get(curr_did)
    if prev_dom is None or curr_dom is None:
        return []
    if prev_dom.province_id is None or curr_dom.province_id is None:
        return []
    if prev_dom.province_id == curr_dom.province_id:
        return []
    return [
        DiffEvent(
            event=DomicileMovedEvent(
                d=date,
                c=ck3_id,
                p=DomicilePayload(
                    domicile_id=curr_dom.domicile_id,
                    domicile_type=curr_dom.domicile_type,
                    from_province_id=prev_dom.province_id,
                    to_province_id=curr_dom.province_id,
                ),
            ),
            participants=(),
        )
    ]


def _diff_constructions(
    ck3_id: int,
    char: CharacterSnapshot,
    prev_in_flight: dict[tuple[int, int], ConstructionSnapshot],
    prev_char_to_cons: dict[int, frozenset[tuple[int, int]]],
    curr_char_to_cons: dict[int, frozenset[tuple[int, int]]],
    curr_buildings_by_slot: dict[tuple[int, int], str],
    date: str,
) -> list[DiffEvent]:
    """ck3_chronicler-2ur: emit ``building_completed`` when an in-flight
    construction the tracked character initiated has shipped.

    Per-character set-diff over ``character_to_constructions``: a
    (province_id, slot_index) pair that was in ``prev_char_to_cons[ck3_id]``
    but is NOT in ``curr_char_to_cons[ck3_id]`` left the in-flight set —
    the construction either completed or was cancelled. Disambiguate via
    ``curr_buildings_by_slot``: if the slot now carries the construction's
    ``building`` type, the building shipped. If not (slot still empty,
    or some other type — rare race where a different construction
    completed in the same slot first), treat as cancelled and skip.

    Skipped on dead chars by symmetry with :func:`_diff_epidemics` —
    while a dead character can't *start* construction, the engine's
    cleanup pass on death may pull a construction record while we're
    diffing across the death window. Skipping avoids false-positive
    completions on a deceased constructor.

    No ``building_started`` event in v1: the user's bd 2026-05-09
    framing is on the *completion* moment as the biographically
    load-bearing signal ('during his reign, the Hospices were built at
    Tartu'). Starts and cancellations are filed as v2 follow-ups."""
    if char.is_dead:
        return []
    prev_set = prev_char_to_cons.get(ck3_id, frozenset())
    curr_set = curr_char_to_cons.get(ck3_id, frozenset())
    departed = prev_set - curr_set
    if not departed:
        return []
    events: list[DiffEvent] = []
    for key in sorted(departed):
        cons = prev_in_flight.get(key)
        if cons is None:
            # Defensive — prev_set was built from prev_char_to_cons which
            # was built from prev.in_flight_constructions. Skip rather
            # than emit a payload missing its building name.
            continue
        slot_building = curr_buildings_by_slot.get(key)
        if slot_building != cons.building:
            # Cancelled (slot empty or different type) — skip in v1.
            continue
        events.append(
            DiffEvent(
                event=BuildingCompletedEvent(
                    d=date,
                    c=ck3_id,
                    p=BuildingCompletedPayload(
                        building=cons.building,
                        province_id=cons.province_id,
                        slot_index=cons.slot_index,
                        start_date=cons.start_date,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _war_participants(war: WarSnapshot, side: str | None) -> tuple[tuple[str, int], ...]:
    """Build the participants tuple for a war event. Surfaces the
    primaries on each side (with role labels matching the side
    semantics) so the consolidation prompt's names_map glossary picks
    them up — "Erik joined Sigurd's holy war against Æthelred" is
    only possible if Sigurd and Æthelred are in the participants
    list. The claimant gets a separate role when present (claimant
    wars name a third party who'd benefit)."""
    out: list[tuple[str, int]] = []
    if war.primary_attacker_id is not None:
        out.append(("primary_attacker", war.primary_attacker_id))
    if war.primary_defender_id is not None:
        out.append(("primary_defender", war.primary_defender_id))
    if war.claimant_id is not None and war.claimant_id not in (
        war.primary_attacker_id,
        war.primary_defender_id,
    ):
        out.append(("claimant", war.claimant_id))
    return tuple(out)


# ck3_chronicler-mcu: the canonical CK3 government string for Roads to
# Power's landless-adventurer state. Verified live against Ælla 12267's
# raw record — landed_data.government == this exact value.
_LANDLESS_ADVENTURER_GOVERNMENT = "landless_adventurer_government"


def _make_adventurer_started(c: CharacterSnapshot, date: str) -> DiffEvent:
    return DiffEvent(
        event=AdventurerStartedEvent(
            d=date,
            c=c.ck3_id,
            p=_EmptyPayload(),
        ),
        participants=(),
    )


def _make_adventurer_ended(c: CharacterSnapshot, date: str) -> DiffEvent:
    return DiffEvent(
        event=AdventurerEndedEvent(
            d=date,
            c=c.ck3_id,
            p=_EmptyPayload(),
        ),
        participants=(),
    )


def _make_government_changed(c: CharacterSnapshot, previous: str, new: str, date: str) -> DiffEvent:
    return DiffEvent(
        event=GovernmentChangedEvent(
            d=date,
            c=c.ck3_id,
            p=GovernmentChangedPayload(
                previous_government=previous,
                new_government=new,
            ),
        ),
        participants=(),
    )


def _make_nickname(
    c: CharacterSnapshot, from_nick: str | None, to_nick: str | None, date: str
) -> DiffEvent:
    return DiffEvent(
        event=NicknameEvent(
            d=date,
            c=c.ck3_id,
            p=NicknamePayload(from_nickname=from_nick, to_nickname=to_nick),
        ),
        participants=(),
    )


# ck3_chronicler-jrwe: decisions whose recently-taken transition the
# diff layer should surface as a synthesised :class:`DecisionTakenEvent`.
# Curated rather than open: most CK3 decisions (hold court, train for
# tournament, extract gold from treasury, author book, ...) are
# administrative noise that would drown the prose-relevant beats.
#
# Adding to this list:
# - The decision must produce a state change a biographer would weave
#   ("raised a runestone to his father") rather than a quality-of-life
#   action ("called his court").
# - Empirical signal preferred — observe the decision_id in a real save
#   before adding, since CK3 modders may rename or shadow built-in
#   decisions.
_NARRATIVELY_INTERESTING_DECISIONS: frozenset[str] = frozenset(
    {
        "raise_stele_decision",
    }
)


def _make_decision_taken(
    c: CharacterSnapshot,
    decision_id: str,
    cooldown_end_date: str,
    diff_date: str,
) -> DiffEvent:
    """Synthesise a DecisionTakenEvent for a freshly-observed entry in
    landed_data.decision_cooldowns. Date is the diff window's
    current_date — the actual taken-date is roughly cooldown_end_date
    minus the decision's cooldown duration; biographies should
    interpret the field as 'this was the recent past'."""
    return DiffEvent(
        event=DecisionTakenEvent(
            d=diff_date,
            c=c.ck3_id,
            p=DecisionTakenPayload(
                decision_id=decision_id,
                cooldown_end_date=cooldown_end_date or None,
            ),
        ),
        participants=(),
    )


def _make_vanilla_memory(
    c: CharacterSnapshot,
    mem: MemorySnapshot,
    characters: dict[int, CharacterSnapshot],
    diff_window_date: str,
) -> DiffEvent:
    """Emit a VanillaMemoryEvent for a newly-observed memory.

    Date is the memory's own creation_date (more accurate than the diff
    window). Participants are extracted from the memory record's
    participants dict.

    ck3_chronicler-05va: when creation_date is missing CK3 sometimes
    leaves it empty on engine-generated memories (founder-of-dynasty
    backfill, e.g.). Fall back to the diff window's date so the event
    has a non-empty date string; otherwise two distinct memories of the
    same memory_type for the same character on the same tick collapse
    to the same dedup key and one is silently dropped via the events
    UNIQUE constraint.

    ck3_chronicler-7jwu: for memory_type == "relative_died", look up the
    deceased's snapshot row and thread cause-of-death + age + first name
    into the payload so the surviving relative's biography can weave the
    death into prose. Silent no-op when the deceased has been pruned
    from the snapshot.

    ck3_chronicler-dn7a: when the deceased's record carries a
    death_killer (murder / execution / battle death), thread the
    killer's id + first_name through too. Resolves the killer's name
    via the same characters lookup; both fields stay None for
    natural-cause deaths and for the rare case where the killer has
    been pruned but the deceased hasn't.
    """
    participants_dict = dict(mem.participants)
    deceased_cause: str | None = None
    deceased_age: int | None = None
    deceased_first_name: str | None = None
    deceased_killer_id: int | None = None
    deceased_killer_first_name: str | None = None
    if mem.memory_type == "relative_died":
        dead_id = participants_dict.get("dead_relation")
        if dead_id is not None:
            deceased = characters.get(dead_id)
            if deceased is not None:
                deceased_cause = deceased.death_cause
                deceased_age = _compute_death_age(deceased.birth_date, deceased.death_date)
                deceased_first_name = deceased.first_name
                if deceased.death_killer is not None:
                    deceased_killer_id = deceased.death_killer
                    killer = characters.get(deceased.death_killer)
                    if killer is not None:
                        deceased_killer_first_name = killer.first_name
    return DiffEvent(
        event=VanillaMemoryEvent(
            d=mem.creation_date or diff_window_date,
            c=c.ck3_id,
            p=VanillaMemoryPayload(
                memory_type=mem.memory_type,
                end_date=mem.end_date,
                participants=participants_dict,
                deceased_cause=deceased_cause,
                deceased_age=deceased_age,
                deceased_first_name=deceased_first_name,
                deceased_killer_id=deceased_killer_id,
                deceased_killer_first_name=deceased_killer_first_name,
            ),
        ),
        participants=tuple(mem.participants),
    )


def _family_participants(c: CharacterSnapshot) -> tuple[tuple[str, int], ...]:
    """Pull family members as (role, char_id) tuples for inclusion in
    event_participants. Useful on death events so biographies can find
    the deceased's spouse, children, and parents from the DB."""
    out: list[tuple[str, int]] = []
    if c.family.mother is not None:
        out.append(("mother", c.family.mother))
    if c.family.father is not None:
        out.append(("father", c.family.father))
    if c.family.primary_spouse is not None:
        out.append(("primary_spouse", c.family.primary_spouse))
    for sp in c.family.spouses:
        out.append(("spouse", sp))
    for ch in c.family.children:
        out.append(("child", ch))
    return tuple(out)
