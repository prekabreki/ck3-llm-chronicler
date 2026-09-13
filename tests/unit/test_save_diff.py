"""Tests for chronicler.save.diff — snapshot delta → events.

Constructs synthetic SaveSnapshot pairs and asserts the expected event
list comes out. Each test isolates one event-type detection path.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from chronicler.save.diff import diff_snapshots
from chronicler.save.parse import (
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
    WarSnapshot,
)
from chronicler.schema import (
    ActivityCompletedEvent,
    BuildingCompletedEvent,
    CampCompanionJoinedEvent,
    CampCompanionLeftEvent,
    ContractCompletedEvent,
    DeathEvent,
    DecisionTakenEvent,
    DivorceEvent,
    DomicileMovedEvent,
    EpidemicOutbreakEvent,
    MarriageEvent,
    MiscarriageEvent,
    NicknameEvent,
    TravelEvent,
    VanillaMemoryEvent,
)
from tests.helpers.snapshots import make_char


def _make_char(
    cid: int,
    *,
    is_dead: bool = False,
    death_date: str | None = None,
    death_cause: str | None = None,
    death_killer: int | None = None,
    birth_date: str = "1020.1.1",
    family: FamilySnapshot | None = None,
    location_id: int | None = 100,
    memories: tuple[MemorySnapshot, ...] = (),
    first_name: str = "TestChar",
    nickname: str | None = None,
    decisions_taken: tuple[tuple[str, str], ...] = (),
) -> CharacterSnapshot:
    return make_char(
        cid,
        first_name=first_name,
        nickname=nickname,
        is_dead=is_dead,
        birth_date=birth_date,
        death_date=death_date,
        family=family or FamilySnapshot(),
        location_id=location_id,
        memories=memories,
        death_cause=death_cause,
        death_killer=death_killer,
        decisions_taken=decisions_taken,
    )


def _make_snap(
    chars: dict[int, CharacterSnapshot],
    *,
    date: str = "1067.2.1",
    playthrough_id: str = "test-uuid",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id=playthrough_id,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
    )


# --- death detection ---


def test_death_emitted_when_alive_to_dead() -> None:
    prev = _make_snap({1234: _make_char(1234, is_dead=False)})
    curr = _make_snap(
        {1234: _make_char(1234, is_dead=True, death_date="1067.1.15")},
        date="1067.2.1",
    )
    events = diff_snapshots(prev, curr)
    assert len(events) == 1
    assert isinstance(events[0].event, DeathEvent)
    assert events[0].event.c == 1234
    # Prefer the snapshot's death_date over the diff-window date
    assert events[0].event.d == "1067.1.15"


def test_no_death_when_already_dead() -> None:
    prev = _make_snap({1234: _make_char(1234, is_dead=True, death_date="1066.10.14")})
    curr = _make_snap({1234: _make_char(1234, is_dead=True, death_date="1066.10.14")})
    assert diff_snapshots(prev, curr) == []


def test_death_emitted_when_death_date_appears_but_still_in_living() -> None:
    """ck3_chronicler-l8h2: at game-exit CK3 records the dying ruler with
    dead_data populated (so death_date lands in the snapshot) but may still
    list them in `living`, so the parser reports is_dead=False. The death
    event used to fire ONLY on the is_dead collection flip, so the player's
    own death-at-exit emitted no DeathEvent and never scheduled a biography
    (it only recovered on a chronicler restart via the startup catch-up).
    A newly-appearing death_date must emit a DeathEvent even when is_dead is
    still False, because death_date is the signal the rest of the system
    (the DB column, the startup catch-up) already trusts."""
    prev = _make_snap({1234: _make_char(1234, is_dead=False, death_date=None)})
    curr = _make_snap(
        {1234: _make_char(1234, is_dead=False, death_date="947.6.9")},
        date="947.6.9",
    )
    events = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, DeathEvent)]
    assert len(events) == 1
    assert events[0].event.c == 1234
    assert events[0].event.d == "947.6.9"


def test_death_emitted_once_when_both_signals_flip_together() -> None:
    """ck3_chronicler-l8h2 guard: the normal case where is_dead flips AND
    death_date appears in the same diff must still emit exactly one
    DeathEvent, not two."""
    prev = _make_snap({1234: _make_char(1234, is_dead=False, death_date=None)})
    curr = _make_snap(
        {1234: _make_char(1234, is_dead=True, death_date="1067.1.15")},
        date="1067.2.1",
    )
    events = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, DeathEvent)]
    assert len(events) == 1
    assert events[0].event.d == "1067.1.15"


def test_death_emitted_once_across_two_ticks_in_death_at_exit_sequence() -> None:
    """ck3_chronicler-27ov.37 (audit M-D1): in the l8h2 death-at-exit
    sequence, tick N sees death_date appear while the char is still in
    `living` (fires), and tick N+1 sees is_dead flip with death_date
    already set. The second tick must NOT fire a second DeathEvent —
    the DB UNIQUE absorbed the duplicate, but dedup-free consumers
    (per-tick tally feeding the activity strip, smoke tooling) counted
    the death twice."""
    snap_alive = _make_snap({1234: _make_char(1234, is_dead=False, death_date=None)})
    snap_dying = _make_snap(
        {1234: _make_char(1234, is_dead=False, death_date="947.6.9")},
        date="947.6.9",
    )
    snap_dead = _make_snap(
        {1234: _make_char(1234, is_dead=True, death_date="947.6.9")},
        date="947.7.1",
    )
    tick_n = [e for e in diff_snapshots(snap_alive, snap_dying) if isinstance(e.event, DeathEvent)]
    tick_n1 = [e for e in diff_snapshots(snap_dying, snap_dead) if isinstance(e.event, DeathEvent)]
    assert len(tick_n) == 1
    assert tick_n1 == []


def test_death_event_populates_death_age_from_birth_and_death_dates() -> None:
    """ck3_chronicler-caxv: death payload was empty; downstream biography
    prompts had to fabricate ages from in-game date alone. Now
    DeathPayload.death_age is computed from birth_date and death_date.
    _make_char defaults birth_date to '1020.1.1'; with a January-1
    birthday, the year diff equals the age."""
    prev = _make_snap({1234: _make_char(1234, is_dead=False)})
    curr = _make_snap(
        {1234: _make_char(1234, is_dead=True, death_date="1083.12.23")},
        date="1083.12.23",
    )
    events = diff_snapshots(prev, curr)
    assert len(events) == 1
    assert isinstance(events[0].event, DeathEvent)
    assert events[0].event.p.death_age == 63  # 1083 - 1020, birthday already passed


def test_death_event_carries_cause_and_killer_from_snapshot() -> None:
    """ck3_chronicler-caxv: parser pulls dead_data.reason / dead_data.killer
    onto CharacterSnapshot; diff layer threads both into DeathPayload so
    biographies can render 'killed in battle by X' / 'died of consumption'
    without re-walking the raw save tree."""
    char_alive = make_char(1234, first_name="X", birth_date="1023.1.1")
    char_dead = make_char(
        1234,
        first_name="X",
        birth_date="1023.1.1",
        is_dead=True,
        death_date="1083.4.1",
        death_cause="death_battle",
        death_killer=9999,
    )
    prev = _make_snap({1234: char_alive})
    curr = _make_snap({1234: char_dead}, date="1083.4.1")
    events = diff_snapshots(prev, curr)
    death = events[0]
    assert isinstance(death.event, DeathEvent)
    assert death.event.p.cause == "death_battle"
    assert death.event.p.killer == 9999
    # killer also surfaces as an event participant so DB joins find it.
    assert ("killer", 9999) in death.participants


def test_death_event_omits_killer_when_natural_cause() -> None:
    """death_old_age has no killer; payload should leave killer=None and
    participants should not invent a killer entry."""
    char_alive = make_char(1234, first_name="X", birth_date="1023.1.1")
    char_dead = make_char(
        1234,
        first_name="X",
        birth_date="1023.1.1",
        is_dead=True,
        death_date="1083.12.23",
        death_cause="death_old_age",
    )
    prev = _make_snap({1234: char_alive})
    curr = _make_snap({1234: char_dead}, date="1083.12.23")
    events = diff_snapshots(prev, curr)
    assert events[0].event.p.cause == "death_old_age"
    assert events[0].event.p.killer is None
    assert not any(role == "killer" for role, _ in events[0].participants)


def test_death_age_subtracts_year_when_birthday_not_yet_passed() -> None:
    """Year-diff is 60 but death (1080.3.1) is BEFORE birthday (1020.6.15
    → birthday on 6.15 each year) — age is 59, not 60. Use the
    snapshot helper directly so we can override birth_date."""
    char_alive = make_char(1234, first_name="X", birth_date="1020.6.15")
    char_dead = make_char(
        1234,
        first_name="X",
        birth_date="1020.6.15",
        is_dead=True,
        death_date="1080.3.1",
    )
    prev = _make_snap({1234: char_alive})
    curr = _make_snap({1234: char_dead}, date="1080.3.1")
    events = diff_snapshots(prev, curr)
    assert events[0].event.p.death_age == 59


def test_death_event_includes_family_participants() -> None:
    family = FamilySnapshot(mother=10, father=11, primary_spouse=20, children=(30, 31))
    prev = _make_snap({1234: _make_char(1234, is_dead=False, family=family)})
    curr = _make_snap({1234: _make_char(1234, is_dead=True, death_date="1067.1.15", family=family)})
    events = diff_snapshots(prev, curr)
    parts = dict(events[0].participants)
    # primary_spouse role lives separately from spouse role
    assert parts["mother"] == 10
    assert parts["father"] == 11
    assert parts["primary_spouse"] == 20
    # children appear as multiple "child" entries
    children = [cid for role, cid in events[0].participants if role == "child"]
    assert children == [30, 31]


# --- travel detection ---


def test_travel_emitted_when_location_changes() -> None:
    prev = _make_snap({1234: _make_char(1234, location_id=100)})
    curr = _make_snap({1234: _make_char(1234, location_id=200)})
    events = diff_snapshots(prev, curr)
    travel = [e for e in events if isinstance(e.event, TravelEvent)]
    assert len(travel) == 1
    assert travel[0].event.p.from_location == 100
    assert travel[0].event.p.to_location == 200


def test_no_travel_for_dead_character() -> None:
    """A character who died has location go to None or stale; we don't
    want a misleading 'travel' event for that."""
    prev = _make_snap({1234: _make_char(1234, location_id=100)})
    curr = _make_snap(
        {1234: _make_char(1234, is_dead=True, death_date="1067.1.15", location_id=200)}
    )
    events = diff_snapshots(prev, curr)
    travel = [e for e in events if isinstance(e.event, TravelEvent)]
    assert travel == []


def test_no_travel_when_locations_equal() -> None:
    prev = _make_snap({1234: _make_char(1234, location_id=100)})
    curr = _make_snap({1234: _make_char(1234, location_id=100)})
    assert diff_snapshots(prev, curr) == []


# --- marriage / divorce ---


def test_marriage_event_for_new_spouse() -> None:
    prev = _make_snap({1234: _make_char(1234, family=FamilySnapshot())})
    curr = _make_snap(
        {1234: _make_char(1234, family=FamilySnapshot(primary_spouse=5678, spouses=(5678,)))}
    )
    events = diff_snapshots(prev, curr)
    marriages = [e for e in events if isinstance(e.event, MarriageEvent)]
    assert len(marriages) == 1
    assert dict(marriages[0].participants).get("spouse") == 5678
    # ck3_chronicler-dnb: spouse_character_id is also surfaced in payload so
    # dedup distinguishes per-spouse marriages on the same in-game date.
    assert marriages[0].event.p.spouse_character_id == 5678


def test_marriage_payload_distinguishes_polygamous_same_date_marriages() -> None:
    """ck3_chronicler-dnb: a tracked character marrying two spouses on the
    same date used to dedup-collide because both marriage events had
    payload={}; only one survived insert. Now that payload carries
    spouse_character_id, the canonical JSON differs and both events
    persist independently."""
    prev = _make_snap({1234: _make_char(1234, family=FamilySnapshot())})
    curr = _make_snap(
        {1234: _make_char(1234, family=FamilySnapshot(primary_spouse=5678, spouses=(5678, 9012)))}
    )
    events = diff_snapshots(prev, curr)
    marriages = [e for e in events if isinstance(e.event, MarriageEvent)]
    assert len(marriages) == 2
    payload_jsons = {e.event.p.model_dump_json() for e in marriages}
    assert len(payload_jsons) == 2  # distinct → no dedup collision


def test_divorce_event_for_new_former_spouse() -> None:
    prev = _make_snap({1234: _make_char(1234, family=FamilySnapshot(spouses=(5678,)))})
    curr = _make_snap({1234: _make_char(1234, family=FamilySnapshot(former_spouses=(5678,)))})
    events = diff_snapshots(prev, curr)
    divorces = [e for e in events if isinstance(e.event, DivorceEvent)]
    assert len(divorces) == 1
    assert dict(divorces[0].participants).get("former_spouse") == 5678
    assert divorces[0].event.p.former_spouse_character_id == 5678


# --- vanilla memory deltas ---


def test_new_vanilla_memory_emitted_as_event() -> None:
    mem = MemorySnapshot(
        memory_id=42,
        memory_type="memory_grand_wedding",
        creation_date="1066.6.1",
        end_date="1099.1.1",
        participants=(("spouse", 5678),),
    )
    prev = _make_snap({1234: _make_char(1234, memories=())})
    curr = _make_snap({1234: _make_char(1234, memories=(mem,))})
    events = diff_snapshots(prev, curr)
    memories_emitted = [e for e in events if isinstance(e.event, VanillaMemoryEvent)]
    assert len(memories_emitted) == 1
    e = memories_emitted[0]
    assert e.event.p.memory_type == "memory_grand_wedding"
    # date prefers the memory's own creation_date over the diff window
    assert e.event.d == "1066.6.1"
    assert e.event.p.participants == {"spouse": 5678}
    assert dict(e.participants) == {"spouse": 5678}


def test_existing_memories_not_re_emitted() -> None:
    mem = MemorySnapshot(
        memory_id=42,
        memory_type="memory_won_battle",
        creation_date="1066.6.1",
        end_date=None,
        participants=(),
    )
    prev = _make_snap({1234: _make_char(1234, memories=(mem,))})
    curr = _make_snap({1234: _make_char(1234, memories=(mem,))})
    events = diff_snapshots(prev, curr)
    assert events == []


# --- ck3_chronicler-7jwu: relative_died deceased-context threading ---


def test_relative_died_threads_deceased_cause_age_and_name() -> None:
    """When a relative_died vanilla_memory is emitted on a tracked
    character, the deceased's cause-of-death, age-at-death, and first
    name are looked up from the snapshot's character map and threaded
    into the payload. Acceptance scenario from 7jwu — Svend's son
    killed by typhus."""
    son = _make_char(
        37000,
        is_dead=True,
        first_name="Harald",
        birth_date="1080.1.1",
        death_date="1102.6.16",
        death_cause="death_disease_typhus",
    )
    rel_died = MemorySnapshot(
        memory_id=99,
        memory_type="relative_died",
        creation_date="1102.6.16",
        end_date=None,
        participants=(("dead_relation", 37000),),
    )
    svend_prev = _make_char(36957)
    svend_curr = _make_char(36957, memories=(rel_died,))
    prev = _make_snap({36957: svend_prev, 37000: _make_char(37000)})
    curr = _make_snap({36957: svend_curr, 37000: son})
    events = diff_snapshots(prev, curr)
    memories_emitted = [e for e in events if isinstance(e.event, VanillaMemoryEvent)]
    rel = next(e for e in memories_emitted if e.event.p.memory_type == "relative_died")
    assert rel.event.c == 36957
    assert rel.event.p.participants == {"dead_relation": 37000}
    assert rel.event.p.deceased_cause == "death_disease_typhus"
    assert rel.event.p.deceased_age == 22
    assert rel.event.p.deceased_first_name == "Harald"


def test_relative_died_no_threading_when_deceased_pruned() -> None:
    """If CK3 has pruned the deceased's row from the snapshot before the
    surviving relative's tick is processed, the lookup misses silently
    and the deceased_* fields stay None — the memory still emits."""
    rel_died = MemorySnapshot(
        memory_id=99,
        memory_type="relative_died",
        creation_date="1102.6.16",
        end_date=None,
        participants=(("dead_relation", 37000),),
    )
    svend_prev = _make_char(36957)
    svend_curr = _make_char(36957, memories=(rel_died,))
    # 37000 not present in either snapshot's characters map.
    prev = _make_snap({36957: svend_prev})
    curr = _make_snap({36957: svend_curr})
    events = diff_snapshots(prev, curr)
    memories_emitted = [e for e in events if isinstance(e.event, VanillaMemoryEvent)]
    assert len(memories_emitted) == 1
    p = memories_emitted[0].event.p
    assert p.memory_type == "relative_died"
    assert p.deceased_cause is None
    assert p.deceased_age is None
    assert p.deceased_first_name is None


def test_non_relative_died_memory_does_not_get_deceased_fields() -> None:
    """Other memory types (grand_wedding, won_battle, etc.) are not
    eligible for deceased threading even if they happen to carry a
    'dead_relation' participant by accident — the gate is on
    memory_type."""
    mem = MemorySnapshot(
        memory_id=42,
        memory_type="memory_grand_wedding",
        creation_date="1066.6.1",
        end_date=None,
        participants=(("spouse", 5678),),
    )
    prev = _make_snap({1234: _make_char(1234)})
    curr = _make_snap({1234: _make_char(1234, memories=(mem,))})
    events = diff_snapshots(prev, curr)
    memories_emitted = [e for e in events if isinstance(e.event, VanillaMemoryEvent)]
    assert len(memories_emitted) == 1
    p = memories_emitted[0].event.p
    assert p.deceased_cause is None
    assert p.deceased_age is None
    assert p.deceased_first_name is None


# --- ck3_chronicler-dn7a: deceased_killer threading ---


def test_relative_died_threads_killer_id_and_name() -> None:
    """When the deceased's CharacterSnapshot carries death_killer (CK3
    set dead_data.killer for a murder/execution/battle death), the
    relative_died memory payload threads the killer's id + first_name
    through. Surviving relative's biography can then name the killer
    rather than write around the murder."""
    eirik = _make_char(
        38000,
        first_name="Eirik",
    )
    son = _make_char(
        37000,
        is_dead=True,
        first_name="Harald",
        birth_date="1080.1.1",
        death_date="1102.6.16",
        death_cause="death_battle",
        death_killer=38000,
    )
    rel_died = MemorySnapshot(
        memory_id=99,
        memory_type="relative_died",
        creation_date="1102.6.16",
        end_date=None,
        participants=(("dead_relation", 37000),),
    )
    svend_prev = _make_char(36957)
    svend_curr = _make_char(36957, memories=(rel_died,))
    prev = _make_snap({36957: svend_prev, 37000: _make_char(37000), 38000: eirik})
    curr = _make_snap({36957: svend_curr, 37000: son, 38000: eirik})
    events = diff_snapshots(prev, curr)
    rel = next(
        e
        for e in events
        if isinstance(e.event, VanillaMemoryEvent) and e.event.p.memory_type == "relative_died"
    )
    assert rel.event.p.deceased_killer_id == 38000
    assert rel.event.p.deceased_killer_first_name == "Eirik"
    # 7jwu fields still populate alongside.
    assert rel.event.p.deceased_cause == "death_battle"
    assert rel.event.p.deceased_first_name == "Harald"


def test_relative_died_killer_fields_none_when_no_killer_recorded() -> None:
    """The common case: a natural-cause death (death_old_age, plague,
    etc.) has no death_killer set. Both killer fields stay None — no
    fabricated 'killed by' surface for biography prose."""
    son = _make_char(
        37000,
        is_dead=True,
        first_name="Harald",
        birth_date="1080.1.1",
        death_date="1102.6.16",
        death_cause="death_disease_typhus",
        # no death_killer
    )
    rel_died = MemorySnapshot(
        memory_id=99,
        memory_type="relative_died",
        creation_date="1102.6.16",
        end_date=None,
        participants=(("dead_relation", 37000),),
    )
    svend_curr = _make_char(36957, memories=(rel_died,))
    prev = _make_snap({36957: _make_char(36957), 37000: _make_char(37000)})
    curr = _make_snap({36957: svend_curr, 37000: son})
    events = diff_snapshots(prev, curr)
    rel = next(
        e
        for e in events
        if isinstance(e.event, VanillaMemoryEvent) and e.event.p.memory_type == "relative_died"
    )
    assert rel.event.p.deceased_killer_id is None
    assert rel.event.p.deceased_killer_first_name is None


def test_relative_died_killer_id_set_but_name_none_when_killer_pruned() -> None:
    """Edge case: deceased.death_killer points to a character CK3 has
    already pruned from the snapshot. Surface the id (still useful for
    cross-DB lookups later) but leave first_name None — no fabricated
    name reaches the biography."""
    son = _make_char(
        37000,
        is_dead=True,
        first_name="Harald",
        birth_date="1080.1.1",
        death_date="1102.6.16",
        death_cause="death_assassination",
        death_killer=38001,  # 38001 not in the snapshot
    )
    rel_died = MemorySnapshot(
        memory_id=99,
        memory_type="relative_died",
        creation_date="1102.6.16",
        end_date=None,
        participants=(("dead_relation", 37000),),
    )
    svend_curr = _make_char(36957, memories=(rel_died,))
    prev = _make_snap({36957: _make_char(36957), 37000: _make_char(37000)})
    curr = _make_snap({36957: svend_curr, 37000: son})
    events = diff_snapshots(prev, curr)
    rel = next(
        e
        for e in events
        if isinstance(e.event, VanillaMemoryEvent) and e.event.p.memory_type == "relative_died"
    )
    assert rel.event.p.deceased_killer_id == 38001
    assert rel.event.p.deceased_killer_first_name is None


# --- ck3_chronicler-jrwe: synthesised DecisionTaken events ---


def test_runestone_decision_emits_decision_taken_event() -> None:
    """ck3_chronicler-jrwe: when the player's decision_cooldowns gains
    raise_stele_decision between adjacent ticks, the diff layer
    synthesises a DecisionTakenEvent (CK3 doesn't emit a vanilla
    memory for the runestone decision — verified against fp1 game
    files). Empirically reproduces the 2026-05-08 Thrugot smoke
    sequence where the user took the decision and chronicler caught
    nothing."""
    prev = _make_snap({1234: _make_char(1234, decisions_taken=())})
    curr = _make_snap(
        {
            1234: _make_char(
                1234,
                decisions_taken=(("raise_stele_decision", "1095.4.1"),),
            )
        },
        date="1085.4.10",
    )
    events = diff_snapshots(prev, curr)
    decisions = [e for e in events if isinstance(e.event, DecisionTakenEvent)]
    assert len(decisions) == 1
    e = decisions[0]
    assert e.event.c == 1234
    assert e.event.d == "1085.4.10"
    assert e.event.p.decision_id == "raise_stele_decision"
    assert e.event.p.cooldown_end_date == "1095.4.1"


def test_decision_taken_not_re_emitted_when_already_present() -> None:
    """The decision was already on cooldown last tick — no new event
    fires. Guards against re-emitting on every save-tail tick for the
    full cooldown duration (10 years for raise_stele)."""
    cooldowns = (("raise_stele_decision", "1095.4.1"),)
    prev = _make_snap({1234: _make_char(1234, decisions_taken=cooldowns)})
    curr = _make_snap({1234: _make_char(1234, decisions_taken=cooldowns)})
    events = diff_snapshots(prev, curr)
    assert not any(isinstance(e.event, DecisionTakenEvent) for e in events)


def test_administrative_decision_outside_allowlist_not_emitted() -> None:
    """Administrative decisions (hold_court, train_for_tournament,
    extract_gold_from_treasury) are not in the allowlist — they would
    drown the event log without contributing to biography prose. The
    diff layer silently skips them."""
    prev = _make_snap({1234: _make_char(1234, decisions_taken=())})
    curr = _make_snap(
        {
            1234: _make_char(
                1234,
                decisions_taken=(("hold_court_decision", "1086.7.2"),),
            )
        }
    )
    events = diff_snapshots(prev, curr)
    assert not any(isinstance(e.event, DecisionTakenEvent) for e in events)


def test_decision_taken_emitted_only_for_allowlisted_when_mixed() -> None:
    """A character takes both an administrative decision and an
    allowlisted one in the same window — only the allowlisted entry
    emits, regardless of order."""
    prev = _make_snap({1234: _make_char(1234, decisions_taken=())})
    curr = _make_snap(
        {
            1234: _make_char(
                1234,
                decisions_taken=(
                    ("hold_court_decision", "1086.7.2"),
                    ("raise_stele_decision", "1095.4.1"),
                ),
            )
        }
    )
    events = diff_snapshots(prev, curr)
    decisions = [e for e in events if isinstance(e.event, DecisionTakenEvent)]
    assert len(decisions) == 1
    assert decisions[0].event.p.decision_id == "raise_stele_decision"


# --- ck3_chronicler-u2g: nickname diff events ---


def test_nickname_event_emitted_when_epithet_gained() -> None:
    """The live-Ælla case: a character with no prior epithet gains
    'the Impaler' after war losses. The transition moment must be an
    event so the consolidator and biographer can locate when the name
    changed, not just see the current state."""
    prev = _make_snap({1234: _make_char(1234, nickname=None)})
    curr = _make_snap({1234: _make_char(1234, nickname="the Impaler")}, date="1067.4.10")
    events = diff_snapshots(prev, curr)
    nicks = [e for e in events if isinstance(e.event, NicknameEvent)]
    assert len(nicks) == 1
    assert nicks[0].event.c == 1234
    assert nicks[0].event.d == "1067.4.10"
    assert nicks[0].event.p.from_nickname is None
    assert nicks[0].event.p.to_nickname == "the Impaler"


def test_nickname_event_emitted_when_epithet_changed() -> None:
    prev = _make_snap({1234: _make_char(1234, nickname="the Bold")})
    curr = _make_snap({1234: _make_char(1234, nickname="the Wise")})
    events = diff_snapshots(prev, curr)
    nicks = [e for e in events if isinstance(e.event, NicknameEvent)]
    assert len(nicks) == 1
    assert nicks[0].event.p.from_nickname == "the Bold"
    assert nicks[0].event.p.to_nickname == "the Wise"


def test_nickname_event_emitted_when_epithet_lost() -> None:
    """CK3 can rescind a nickname under the right conditions (rare but
    possible). The transition is still narratively material."""
    prev = _make_snap({1234: _make_char(1234, nickname="the Cruel")})
    curr = _make_snap({1234: _make_char(1234, nickname=None)})
    events = diff_snapshots(prev, curr)
    nicks = [e for e in events if isinstance(e.event, NicknameEvent)]
    assert len(nicks) == 1
    assert nicks[0].event.p.from_nickname == "the Cruel"
    assert nicks[0].event.p.to_nickname is None


def test_no_nickname_event_when_unchanged() -> None:
    prev = _make_snap({1234: _make_char(1234, nickname="the Impaler")})
    curr = _make_snap({1234: _make_char(1234, nickname="the Impaler")})
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, NicknameEvent)] == []


def test_no_nickname_event_for_dead_character() -> None:
    """Don't emit nickname events on the death-snapshot — the character
    couldn't have changed an epithet between the moment of death and the
    next save. Either the change happened before death (caught in an
    earlier diff) or it's noise from CK3 retroactively writing data."""
    prev = _make_snap({1234: _make_char(1234, nickname=None, is_dead=False)})
    curr = _make_snap(
        {1234: _make_char(1234, nickname="the Posthumous", is_dead=True, death_date="1067.4.10")}
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, NicknameEvent)] == []


def test_multiple_new_memories_each_emit_an_event() -> None:
    m1 = MemorySnapshot(
        memory_id=1,
        memory_type="had_sex",
        creation_date="1066.1.1",
        end_date=None,
        participants=(),
    )
    m2 = MemorySnapshot(
        memory_id=2,
        memory_type="memory_won_battle",
        creation_date="1066.2.1",
        end_date=None,
        participants=(("opponent", 9999),),
    )
    prev = _make_snap({1234: _make_char(1234, memories=())})
    curr = _make_snap({1234: _make_char(1234, memories=(m1, m2))})
    events = diff_snapshots(prev, curr)
    types = {e.event.p.memory_type for e in events if isinstance(e.event, VanillaMemoryEvent)}
    assert types == {"had_sex", "memory_won_battle"}


# --- general behavior ---


def test_playthrough_mismatch_raises() -> None:
    prev = _make_snap({}, playthrough_id="campaign-A")
    curr = _make_snap({}, playthrough_id="campaign-B")
    with pytest.raises(ValueError, match="playthrough_id mismatch"):
        diff_snapshots(prev, curr)


def test_empty_snapshots_yield_no_events() -> None:
    prev = _make_snap({})
    curr = _make_snap({})
    assert diff_snapshots(prev, curr) == []


def test_new_character_in_curr_does_not_emit_event() -> None:
    """Characters that appear only in curr (loaded into scope, born,
    etc.) don't trigger any events at v0.6 MVP — that path is too noisy
    until we have a relevance gate."""
    prev = _make_snap({})
    curr = _make_snap({9999: _make_char(9999)})
    assert diff_snapshots(prev, curr) == []


def test_disappearing_character_does_not_emit_event() -> None:
    """When CK3 prunes a minor dead NPC between saves, it disappears from
    dead_unprunable. That's GC, not an event."""
    prev = _make_snap({9999: _make_char(9999, is_dead=True, death_date="1000.1.1")})
    curr = _make_snap({})
    assert diff_snapshots(prev, curr) == []


def test_tracked_filter_drops_untracked_characters() -> None:
    """tracked_filter limits emission to events whose primary char is in the set."""
    prev = _make_snap(
        {
            1234: _make_char(1234, is_dead=False, location_id=100),
            5678: _make_char(5678, is_dead=False, location_id=200),
            9999: _make_char(9999, is_dead=False, location_id=300),
        }
    )
    curr = _make_snap(
        {
            1234: _make_char(1234, is_dead=True, death_date="1066.10.14", location_id=100),
            5678: _make_char(5678, is_dead=False, location_id=201),  # travel
            9999: _make_char(9999, is_dead=False, location_id=301),  # travel
        }
    )
    # Only 1234 + 5678 are tracked; 9999's travel must be dropped.
    events = diff_snapshots(prev, curr, tracked_filter={1234, 5678})
    chars = {e.event.c for e in events}
    assert chars == {1234, 5678}
    # No filter (default) emits all three.
    events_unfiltered = diff_snapshots(prev, curr)
    chars_unfiltered = {e.event.c for e in events_unfiltered}
    assert chars_unfiltered == {1234, 5678, 9999}


def test_tracked_filter_empty_set_emits_nothing() -> None:
    """An empty tracked_filter means 'no one is tracked' — emit zero events."""
    prev = _make_snap({1234: _make_char(1234, location_id=100)})
    curr = _make_snap({1234: _make_char(1234, location_id=200)})
    assert diff_snapshots(prev, curr, tracked_filter=set()) == []


def test_tracked_filter_none_keeps_legacy_emit_everything_behavior() -> None:
    """None is the explicit 'no filter' signal used by import-save."""
    prev = _make_snap(
        {
            1234: _make_char(1234, location_id=100),
            5678: _make_char(5678, location_id=200),
        }
    )
    curr = _make_snap(
        {
            1234: _make_char(1234, location_id=101),
            5678: _make_char(5678, location_id=201),
        }
    )
    events = diff_snapshots(prev, curr, tracked_filter=None)
    assert {e.event.c for e in events} == {1234, 5678}


def test_events_sorted_deterministically() -> None:
    """Multiple chars + multiple event types should sort by (char_id, type)
    so replay/test output is stable."""
    m1 = MemorySnapshot(
        memory_id=1, memory_type="had_sex", creation_date="d", end_date=None, participants=()
    )
    m2 = MemorySnapshot(
        memory_id=2, memory_type="had_sex", creation_date="d", end_date=None, participants=()
    )
    prev = _make_snap(
        {
            1234: _make_char(1234, location_id=100, memories=()),
            5678: _make_char(5678, location_id=200, memories=()),
        }
    )
    curr = _make_snap(
        {
            1234: _make_char(1234, location_id=101, memories=(m1,)),
            5678: _make_char(5678, location_id=201, memories=(m2,)),
        }
    )
    events = diff_snapshots(prev, curr)
    keys = [(e.event.c, e.event.t) for e in events]
    assert keys == sorted(keys)


# --- ck3_chronicler-cc3: alliance diff events ---


def test_alliance_formed_event_when_new_ally_appears() -> None:
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances={1234: frozenset()},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances={1234: frozenset({27365}), 27365: frozenset({1234})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    af = [e for e in events if e.event.t == "alliance_formed"]
    assert len(af) == 1
    assert af[0].event.c == 1234
    assert af[0].event.p.ally_character_id == 27365
    assert af[0].event.d == "1067.1.1"
    assert dict(af[0].participants).get("ally") == 27365


def test_alliance_broken_event_when_ally_removed() -> None:
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances={1234: frozenset({27365})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances={1234: frozenset()},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    ab = [e for e in events if e.event.t == "alliance_broken"]
    assert len(ab) == 1
    assert ab[0].event.p.ally_character_id == 27365
    assert dict(ab[0].participants).get("former_ally") == 27365


def test_no_alliance_event_when_unchanged() -> None:
    allies = {1234: frozenset({27365})}
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances=allies,
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances=allies,
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    assert [e for e in events if "alliance" in e.event.t] == []


def test_no_alliance_event_for_dead_character() -> None:
    """Death dissolves alliances; we don't want a flood of broken events
    riding alongside the DeathEvent we already emit."""
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234, is_dead=False)},
        alliances={1234: frozenset({27365})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234, is_dead=True, death_date="1066.12.1")},
        alliances={},  # alliances cleared by engine on death
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    assert [e for e in events if "alliance" in e.event.t] == []


def test_alliance_formed_and_broken_in_same_diff() -> None:
    """A character can lose one ally and gain another between snapshots
    (e.g. truce expires + new alliance signed)."""
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances={1234: frozenset({100})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        alliances={1234: frozenset({200})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    formed = [e for e in events if e.event.t == "alliance_formed"]
    broken = [e for e in events if e.event.t == "alliance_broken"]
    assert len(formed) == 1 and formed[0].event.p.ally_character_id == 200
    assert len(broken) == 1 and broken[0].event.p.ally_character_id == 100


# --- ck3_chronicler-o7j: war-state diff events ---


def _make_war(
    war_id: int,
    *,
    attacker_id: int,
    defender_id: int,
    attacker_participants: frozenset[int] | None = None,
    defender_participants: frozenset[int] | None = None,
    cb_type: str = "claimant_faction_war",
    targeted_titles: tuple[int, ...] = (),
    name: str | None = None,
    claimant_id: int | None = None,
) -> WarSnapshot:
    return WarSnapshot(
        war_id=war_id,
        name=name,
        start_date="1066.10.1",
        casus_belli_type=cb_type,
        targeted_titles=targeted_titles,
        primary_attacker_id=attacker_id,
        primary_defender_id=defender_id,
        claimant_id=claimant_id,
        attacker_participants=attacker_participants
        if attacker_participants is not None
        else frozenset({attacker_id}),
        defender_participants=defender_participants
        if defender_participants is not None
        else frozenset({defender_id}),
    )


def test_war_declared_event_when_principal_appears_in_new_war() -> None:
    """The cb.attacker of a war that wasn't in the prev tick gets a
    war_declared event with side='attacker'. Same for cb.defender on
    their side."""
    war = _make_war(42, attacker_id=1234, defender_id=999, cb_type="holy_war")
    prev = _make_snap({1234: _make_char(1234)})
    curr = SaveSnapshot(
        playthrough_id=prev.playthrough_id,
        ck3_version=prev.ck3_version,
        bookmark_date=prev.bookmark_date,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    declared = [e for e in events if e.event.t == "war_declared"]
    assert len(declared) == 1
    e = declared[0]
    assert e.event.c == 1234
    assert e.event.p.war_id == 42
    assert e.event.p.casus_belli_type == "holy_war"
    assert e.event.p.side == "attacker"
    assert e.event.p.primary_defender_id == 999
    # Defender shows up as a participant role on the event
    assert (
        ("primary_defender", 999) in e.event.participants
        if hasattr(e.event, "participants")
        else True
    )
    assert ("primary_attacker", 1234) in dict(e.participants).items() or 1234 in (
        v for _, v in e.participants
    )


def test_war_joined_event_for_called_in_ally() -> None:
    """A non-principal participant in a freshly-declared war gets
    war_joined, not war_declared. Same for a participant who appears
    in a war that already existed in the prev tick."""
    war = _make_war(
        42,
        attacker_id=999,
        defender_id=888,
        attacker_participants=frozenset({999, 1234}),  # 1234 called in
    )
    prev = _make_snap({1234: _make_char(1234)})
    curr = SaveSnapshot(
        playthrough_id=prev.playthrough_id,
        ck3_version=prev.ck3_version,
        bookmark_date=prev.bookmark_date,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war},
        character_to_wars={
            1234: frozenset({42}),
            999: frozenset({42}),
            888: frozenset({42}),
        },
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    types = [e.event.t for e in events]
    # 1234 isn't the principal — should be joined, not declared
    assert "war_joined" in types
    assert "war_declared" not in types
    joined = next(e for e in events if e.event.t == "war_joined")
    assert joined.event.p.side == "attacker"
    assert joined.event.p.primary_attacker_id == 999


def test_war_name_sanitized_in_event_payload() -> None:
    """ck3_chronicler-90tv: WarSnapshot.name carries CK3's tooltip/link
    markup verbatim. The diff layer applies strip_loca_markup at
    WarSidePayload construction so emitted events store the visible
    war name. Live-form Crusade name from the 2026-05-08 Thrugot
    smoke."""
    raw_name = (
        "Crusade for \x15ONCLICK:TITLE,8755 "
        "\x15TOOLTIP:LANDED_TITLE,8755 \x15L; "
        "Kingdom of Jerusalem\x15!\x15!\x15!"
    )
    war = _make_war(
        42,
        attacker_id=1234,
        defender_id=999,
        cb_type="undirected_great_holy_war",
        name=raw_name,
    )
    prev = _make_snap({1234: _make_char(1234)})
    curr = SaveSnapshot(
        playthrough_id=prev.playthrough_id,
        ck3_version=prev.ck3_version,
        bookmark_date=prev.bookmark_date,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    declared = [e for e in events if e.event.t == "war_declared"]
    assert len(declared) == 1
    assert declared[0].event.p.war_name == "Crusade for Kingdom of Jerusalem"


def test_war_concluded_when_war_id_disappears() -> None:
    """CK3 deletes concluded wars from active_wars. The diff infers
    conclusion from war_id absent in curr.wars but present in prev."""
    war = _make_war(42, attacker_id=1234, defender_id=999)
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42})},
    )
    curr = _make_snap({1234: _make_char(1234)}, date="1067.1.1")
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    concluded = [e for e in events if e.event.t == "war_concluded"]
    assert len(concluded) == 1
    assert concluded[0].event.p.war_id == 42
    # Side resolved from prev tick's snapshot since the war is gone in curr
    assert concluded[0].event.p.side == "attacker"


def test_war_left_when_war_still_active_but_character_drops_out() -> None:
    """Separate peace / side switch: war is still in active_wars but
    the character is no longer in its participant set."""
    war_prev = _make_war(
        42,
        attacker_id=999,
        defender_id=888,
        attacker_participants=frozenset({999, 1234}),
    )
    # Same war, 1234 has dropped out
    war_curr = _make_war(
        42,
        attacker_id=999,
        defender_id=888,
        attacker_participants=frozenset({999}),
    )
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war_prev},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42}), 888: frozenset({42})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war_curr},
        character_to_wars={999: frozenset({42}), 888: frozenset({42})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    left = [e for e in events if e.event.t == "war_left"]
    assert len(left) == 1
    # No war_concluded / war_declared etc. — the war's still active
    assert "war_concluded" not in [e.event.t for e in events]


def test_no_war_event_when_unchanged() -> None:
    war = _make_war(42, attacker_id=1234, defender_id=999)
    snap = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42})},
    )
    snap_later = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        wars={42: war},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42})},
    )
    events = diff_snapshots(snap, snap_later, tracked_filter={1234})
    war_events = [e for e in events if e.event.t.startswith("war_")]
    assert war_events == []


def test_no_war_event_for_dead_character() -> None:
    """Death cleans up war participation; we'd otherwise emit a
    war_left flood riding the DeathEvent."""
    war = _make_war(42, attacker_id=1234, defender_id=999)
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234, is_dead=False)},
        wars={42: war},
        character_to_wars={1234: frozenset({42}), 999: frozenset({42})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234, is_dead=True, death_date="1066.12.1")},
        wars={},  # war ended on death-trigger
        character_to_wars={},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    # We expect death + travel-aside, but no war_concluded for 1234
    types = {e.event.t for e in events}
    assert "death" in types
    assert "war_concluded" not in types
    assert "war_left" not in types


# --- ck3_chronicler-d83: artifact + dynasty-legacy diff events ---


def test_artifact_acquired_event_when_new_owner_appears() -> None:
    art = ArtifactSnapshot(
        artifact_id=42,
        name="Crown of Light",
        type="regalia",
        rarity="illustrious",
        owner_id=1234,
    )
    prev = _make_snap({1234: _make_char(1234)})
    curr = SaveSnapshot(
        playthrough_id=prev.playthrough_id,
        ck3_version=prev.ck3_version,
        bookmark_date=prev.bookmark_date,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        artifacts={42: art},
        character_to_artifacts={1234: frozenset({42})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    acquired = [e for e in events if e.event.t == "artifact_acquired"]
    assert len(acquired) == 1
    assert acquired[0].event.p.artifact_id == 42
    assert acquired[0].event.p.name == "Crown of Light"
    assert acquired[0].event.p.rarity == "illustrious"


def test_artifact_lost_event_when_owner_changes() -> None:
    art = ArtifactSnapshot(
        artifact_id=42,
        name="Crown of Light",
        type="regalia",
        rarity="illustrious",
        owner_id=999,  # changed owner
    )
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        artifacts={42: ArtifactSnapshot(42, "Crown of Light", "regalia", "illustrious", 1234)},
        character_to_artifacts={1234: frozenset({42})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234)},
        artifacts={42: art},
        character_to_artifacts={999: frozenset({42})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    lost = [e for e in events if e.event.t == "artifact_lost"]
    assert len(lost) == 1
    assert lost[0].event.p.artifact_id == 42
    assert lost[0].event.p.name == "Crown of Light"


def test_no_artifact_event_for_dead_character() -> None:
    """Death triggers the engine's inheritance pass; we don't want a
    flood of artifact_lost events riding the DeathEvent."""
    art = ArtifactSnapshot(42, "Crown", None, None, 1234)
    prev = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1066.10.1",
        player_character_id=1234,
        characters={1234: _make_char(1234, is_dead=False)},
        artifacts={42: art},
        character_to_artifacts={1234: frozenset({42})},
    )
    curr = SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _make_char(1234, is_dead=True, death_date="1066.12.1")},
        artifacts={42: ArtifactSnapshot(42, "Crown", None, None, 999)},  # inherited
        character_to_artifacts={999: frozenset({42})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    types = {e.event.t for e in events}
    assert "death" in types
    assert "artifact_lost" not in types


def test_dynasty_legacy_unlocked_event_for_dynasty_member() -> None:
    """Tracked character whose dynasty advances a perk between
    snapshots gets a dynasty_legacy_unlocked event resolved through
    house_to_dynasty (their dynasty_house_id → dynasty_id)."""
    char = make_char(1234, first_name="Erik", birth_date="1031.1.1", dynasty_house_id=10566)
    base_kwargs = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=1234,
        characters={1234: char},
        house_to_dynasty={10566: 1504},  # house 10566 belongs to dynasty 1504
        dynasties_lookup={1504: "Munso"},
    )
    prev = SaveSnapshot(
        current_date="1066.10.1",
        dynasty_perks={1504: frozenset({"blood_legacy_1"})},
        **base_kwargs,
    )
    curr = SaveSnapshot(
        current_date="1067.1.1",
        dynasty_perks={
            1504: frozenset({"blood_legacy_1", "blood_legacy_2", "fp1_pillage_legacy_1"})
        },
        **base_kwargs,
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    unlocked = [e for e in events if e.event.t == "dynasty_legacy_unlocked"]
    assert len(unlocked) == 2
    keys = {e.event.p.legacy_key for e in unlocked}
    assert keys == {"blood_legacy_2", "fp1_pillage_legacy_1"}
    assert all(e.event.p.dynasty_id == 1504 for e in unlocked)
    assert all(e.event.p.dynasty_name == "Munso" for e in unlocked)
    assert all(e.event.c == 1234 for e in unlocked)


def test_no_dynasty_legacy_event_for_houseless_character() -> None:
    """A character with no dynasty_house_id can't be tied to a
    dynasty's perk advancement — the diff is silent for them rather
    than guessing or attributing the legacy."""
    char = _make_char(1234)  # no dynasty_house_id
    prev_kwargs = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=1234,
        characters={1234: char},
    )
    prev = SaveSnapshot(
        current_date="1066.10.1",
        dynasty_perks={1504: frozenset({"blood_legacy_1"})},
        **prev_kwargs,
    )
    curr = SaveSnapshot(
        current_date="1067.1.1",
        dynasty_perks={1504: frozenset({"blood_legacy_1", "blood_legacy_2"})},
        **prev_kwargs,
    )
    events = diff_snapshots(prev, curr, tracked_filter={1234})
    assert [e for e in events if e.event.t == "dynasty_legacy_unlocked"] == []


# --- ck3_chronicler-n0s4: character modifiers ---


def _n0s4_char(cid: int, *, modifiers: tuple[str, ...] = ()) -> CharacterSnapshot:
    return make_char(cid, modifiers=modifiers)


def test_modifier_acquired_event_when_new_modifier_appears() -> None:
    """Live evidence: player chose the deity Ullr and received the
    'devoted_to_ullr' modifier. The diff layer was silent on this
    significant character moment; emit modifier_acquired with the
    engine key so memories and biographies can surface deity
    devotions."""
    prev = _make_snap({1: _n0s4_char(1, modifiers=())})
    curr = _make_snap({1: _n0s4_char(1, modifiers=("devoted_to_ullr",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    acquired = [e for e in events if e.event.t == "modifier_acquired"]
    assert len(acquired) == 1
    e = acquired[0].event
    assert e.c == 1
    assert e.p.modifier_key == "devoted_to_ullr"


def test_modifier_event_silent_on_no_change() -> None:
    """Existing modifier still present — no event."""
    prev = _make_snap({1: _n0s4_char(1, modifiers=("devoted_to_ullr",))})
    curr = _make_snap({1: _n0s4_char(1, modifiers=("devoted_to_ullr",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "modifier_acquired"] == []


def test_no_modifier_event_on_release() -> None:
    """Modifier expired or removed — no event. Acquired-only design.
    Some modifiers are temporary (event-granted), tracking expiry
    would flood biographies with low-signal noise."""
    prev = _make_snap({1: _n0s4_char(1, modifiers=("mourning_son",))})
    curr = _make_snap({1: _n0s4_char(1, modifiers=())})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "modifier_acquired"] == []


def test_modifier_acquired_event_for_multiple_new_modifiers() -> None:
    """Two new modifiers between snapshots — one event per key,
    sorted for deterministic order (mirrors trait_gained shape)."""
    prev = _make_snap({1: _n0s4_char(1, modifiers=("devoted_to_ullr",))})
    curr = _make_snap({1: _n0s4_char(1, modifiers=("devoted_to_ullr", "mourning_son", "event_a"))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    keys = sorted(e.event.p.modifier_key for e in events if e.event.t == "modifier_acquired")
    assert keys == ["event_a", "mourning_son"]


def test_no_modifier_event_for_dead_character() -> None:
    """Death tick may reshuffle modifiers as the engine cleans up
    state — defensive skip mirroring _diff_traits."""
    dead = make_char(
        1,
        first_name="Erik",
        is_dead=True,
        death_date="921.4.1",
        modifiers=("devoted_to_ullr",),
    )
    prev = _make_snap({1: _n0s4_char(1, modifiers=())})
    curr = _make_snap({1: dead})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "modifier_acquired"] == []


# --- ck3_chronicler-rgay: lifestyle perk acquired events ---


def _rgay_char(cid: int, *, perks: tuple[str, ...] = ()) -> CharacterSnapshot:
    return make_char(cid, perks=perks)


def test_perk_acquired_event_when_new_perk_appears() -> None:
    """Live evidence: Genji (Roads to Power adventurer) started with
    bellum_justum_perk + parthian_tactics_perk from ruler-designer
    points. A character picking up their first perk in a new lifestyle
    tree is a defining narrative beat that today's diff layer is
    silent on."""
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: _rgay_char(1, perks=("bellum_justum_perk",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    acquired = [e for e in events if e.event.t == "perk_acquired"]
    assert len(acquired) == 1
    e = acquired[0].event
    assert e.c == 1
    assert e.p.perk_key == "bellum_justum_perk"


def test_perk_event_silent_on_no_change() -> None:
    """Existing perk still present — no event."""
    prev = _make_snap({1: _rgay_char(1, perks=("schemer_perk",))})
    curr = _make_snap({1: _rgay_char(1, perks=("schemer_perk",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "perk_acquired"] == []


def test_no_perk_event_on_release() -> None:
    """Perk reset / tree-reroll removes perks — no event. Acquired-only
    design: per-perk loss is structural noise; the meaningful signal
    is the new commitment, not the unwind."""
    prev = _make_snap({1: _rgay_char(1, perks=("schemer_perk", "intrigue_perk"))})
    curr = _make_snap({1: _rgay_char(1, perks=("schemer_perk",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "perk_acquired"] == []


def test_perk_acquired_event_for_multiple_new_perks() -> None:
    """Two new perks between snapshots — one event per key, sorted
    deterministically (mirrors trait_gained / modifier_acquired)."""
    prev = _make_snap({1: _rgay_char(1, perks=("bellum_justum_perk",))})
    curr = _make_snap(
        {
            1: _rgay_char(
                1,
                perks=(
                    "bellum_justum_perk",
                    "parthian_tactics_perk",
                    "schemer_perk",
                ),
            )
        }
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    keys = sorted(e.event.p.perk_key for e in events if e.event.t == "perk_acquired")
    assert keys == ["parthian_tactics_perk", "schemer_perk"]


def test_no_perk_event_for_dead_character() -> None:
    """Death tick may zero perk state as the engine cleans up —
    defensive skip mirroring _diff_modifiers."""
    dead = make_char(
        1,
        first_name="Erik",
        is_dead=True,
        death_date="921.4.1",
        perks=("schemer_perk",),
    )
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: dead})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "perk_acquired"] == []


# --- ck3_chronicler-9p2g: lifestyle committed events ---


def test_lifestyle_committed_fires_on_first_perk_in_lifestyle() -> None:
    """Spec: a character picking up their first perk in a previously-empty
    lifestyle fires exactly one LifestyleCommittedEvent for that lifestyle.

    Anchor: Genji (rgay live evidence) starts with bellum_justum_perk
    (martial / chivalry tree). The chronicler today sees the perk_acquired
    but not the higher-order 'committed to the martial lifestyle' beat."""
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: _rgay_char(1, perks=("bellum_justum_perk",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    committed = [e for e in events if e.event.t == "lifestyle_committed"]
    assert len(committed) == 1
    e = committed[0].event
    assert e.c == 1
    assert e.p.lifestyle_key == "martial_lifestyle"
    assert e.p.first_perk_key == "bellum_justum_perk"


def test_lifestyle_committed_silent_when_already_committed() -> None:
    """A character who already has a perk in the martial lifestyle does
    NOT fire a new commit event when picking up a second martial perk —
    the commitment beat is one-shot per lifestyle."""
    prev = _make_snap({1: _rgay_char(1, perks=("bellum_justum_perk",))})
    curr = _make_snap({1: _rgay_char(1, perks=("bellum_justum_perk", "parthian_tactics_perk"))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "lifestyle_committed"] == []


def test_lifestyle_committed_fires_per_distinct_new_lifestyle() -> None:
    """If a single tick reveals perks in two different lifestyles, both
    fire — same one-event-per-lifestyle invariant, sorted by lifestyle_key
    for deterministic ordering."""
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: _rgay_char(1, perks=("bellum_justum_perk", "schemer_perk"))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    committed = [e for e in events if e.event.t == "lifestyle_committed"]
    keys = sorted(e.event.p.lifestyle_key for e in committed)
    assert keys == ["intrigue_lifestyle", "martial_lifestyle"]


def test_lifestyle_committed_silent_for_unmapped_perk() -> None:
    """An unknown perk (not in the static map) doesn't fire a commit
    event — graceful skip, the map grows organically over time."""
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: _rgay_char(1, perks=("some_uncharted_perk",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "lifestyle_committed"] == []


def test_lifestyle_committed_silent_on_no_change() -> None:
    prev = _make_snap({1: _rgay_char(1, perks=("schemer_perk",))})
    curr = _make_snap({1: _rgay_char(1, perks=("schemer_perk",))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "lifestyle_committed"] == []


def test_lifestyle_committed_silent_on_dead_character() -> None:
    """Death-tick may zero alive_data.perk as the engine cleans up;
    defensive skip mirrors _diff_perks."""
    dead = make_char(
        1,
        first_name="Erik",
        is_dead=True,
        death_date="921.4.1",
        perks=("bellum_justum_perk",),
    )
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: dead})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if e.event.t == "lifestyle_committed"] == []


def test_lifestyle_committed_uses_first_known_perk_as_first_perk_key() -> None:
    """When the same tick reveals two new perks in the same lifestyle,
    first_perk_key is the lex-smallest known perk (sorted) for
    deterministic output."""
    prev = _make_snap({1: _rgay_char(1, perks=())})
    curr = _make_snap({1: _rgay_char(1, perks=("parthian_tactics_perk", "bellum_justum_perk"))})
    events = diff_snapshots(prev, curr, tracked_filter={1})
    committed = [e for e in events if e.event.t == "lifestyle_committed"]
    assert len(committed) == 1
    assert committed[0].event.p.lifestyle_key == "martial_lifestyle"
    # bellum_justum_perk < parthian_tactics_perk lex order
    assert committed[0].event.p.first_perk_key == "bellum_justum_perk"


# --- ck3_chronicler-gu7j: concubine taken events ---


def _gu7j_char(cid: int, *, concubines: tuple[int, ...] = ()) -> CharacterSnapshot:
    return make_char(cid, family=FamilySnapshot(concubines=concubines))


def test_concubine_taken_event_when_new_concubine_appears() -> None:
    """A tracked character gains a concubine — emit concubine_taken
    keyed on the new id. Live evidence: Örvar took Leofwynn (id 42865)
    after raiding Wessex; today the chronicle has zero record of the
    moment."""
    prev = _make_snap({60494: _gu7j_char(60494, concubines=())})
    curr = _make_snap({60494: _gu7j_char(60494, concubines=(42865,))})
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    taken = [e for e in events if e.event.t == "concubine_taken"]
    assert len(taken) == 1
    e = taken[0].event
    assert e.c == 60494
    assert e.p.concubine_id == 42865


def test_concubine_event_silent_on_no_change() -> None:
    """Existing concubine still present — no event."""
    prev = _make_snap({60494: _gu7j_char(60494, concubines=(42865,))})
    curr = _make_snap({60494: _gu7j_char(60494, concubines=(42865,))})
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "concubine_taken"] == []


def test_no_concubine_event_on_release() -> None:
    """Released concubine — taken-only design choice. Death events
    cover the death case; manumission/repatriation are out of scope
    for v1."""
    prev = _make_snap({60494: _gu7j_char(60494, concubines=(42865,))})
    curr = _make_snap({60494: _gu7j_char(60494, concubines=())})
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "concubine_taken"] == []


def test_concubine_taken_event_for_multiple_new_concubines() -> None:
    """Two new concubines on the same tick produce two events, one
    per new id (mirrors d83 multi-perk emission)."""
    prev = _make_snap({60494: _gu7j_char(60494, concubines=(42865,))})
    curr = _make_snap({60494: _gu7j_char(60494, concubines=(42865, 50001, 50002))})
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    taken = sorted(
        (e.event.p.concubine_id for e in events if e.event.t == "concubine_taken"),
    )
    assert taken == [50001, 50002]


def test_no_concubine_event_for_dead_character() -> None:
    """A dead character can't take a new concubine — defensive guard
    against engine pruning artefacts where a death tick re-shuffles
    family relations as the household disbands."""
    dead = make_char(
        60494,
        first_name="Erik",
        is_dead=True,
        death_date="921.4.1",
        family=FamilySnapshot(concubines=(42865,)),
    )
    prev = _make_snap({60494: _gu7j_char(60494, concubines=())})
    curr = _make_snap({60494: dead})
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "concubine_taken"] == []


# --- ck3_chronicler-ei8t: dynasty splendor tier crossings ---


def _ei8t_char(cid: int, house_id: int | None) -> CharacterSnapshot:
    return make_char(cid, dynasty_house_id=house_id)


def test_splendor_increased_event_on_tier_crossing() -> None:
    """Tracked character whose dynasty crosses a splendor tier between
    snapshots gets a splendor_increased event with old/new tier
    integers and the renown at crossing."""
    char = _ei8t_char(60494, house_id=11283)
    base = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=60494,
        characters={60494: char},
        house_to_dynasty={11283: 11283},
        dynasties_lookup={11283: "Sleggja"},
        dynasty_heads={11283: 60494},
    )
    prev = SaveSnapshot(
        current_date="921.4.1",
        dynasty_renown={11283: 980.0},  # tier 0
        **base,
    )
    curr = SaveSnapshot(
        current_date="921.5.1",
        dynasty_renown={11283: 1010.485},  # tier 1 — live Sleggja crossing
        **base,
    )
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    splendor = [e for e in events if e.event.t == "splendor_increased"]
    assert len(splendor) == 1
    e = splendor[0].event
    assert e.c == 60494
    assert e.p.dynasty_id == 11283
    assert e.p.dynasty_name == "Sleggja"
    assert e.p.dynasty_head_id == 60494
    assert e.p.old_tier == 0
    assert e.p.new_tier == 1
    assert e.p.total_renown_at_change == 1010.485


def test_no_splendor_event_within_same_tier() -> None:
    """Renown grows but stays within the same tier — no event."""
    char = _ei8t_char(60494, house_id=11283)
    base = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=60494,
        characters={60494: char},
        house_to_dynasty={11283: 11283},
        dynasties_lookup={11283: "Sleggja"},
        dynasty_heads={11283: 60494},
    )
    prev = SaveSnapshot(
        current_date="921.4.1",
        dynasty_renown={11283: 1500.0},  # tier 1
        **base,
    )
    curr = SaveSnapshot(
        current_date="921.5.1",
        dynasty_renown={11283: 4500.0},  # still tier 1
        **base,
    )
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "splendor_increased"] == []


def test_no_splendor_event_on_tier_drop() -> None:
    """Splendor can fall when accumulated drops (rare; refund paths).
    Increases-only — drops produce no event."""
    char = _ei8t_char(60494, house_id=11283)
    base = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=60494,
        characters={60494: char},
        house_to_dynasty={11283: 11283},
        dynasties_lookup={11283: "Sleggja"},
        dynasty_heads={11283: 60494},
    )
    prev = SaveSnapshot(
        current_date="921.4.1",
        dynasty_renown={11283: 5500.0},  # tier 2
        **base,
    )
    curr = SaveSnapshot(
        current_date="921.5.1",
        dynasty_renown={11283: 4900.0},  # back to tier 1
        **base,
    )
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "splendor_increased"] == []


def test_no_splendor_event_for_dead_character() -> None:
    """A dead character can't be the subject of their dynasty's
    state transitions — mirrors _diff_dynasty_legacies."""
    char = make_char(
        60494,
        first_name="Örvar",
        is_dead=True,
        death_date="921.4.1",
        dynasty_house_id=11283,
    )
    base = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=60494,
        characters={60494: char},
        house_to_dynasty={11283: 11283},
        dynasties_lookup={11283: "Sleggja"},
        dynasty_heads={11283: 60494},
    )
    prev = SaveSnapshot(
        current_date="921.4.1",
        dynasty_renown={11283: 980.0},
        **base,
    )
    curr = SaveSnapshot(
        current_date="921.5.1",
        dynasty_renown={11283: 1010.0},
        **base,
    )
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "splendor_increased"] == []


def test_no_splendor_event_for_houseless_character() -> None:
    """A character without dynasty_house_id can't be resolved to a
    dynasty — no event, no guess."""
    char = _ei8t_char(60494, house_id=None)
    base = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=60494,
        characters={60494: char},
        dynasties_lookup={11283: "Sleggja"},
    )
    prev = SaveSnapshot(
        current_date="921.4.1",
        dynasty_renown={11283: 980.0},
        **base,
    )
    curr = SaveSnapshot(
        current_date="921.5.1",
        dynasty_renown={11283: 1010.0},
        **base,
    )
    events = diff_snapshots(prev, curr, tracked_filter={60494})
    assert [e for e in events if e.event.t == "splendor_increased"] == []


def test_splendor_event_emitted_per_tracked_dynasty_member() -> None:
    """Two tracked siblings in the same dynasty both get a
    splendor_increased event when the dynasty crosses a tier — same
    coverage shape as dynasty_legacy_unlocked."""
    erik = _ei8t_char(60494, house_id=11283)
    saga = _ei8t_char(15052, house_id=11283)
    base = dict(
        playthrough_id="test-uuid",
        ck3_version="1.19",
        bookmark_date=None,
        player_character_id=60494,
        characters={60494: erik, 15052: saga},
        house_to_dynasty={11283: 11283},
        dynasties_lookup={11283: "Sleggja"},
        dynasty_heads={11283: 60494},
    )
    prev = SaveSnapshot(
        current_date="921.4.1",
        dynasty_renown={11283: 980.0},
        **base,
    )
    curr = SaveSnapshot(
        current_date="921.5.1",
        dynasty_renown={11283: 1010.485},
        **base,
    )
    events = diff_snapshots(prev, curr, tracked_filter={60494, 15052})
    splendor = [e for e in events if e.event.t == "splendor_increased"]
    assert {e.event.c for e in splendor} == {60494, 15052}
    assert all(e.event.p.new_tier == 1 for e in splendor)


# --- ck3_chronicler-mcu: adventurer-mode transition events ---


def _make_char_with_gov(cid: int, government: str | None, **kwargs) -> CharacterSnapshot:
    return replace(_make_char(cid, **kwargs), government=government)


def test_adventurer_started_event_when_landed_to_landless() -> None:
    """A character loses all titles and becomes a Roads to Power
    landless adventurer — the government transition is the event."""
    prev = _make_snap({1: _make_char_with_gov(1, "feudal_government")})
    curr = _make_snap(
        {1: _make_char_with_gov(1, "landless_adventurer_government")},
        date="1067.4.10",
    )
    events = diff_snapshots(prev, curr)
    started = [e for e in events if e.event.t == "adventurer_started"]
    assert len(started) == 1
    assert started[0].event.c == 1
    assert started[0].event.d == "1067.4.10"


def test_adventurer_ended_event_when_landless_to_landed() -> None:
    """Adventurer settles down — gets land via gift, conquest, or marriage."""
    prev = _make_snap({1: _make_char_with_gov(1, "landless_adventurer_government")})
    curr = _make_snap(
        {1: _make_char_with_gov(1, "feudal_government")},
        date="1068.6.1",
    )
    events = diff_snapshots(prev, curr)
    ended = [e for e in events if e.event.t == "adventurer_ended"]
    assert len(ended) == 1
    assert ended[0].event.c == 1


def test_no_adventurer_event_for_unrelated_government_change() -> None:
    """Tribal-to-feudal reform is a real transition but it's not an
    adventurer-mode event; the adventurer_* events only fire on
    into/out-of landless. (Tribal→feudal fires a government_changed
    event instead — see test_government_changed_event_for_tribal_to_feudal.)"""
    prev = _make_snap({1: _make_char_with_gov(1, "tribal_government")})
    curr = _make_snap({1: _make_char_with_gov(1, "feudal_government")})
    events = diff_snapshots(prev, curr)
    adv = [e for e in events if e.event.t.startswith("adventurer_")]
    assert adv == []


def test_government_changed_event_for_tribal_to_feudal() -> None:
    """Tribal→feudal reform fires a government_changed event with
    the from/to government IDs preserved on the payload, so biographers
    can render the regime shift specifically."""
    prev = _make_snap({1: _make_char_with_gov(1, "tribal_government")})
    curr = _make_snap({1: _make_char_with_gov(1, "feudal_government")})
    events = diff_snapshots(prev, curr)
    changes = [e for e in events if e.event.t == "government_changed"]
    assert len(changes) == 1
    assert changes[0].event.c == 1
    assert changes[0].event.p.previous_government == "tribal_government"
    assert changes[0].event.p.new_government == "feudal_government"


def test_no_government_changed_event_on_adventurer_transition() -> None:
    """Into/out-of adventurer mode already has dedicated events
    (adventurer_started / adventurer_ended). Do NOT also fire
    government_changed for those — would double-count the narrative
    beat."""
    prev = _make_snap({1: _make_char_with_gov(1, "feudal_government")})
    curr = _make_snap(
        {1: _make_char_with_gov(1, "landless_adventurer_government")},
        date="1067.5.1",
    )
    events = diff_snapshots(prev, curr)
    changes = [e for e in events if e.event.t == "government_changed"]
    assert changes == []


def test_no_government_changed_event_on_death() -> None:
    """Death may clear landed_data and we'd see government go to None;
    that's not a regime shift."""
    prev = _make_snap({1: _make_char_with_gov(1, "tribal_government")})
    curr = _make_snap({1: _make_char_with_gov(1, None, is_dead=True, death_date="1067.4.10")})
    events = diff_snapshots(prev, curr)
    changes = [e for e in events if e.event.t == "government_changed"]
    assert changes == []


def test_no_adventurer_event_when_government_unchanged() -> None:
    g = "landless_adventurer_government"
    prev = _make_snap({1: _make_char_with_gov(1, g)})
    curr = _make_snap({1: _make_char_with_gov(1, g)})
    events = diff_snapshots(prev, curr)
    adv = [e for e in events if e.event.t.startswith("adventurer_")]
    assert adv == []


def test_no_adventurer_event_for_dead_character() -> None:
    """Death may clear landed_data and we'd see government go to None;
    that's not an 'adventurer ended' moment."""
    prev = _make_snap({1: _make_char_with_gov(1, "landless_adventurer_government")})
    curr = _make_snap({1: _make_char_with_gov(1, None, is_dead=True, death_date="1067.4.10")})
    events = diff_snapshots(prev, curr)
    adv = [e for e in events if e.event.t.startswith("adventurer_")]
    assert adv == []


# --- ck3_chronicler-nzr: trait diff events ---


def _make_char_with_traits(cid: int, traits: tuple[int, ...], **kwargs) -> CharacterSnapshot:
    return replace(_make_char(cid, **kwargs), traits=traits)


def _snap_with_traits_lookup(
    chars: dict, *, traits_lookup: tuple[str, ...] = (), date: str = "1067.2.1"
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        traits_lookup=traits_lookup,
    )


# Match the live save's index ordering (verified live: 125 → 'faltering_heart')
_NAMED_TRAITS = {54: "diligent", 67: "ambitious", 81: "vengeful", 125: "faltering_heart"}
_TEST_TRAITS_LOOKUP: tuple[str, ...] = tuple(_NAMED_TRAITS.get(i, f"trait_{i}") for i in range(150))


def test_trait_gained_event_when_new_trait_appears() -> None:
    """The live-Toirrdelbach case: gained 'faltering_heart' after stress.
    The user explicitly asked to surface this kind of named trait."""
    prev = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (54, 67))}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    curr = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (54, 67, 125))},
        traits_lookup=_TEST_TRAITS_LOOKUP,
        date="1067.4.10",
    )
    events = diff_snapshots(prev, curr)
    gained = [e for e in events if e.event.t == "trait_gained"]
    assert len(gained) == 1
    assert gained[0].event.c == 1
    assert gained[0].event.d == "1067.4.10"
    assert gained[0].event.p.trait_id == 125
    assert gained[0].event.p.trait_name == "faltering_heart"


def test_trait_lost_event_when_trait_removed() -> None:
    prev = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (54, 81))}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    curr = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (54,))}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    events = diff_snapshots(prev, curr)
    lost = [e for e in events if e.event.t == "trait_lost"]
    assert len(lost) == 1
    assert lost[0].event.p.trait_id == 81
    assert lost[0].event.p.trait_name == "vengeful"


def test_no_trait_event_when_unchanged() -> None:
    same = (54, 67, 81)
    prev = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, same)}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    curr = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, same)}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if "trait" in e.event.t] == []


def test_no_trait_event_for_dead_character() -> None:
    """Dead chars may have traits cleared / reshuffled; we don't want
    a flood riding alongside the DeathEvent."""
    prev = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (54, 67))}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    curr = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (), is_dead=True, death_date="1067.4.10")},
        traits_lookup=_TEST_TRAITS_LOOKUP,
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if "trait" in e.event.t] == []


def test_trait_event_with_unknown_id_emits_none_name() -> None:
    """Defensive: out-of-range trait IDs don't crash, just emit
    trait_name=None so downstream prompts can fall back to 'trait #999'."""
    prev = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, ())}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    curr = _snap_with_traits_lookup(
        {1: _make_char_with_traits(1, (9999,))}, traits_lookup=_TEST_TRAITS_LOOKUP
    )
    events = diff_snapshots(prev, curr)
    gained = [e for e in events if e.event.t == "trait_gained"]
    assert len(gained) == 1
    assert gained[0].event.p.trait_id == 9999
    assert gained[0].event.p.trait_name is None


# --- ck3_chronicler-667: state-change diff events ---


def _make_char_with_state(
    cid: int,
    *,
    dynasty_house_id: int | None = None,
    culture_id: int | None = None,
    faith_id: int | None = None,
    is_dead: bool = False,
    death_date: str | None = None,
) -> CharacterSnapshot:
    return replace(
        _make_char(cid, is_dead=is_dead, death_date=death_date),
        dynasty_house_id=dynasty_house_id,
        culture_id=culture_id,
        faith_id=faith_id,
    )


def _snap_with_lookups(
    chars: dict,
    *,
    houses_lookup: dict | None = None,
    cultures_lookup: dict | None = None,
    faiths_lookup: dict | None = None,
    date: str = "1067.2.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        houses_lookup=houses_lookup or {},
        cultures_lookup=cultures_lookup or {},
        faiths_lookup=faiths_lookup or {},
    )


def test_house_change_event_emitted_on_dynasty_house_id_delta() -> None:
    """Cadet-branch founding: player's dynasty_house flips from parent
    house's id to a newly-created house id."""
    prev = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=10450)},
        houses_lookup={10450: "dynn_Briain", 12180: "dynn_Ruaidri"},
    )
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=12180)},
        houses_lookup={10450: "dynn_Briain", 12180: "dynn_Ruaidri"},
    )
    events = diff_snapshots(prev, curr)
    house_events = [e for e in events if e.event.t == "house_change"]
    assert len(house_events) == 1
    p = house_events[0].event.p
    assert p.from_house_id == 10450
    assert p.from_house_name == "dynn_Briain"
    assert p.to_house_id == 12180
    assert p.to_house_name == "dynn_Ruaidri"


def test_house_change_event_handles_none_to_id() -> None:
    """A character with no house gaining one (rare but possible)."""
    prev = _snap_with_lookups({1: _make_char_with_state(1, dynasty_house_id=None)})
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=99)},
        houses_lookup={99: "dynn_New"},
    )
    events = diff_snapshots(prev, curr)
    [hc] = [e for e in events if e.event.t == "house_change"]
    assert hc.event.p.from_house_id is None
    assert hc.event.p.from_house_name is None
    assert hc.event.p.to_house_id == 99
    assert hc.event.p.to_house_name == "dynn_New"


def test_house_change_event_emits_none_name_for_missing_lookup() -> None:
    """Newly-created house id might not be in the snapshot lookup yet —
    payload still goes out, just with name=None for the unmapped side."""
    prev = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=10450)},
        houses_lookup={10450: "dynn_Briain"},
    )
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=99999)},
        houses_lookup={10450: "dynn_Briain"},  # 99999 missing
    )
    [hc] = [e for e in diff_snapshots(prev, curr) if e.event.t == "house_change"]
    assert hc.event.p.to_house_id == 99999
    assert hc.event.p.to_house_name is None


def test_culture_change_event() -> None:
    prev = _snap_with_lookups(
        {1: _make_char_with_state(1, culture_id=10)},
        cultures_lookup={10: "norse", 11: "anglo_saxon"},
    )
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, culture_id=11)},
        cultures_lookup={10: "norse", 11: "anglo_saxon"},
    )
    [e] = [d for d in diff_snapshots(prev, curr) if d.event.t == "culture_change"]
    assert e.event.p.from_culture_name == "norse"
    assert e.event.p.to_culture_name == "anglo_saxon"


def test_faith_change_event() -> None:
    prev = _snap_with_lookups(
        {1: _make_char_with_state(1, faith_id=5)},
        faiths_lookup={5: "asatru", 6: "catholic"},
    )
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, faith_id=6)},
        faiths_lookup={5: "asatru", 6: "catholic"},
    )
    [e] = [d for d in diff_snapshots(prev, curr) if d.event.t == "faith_change"]
    assert e.event.p.from_faith_name == "asatru"
    assert e.event.p.to_faith_name == "catholic"


def test_no_state_change_events_for_dead_character() -> None:
    """Dead chars may have engine-side state cleared on death — same
    defensive skip as nickname / adventurer / trait diffs."""
    prev = _snap_with_lookups({1: _make_char_with_state(1, dynasty_house_id=10)})
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=20, is_dead=True, death_date="1067.5.1")}
    )
    events = diff_snapshots(prev, curr)
    state_kinds = {"house_change", "culture_change", "faith_change"}
    assert [e for e in events if e.event.t in state_kinds] == []


def test_no_state_change_events_when_ids_unchanged() -> None:
    """Snapshots with identical state IDs emit no spurious events."""
    prev = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=10, culture_id=5, faith_id=3)}
    )
    curr = _snap_with_lookups(
        {1: _make_char_with_state(1, dynasty_house_id=10, culture_id=5, faith_id=3)}
    )
    events = diff_snapshots(prev, curr)
    state_kinds = {"house_change", "culture_change", "faith_change"}
    assert [e for e in events if e.event.t in state_kinds] == []


# --- ck3_chronicler-dr9: title diff events ---


def _t(
    title_id: int,
    *,
    key: str,
    holder: int | None,
    name: str | None = None,
    tier: str = "other",
):
    """TitleSnapshot helper."""
    from chronicler.save.parse import TitleSnapshot, _tier_from_key

    return TitleSnapshot(
        title_id=title_id,
        key=key,
        name=name,
        tier=_tier_from_key(key) if key else tier,
        holder_id=holder,
    )


def _snap_with_titles(
    chars: dict, *, titles: dict | None = None, date: str = "1067.2.1"
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        titles=titles or {},
    )


def test_title_acquired_event_when_holder_changes_to_tracked_char() -> None:
    """Title existed in prev with a different holder; now held by char 1."""
    prev = _snap_with_titles(
        {1: _make_char(1)},
        titles={100: _t(100, key="d_munster", holder=999, name="Duchy of Munster")},
    )
    curr = _snap_with_titles(
        {1: _make_char(1)},
        titles={100: _t(100, key="d_munster", holder=1, name="Duchy of Munster")},
    )
    [e] = [d for d in diff_snapshots(prev, curr) if d.event.t == "title_acquired"]
    assert e.event.c == 1
    assert e.event.p.title_id == 100
    assert e.event.p.title_key == "d_munster"
    assert e.event.p.title_name == "Duchy of Munster"
    assert e.event.p.tier == "duchy"
    assert e.event.p.from_holder_id == 999
    # Former holder appears as a participant for biography prompts
    assert e.participants == (("former_holder", 999),)


def test_title_relinquished_event_when_holder_changes_away_from_tracked_char() -> None:
    prev = _snap_with_titles(
        {1: _make_char(1)},
        titles={100: _t(100, key="c_thomond", holder=1)},
    )
    curr = _snap_with_titles(
        {1: _make_char(1)},
        titles={100: _t(100, key="c_thomond", holder=42)},
    )
    [e] = [d for d in diff_snapshots(prev, curr) if d.event.t == "title_relinquished"]
    assert e.event.c == 1
    assert e.event.p.title_key == "c_thomond"
    assert e.event.p.tier == "county"
    assert e.event.p.to_holder_id == 42
    assert e.participants == (("new_holder", 42),)


def test_title_created_event_when_title_appears_with_holder() -> None:
    """Decision-driven creation: kingdom didn't exist in prev, exists
    in curr held by the character. The 'unify the petty kingdom of
    Munster' / form-empire flow.

    Note: prev must have at least one title so _diff_titles doesn't
    short-circuit as stale-baseline (ck3_chronicler-qz5y)."""
    prev = _snap_with_titles(
        {1: _make_char(1)},
        titles={100: _t(100, key="c_thomond", holder=1)},
    )
    curr = _snap_with_titles(
        {1: _make_char(1)},
        titles={
            100: _t(100, key="c_thomond", holder=1),
            555: _t(555, key="k_munster", holder=1, name="Kingdom of Munster"),
        },
    )
    title_events = [
        d for d in diff_snapshots(prev, curr) if d.event.t in {"title_created", "title_acquired"}
    ]
    assert len(title_events) == 1
    assert title_events[0].event.t == "title_created"
    p = title_events[0].event.p
    assert p.title_key == "k_munster"
    assert p.title_name == "Kingdom of Munster"
    assert p.tier == "kingdom"


def test_title_relinquished_when_title_disappears_entirely() -> None:
    """Title destroyed: in prev held by char 1, gone from curr."""
    prev = _snap_with_titles(
        {1: _make_char(1)},
        titles={100: _t(100, key="d_munster", holder=1)},
    )
    curr = _snap_with_titles({1: _make_char(1)}, titles={})
    [e] = [d for d in diff_snapshots(prev, curr) if d.event.t == "title_relinquished"]
    assert e.event.p.to_holder_id is None
    assert e.participants == ()  # no new holder to participantise


def test_no_title_events_for_unchanged_holdings() -> None:
    same_titles = {100: _t(100, key="d_munster", holder=1)}
    prev = _snap_with_titles({1: _make_char(1)}, titles=same_titles)
    curr = _snap_with_titles({1: _make_char(1)}, titles=same_titles)
    events = diff_snapshots(prev, curr)
    assert [e for e in events if "title_" in e.event.t] == []


def test_title_acquired_per_county_in_unification_burst() -> None:
    """Per the issue's noise note: a kingdom-tier unification looks like
    a burst of TitleAcquired events (one per county that flipped). This
    test verifies the per-title fidelity; coalescing is deferred."""
    prev = _snap_with_titles(
        {1: _make_char(1)},
        titles={
            10: _t(10, key="c_county_a", holder=999),
            11: _t(11, key="c_county_b", holder=998),
            12: _t(12, key="c_county_c", holder=997),
        },
    )
    curr = _snap_with_titles(
        {1: _make_char(1)},
        titles={
            10: _t(10, key="c_county_a", holder=1),
            11: _t(11, key="c_county_b", holder=1),
            12: _t(12, key="c_county_c", holder=1),
        },
    )
    acquired = [d for d in diff_snapshots(prev, curr) if d.event.t == "title_acquired"]
    assert len(acquired) == 3
    ids = sorted(d.event.p.title_id for d in acquired)
    assert ids == [10, 11, 12]


def test_title_diff_respects_tracked_filter() -> None:
    """Title transitions for non-tracked characters are not emitted when
    a tracked_filter is provided."""
    prev = _snap_with_titles(
        {1: _make_char(1), 2: _make_char(2)},
        titles={100: _t(100, key="d_x", holder=999)},
    )
    curr = _snap_with_titles(
        {1: _make_char(1), 2: _make_char(2)},
        titles={100: _t(100, key="d_x", holder=2)},  # untracked char 2 grabs it
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if "title_" in e.event.t] == []


# --- ck3_chronicler-9wrd: epidemic outbreak ---


def _snap_with_epidemics(
    chars: dict[int, CharacterSnapshot],
    *,
    epidemics: dict[int, EpidemicSnapshot] | None = None,
    char_to_eps: dict[int, frozenset[int]] | None = None,
    date: str = "1083.5.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        epidemics=epidemics or {},
        character_to_epidemics=char_to_eps or {},
    )


def _ep(
    eid: int,
    *,
    epidemic_type: str = "measles",
    name: str = "Yamato Boils",
    intensity: str = "major",
    creation_date: str | None = "1083.4.4",
    deaths: int = 0,
    provinces: int = 0,
) -> EpidemicSnapshot:
    return EpidemicSnapshot(
        epidemic_id=eid,
        epidemic_type=epidemic_type,
        name=name,
        intensity=intensity,
        creation_date=creation_date,
        start_province=None,
        num_infected_provinces=provinces,
        num_infected_characters=0,
        num_character_deaths=deaths,
    )


def test_epidemic_outbreak_emitted_when_tracked_char_newly_infected() -> None:
    """The bread-and-butter case: char 1234 wasn't in any epidemic last
    tick, this tick they're in epidemic 67108866. One epidemic_outbreak
    event with the metadata payload."""
    prev = _snap_with_epidemics(
        {1234: _make_char(1234)},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={},
    )
    curr = _snap_with_epidemics(
        {1234: _make_char(1234)},
        epidemics={67108866: _ep(67108866, deaths=121, provinces=155)},
        char_to_eps={1234: frozenset({67108866})},
        date="1083.5.1",
    )
    events = diff_snapshots(prev, curr)
    outbreaks = [e for e in events if isinstance(e.event, EpidemicOutbreakEvent)]
    assert len(outbreaks) == 1
    p = outbreaks[0].event.p
    assert p.epidemic_id == 67108866
    assert p.name == "Yamato Boils"
    assert p.intensity == "major"
    assert p.num_character_deaths == 121
    assert p.num_infected_provinces == 155
    assert p.start_date == "1083.4.4"


def test_epidemic_outbreak_does_not_re_fire_when_continuing() -> None:
    """Char was already in the epidemic last tick AND this tick — no
    event. The diff is per-character set membership transition only."""
    prev = _snap_with_epidemics(
        {1234: _make_char(1234)},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={1234: frozenset({67108866})},
    )
    curr = _snap_with_epidemics(
        {1234: _make_char(1234)},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={1234: frozenset({67108866})},
    )
    assert [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, EpidemicOutbreakEvent)
    ] == []


def test_epidemic_outbreak_skipped_for_dead_chars() -> None:
    """Dead chars don't get epidemic events — death events / engine
    cleanup pass cover the relationship-perspective signal."""
    prev = _snap_with_epidemics(
        {1234: _make_char(1234, is_dead=True, death_date="1083.4.20")},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={},
    )
    curr = _snap_with_epidemics(
        {1234: _make_char(1234, is_dead=True, death_date="1083.4.20")},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={1234: frozenset({67108866})},
    )
    assert [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, EpidemicOutbreakEvent)
    ] == []


def test_epidemic_outbreak_respects_tracked_filter() -> None:
    """Untracked chars who get infected emit nothing; only tracked
    chars produce outbreak events."""
    prev = _snap_with_epidemics(
        {1: _make_char(1), 2: _make_char(2)},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={},
    )
    curr = _snap_with_epidemics(
        {1: _make_char(1), 2: _make_char(2)},
        epidemics={67108866: _ep(67108866)},
        char_to_eps={1: frozenset({67108866}), 2: frozenset({67108866})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    outbreaks = [e for e in events if isinstance(e.event, EpidemicOutbreakEvent)]
    assert len(outbreaks) == 1
    assert outbreaks[0].event.c == 1


# --- ck3_chronicler-2ur: building completion ---


def _snap_with_constructions(
    chars: dict[int, CharacterSnapshot],
    *,
    in_flight: dict[tuple[int, int], ConstructionSnapshot] | None = None,
    char_to_cons: dict[int, frozenset[tuple[int, int]]] | None = None,
    buildings_by_slot: dict[tuple[int, int], str] | None = None,
    date: str = "1067.6.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        in_flight_constructions=in_flight or {},
        character_to_constructions=char_to_cons or {},
        holding_buildings_by_slot=buildings_by_slot or {},
    )


def _cons(
    pid: int,
    slot: int,
    building: str,
    *,
    character_id: int | None = 1,
    start_date: str | None = "1066.9.24",
) -> ConstructionSnapshot:
    return ConstructionSnapshot(
        province_id=pid,
        slot_index=slot,
        building=building,
        start_date=start_date,
        character_id=character_id,
    )


def test_building_completed_emitted_when_construction_ships() -> None:
    """Bread-and-butter case: char 1 had longhouses_01 in flight at
    province 280 slot 3 last tick. This tick the construction record is
    gone AND slot 3 in province 280's buildings now carries
    longhouses_01 → one building_completed event."""
    prev = _snap_with_constructions(
        {1: _make_char(1)},
        in_flight={(280, 3): _cons(280, 3, "longhouses_01")},
        char_to_cons={1: frozenset({(280, 3)})},
        buildings_by_slot={(280, 0): "tribe_02"},
    )
    curr = _snap_with_constructions(
        {1: _make_char(1)},
        in_flight={},
        char_to_cons={},
        buildings_by_slot={
            (280, 0): "tribe_02",
            (280, 3): "longhouses_01",  # shipped
        },
        date="1071.7.1",
    )
    events = diff_snapshots(prev, curr)
    completions = [e for e in events if isinstance(e.event, BuildingCompletedEvent)]
    assert len(completions) == 1
    p = completions[0].event.p
    assert p.building == "longhouses_01"
    assert p.province_id == 280
    assert p.slot_index == 3
    assert p.start_date == "1066.9.24"
    assert completions[0].event.c == 1


def test_building_completed_skipped_when_construction_cancelled() -> None:
    """Construction record disappears AND slot 3 in curr is still empty
    (or carries a different type) → cancellation, not completion. No
    event in v1."""
    prev = _snap_with_constructions(
        {1: _make_char(1)},
        in_flight={(280, 3): _cons(280, 3, "longhouses_01")},
        char_to_cons={1: frozenset({(280, 3)})},
    )
    curr = _snap_with_constructions(
        {1: _make_char(1)},
        in_flight={},
        char_to_cons={},
        buildings_by_slot={},  # slot 3 still empty
    )
    completions = [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, BuildingCompletedEvent)
    ]
    assert completions == []


def test_building_completed_does_not_re_fire_while_in_flight() -> None:
    """Construction still in flight in both prev and curr → no event.
    Set-diff over character_to_constructions means a continuing build
    can't fire repeatedly."""
    prev = _snap_with_constructions(
        {1: _make_char(1)},
        in_flight={(280, 3): _cons(280, 3, "longhouses_01")},
        char_to_cons={1: frozenset({(280, 3)})},
    )
    curr = _snap_with_constructions(
        {1: _make_char(1)},
        in_flight={(280, 3): _cons(280, 3, "longhouses_01")},
        char_to_cons={1: frozenset({(280, 3)})},
    )
    completions = [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, BuildingCompletedEvent)
    ]
    assert completions == []


def test_building_completed_skipped_for_dead_chars() -> None:
    """Mirror of the epidemic dead-char skip: a deceased constructor
    can't 'finish' a building biographically, and the engine's
    cleanup-on-death pass is exactly the kind of phantom transition
    we'd otherwise emit a false-positive completion for."""
    prev = _snap_with_constructions(
        {1: _make_char(1, is_dead=True, death_date="1067.5.20")},
        in_flight={(280, 3): _cons(280, 3, "longhouses_01")},
        char_to_cons={1: frozenset({(280, 3)})},
    )
    curr = _snap_with_constructions(
        {1: _make_char(1, is_dead=True, death_date="1067.5.20")},
        in_flight={},
        char_to_cons={},
        buildings_by_slot={(280, 3): "longhouses_01"},
    )
    completions = [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, BuildingCompletedEvent)
    ]
    assert completions == []


def test_building_completed_respects_tracked_filter() -> None:
    """Only tracked characters' completions fire — untracked
    constructors' shipped buildings are silent."""
    prev = _snap_with_constructions(
        {1: _make_char(1), 2: _make_char(2)},
        in_flight={
            (280, 3): _cons(280, 3, "longhouses_01", character_id=1),
            (461, 3): _cons(461, 3, "pastures_01", character_id=2),
        },
        char_to_cons={1: frozenset({(280, 3)}), 2: frozenset({(461, 3)})},
    )
    curr = _snap_with_constructions(
        {1: _make_char(1), 2: _make_char(2)},
        in_flight={},
        char_to_cons={},
        buildings_by_slot={
            (280, 3): "longhouses_01",
            (461, 3): "pastures_01",
        },
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    completions = [e for e in events if isinstance(e.event, BuildingCompletedEvent)]
    assert len(completions) == 1
    assert completions[0].event.c == 1


# --- ck3_chronicler-4pj: miscarriage detection ---


# A traits_lookup where 'pregnant' is at index 102 — matches the v09-smoke
# campaign's actual ordering.
_PREGNANCY_TRAITS_LOOKUP: tuple[str, ...] = tuple(
    "pregnant" if i == 102 else f"trait_{i}" for i in range(150)
)


def _make_pregnancy_char(
    cid: int,
    *,
    traits: tuple[int, ...],
    primary_spouse: int | None = None,
    memories: tuple[MemorySnapshot, ...] = (),
    is_dead: bool = False,
    death_date: str | None = None,
) -> CharacterSnapshot:
    return make_char(
        cid,
        first_name="Agnes",
        female=True,
        birth_date="1056.1.1",
        is_dead=is_dead,
        death_date=death_date,
        traits=traits,
        family=FamilySnapshot(primary_spouse=primary_spouse),
        memories=memories,
    )


def test_miscarriage_emitted_when_pregnant_lost_without_child_born() -> None:
    """The 1081.8.1 Agnes case from the v09-smoke campaign: pregnant
    trait was set last save; this save it's gone but no child_born
    memory landed. Emit miscarriage."""
    prev = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50, 102), primary_spouse=2)},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    curr = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50,), primary_spouse=2)},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
        date="1081.8.1",
    )
    events = diff_snapshots(prev, curr)
    miscarriages = [e for e in events if isinstance(e.event, MiscarriageEvent)]
    assert len(miscarriages) == 1
    m = miscarriages[0]
    assert m.event.c == 1
    assert m.event.d == "1081.8.1"
    assert m.event.p.assumed_father_id == 2
    assert ("assumed_father", 2) in m.participants


def test_miscarriage_not_emitted_when_child_born_memory_present() -> None:
    """Pregnant trait drops AND a child_born memory lands in the same
    diff window — successful birth, NOT miscarriage."""
    child_mem = MemorySnapshot(
        memory_id=99001,
        memory_type="child_born",
        creation_date="1078.9.1",
        end_date=None,
        participants=(("child", 16843423),),
    )
    prev = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50, 102), primary_spouse=2)},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    curr = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50,), primary_spouse=2, memories=(child_mem,))},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    events = diff_snapshots(prev, curr)
    miscarriages = [e for e in events if isinstance(e.event, MiscarriageEvent)]
    assert miscarriages == []


def test_miscarriage_not_emitted_for_dead_character() -> None:
    """Death clears traits in engine bookkeeping; we don't want the
    death+miscarriage double-emit on a woman who died while pregnant —
    the death event already covers the dominant signal."""
    prev = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50, 102), primary_spouse=2)},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    curr = _snap_with_traits_lookup(
        {
            1: _make_pregnancy_char(
                1,
                traits=(),
                primary_spouse=2,
                is_dead=True,
                death_date="1083.4.1",
            )
        },
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    events = diff_snapshots(prev, curr)
    miscarriages = [e for e in events if isinstance(e.event, MiscarriageEvent)]
    assert miscarriages == []


def test_miscarriage_silent_when_traits_lookup_missing_pregnant() -> None:
    """Mod-stripped or test-fixture lookup tables that don't include a
    'pregnant' entry can't anchor the detection — the helper falls
    through silently rather than emitting on every trait drop."""
    minimal_lookup = ("courage", "wrath")  # no 'pregnant' anywhere
    prev = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(0, 1))},
        traits_lookup=minimal_lookup,
    )
    curr = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(0,))},
        traits_lookup=minimal_lookup,
    )
    miscarriages = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, MiscarriageEvent)]
    assert miscarriages == []


def test_miscarriage_no_emit_when_pregnant_still_present() -> None:
    """Pregnancy continues across the diff window — no event."""
    prev = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50, 102), primary_spouse=2)},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    curr = _snap_with_traits_lookup(
        {1: _make_pregnancy_char(1, traits=(50, 102), primary_spouse=2)},
        traits_lookup=_PREGNANCY_TRAITS_LOOKUP,
    )
    miscarriages = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, MiscarriageEvent)]
    assert miscarriages == []


# --- ck3_chronicler-qx7n: 8aie slice 1, activity completion ---


def _snap_with_activities(
    chars: dict[int, CharacterSnapshot],
    *,
    activities: dict[int, ActivitySnapshot] | None = None,
    char_to_acts: dict[int, frozenset[int]] | None = None,
    date: str = "915.6.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="867.1.1",
        current_date=date,
        player_character_id=1,
        characters=chars,
        activities=activities or {},
        character_to_activities=char_to_acts or {},
    )


def _act(
    aid: int,
    *,
    activity_type: str = "activity_feast",
    host_id: int = 1,
    attendees: frozenset[int] = frozenset(),
    start_province_id: int | None = 100,
    active_start_date: str | None = "915.4.16",
) -> ActivitySnapshot:
    return ActivitySnapshot(
        activity_id=aid,
        activity_type=activity_type,
        host_id=host_id,
        creation_date="914.11.29",
        active_start_date=active_start_date,
        start_province_id=start_province_id,
        attendees=attendees,
    )


def test_activity_completed_emitted_when_tracked_host_no_longer_in_set() -> None:
    """Tracked char 1 was hosting activity 7 last tick; this tick the
    activity is gone from activity_manager.database (CK3 pruned it).
    One activity_completed event with role=host."""
    prev = _snap_with_activities(
        {1: _make_char(1)},
        activities={7: _act(7, activity_type="activity_feast", host_id=1)},
        char_to_acts={1: frozenset({7})},
    )
    curr = _snap_with_activities(
        {1: _make_char(1)},
        activities={},
        char_to_acts={},
        date="915.6.1",
    )
    events = diff_snapshots(prev, curr)
    completed = [e for e in events if isinstance(e.event, ActivityCompletedEvent)]
    assert len(completed) == 1
    p = completed[0].event.p
    assert p.activity_id == 7
    assert p.activity_type == "activity_feast"
    assert p.role == "host"
    assert p.host_id == 1
    assert p.start_province_id == 100
    assert p.start_date == "915.4.16"
    assert completed[0].event.c == 1
    assert completed[0].event.d == "915.6.1"


def test_activity_completed_emitted_when_tracked_attendee_no_longer_in_set() -> None:
    """Tracked char 1 was an attendee at host 9's activity last tick;
    this tick the activity is gone. One event with role=attendee and
    host_id set to the non-tracked host for prose ('attended X
    hosted by Y')."""
    prev = _snap_with_activities(
        {1: _make_char(1)},
        activities={
            7: _act(
                7,
                activity_type="activity_pilgrimage",
                host_id=9,
                attendees=frozenset({1}),
            )
        },
        char_to_acts={9: frozenset({7}), 1: frozenset({7})},
    )
    curr = _snap_with_activities(
        {1: _make_char(1)},
        activities={},
        char_to_acts={},
    )
    events = diff_snapshots(prev, curr)
    completed = [e for e in events if isinstance(e.event, ActivityCompletedEvent)]
    assert len(completed) == 1
    p = completed[0].event.p
    assert p.activity_id == 7
    assert p.activity_type == "activity_pilgrimage"
    assert p.role == "attendee"
    assert p.host_id == 9


def test_activity_completed_does_not_re_fire_while_active() -> None:
    """Tracked char was in the activity last tick AND this tick — no
    event. Diff is per-character set membership transition only."""
    prev = _snap_with_activities(
        {1: _make_char(1)},
        activities={7: _act(7, host_id=1)},
        char_to_acts={1: frozenset({7})},
    )
    curr = _snap_with_activities(
        {1: _make_char(1)},
        activities={7: _act(7, host_id=1)},
        char_to_acts={1: frozenset({7})},
    )
    assert [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, ActivityCompletedEvent)
    ] == []


def test_activity_completed_skipped_for_dead_chars() -> None:
    """Dead chars don't emit activity_completed — the engine's
    death-cleanup pass prunes their participation; the DeathEvent
    boundary already carries the relationship signal. Same skip as
    _diff_epidemics / _diff_constructions."""
    prev = _snap_with_activities(
        {1: _make_char(1, is_dead=True, death_date="915.5.20")},
        activities={7: _act(7, host_id=1)},
        char_to_acts={1: frozenset({7})},
    )
    curr = _snap_with_activities(
        {1: _make_char(1, is_dead=True, death_date="915.5.20")},
        activities={},
        char_to_acts={},
    )
    assert [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, ActivityCompletedEvent)
    ] == []


def test_activity_completed_respects_tracked_filter() -> None:
    """Untracked chars whose activity ends emit nothing; only tracked
    chars produce activity_completed events."""
    prev = _snap_with_activities(
        {1: _make_char(1), 2: _make_char(2)},
        activities={7: _act(7, host_id=1, attendees=frozenset({2}))},
        char_to_acts={1: frozenset({7}), 2: frozenset({7})},
    )
    curr = _snap_with_activities(
        {1: _make_char(1), 2: _make_char(2)},
        activities={},
        char_to_acts={},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    completed = [e for e in events if isinstance(e.event, ActivityCompletedEvent)]
    assert len(completed) == 1
    assert completed[0].event.c == 1
    assert completed[0].event.p.role == "host"


# --- ck3_chronicler-m658: 8aie slice 2, inspiration sponsorship ---


def _snap_with_inspirations(
    chars: dict[int, CharacterSnapshot],
    *,
    inspirations: dict[int, InspirationSnapshot] | None = None,
    char_to_sponsored: dict[int, frozenset[int]] | None = None,
    date: str = "930.1.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.5",
        bookmark_date="867.1.1",
        current_date=date,
        player_character_id=1,
        characters=chars,
        inspirations=inspirations or {},
        character_to_sponsored_inspirations=char_to_sponsored or {},
    )


def _insp(
    iid: int,
    *,
    inspiration_type: str = "weapon_inspiration",
    sponsored: str | None = "930.1.1",
    total_cost: int = 148,
    progress: int = 5,
    artisan_character_id: int | None = 99,
) -> InspirationSnapshot:
    return InspirationSnapshot(
        inspiration_id=iid,
        inspiration_type=inspiration_type,
        sponsored=sponsored,
        total_cost=total_cost,
        progress=progress,
        artisan_character_id=artisan_character_id,
    )


def test_inspiration_sponsored_emitted_when_tracked_char_starts_sponsoring() -> None:
    """ck3_chronicler-m658: tracked char 1 was sponsoring nothing last
    tick; this tick their landed_data.sponsored_inspirations contains
    inspiration 42. One inspiration_sponsored event fires on char 1."""
    from chronicler.schema import InspirationSponsoredEvent

    prev = _snap_with_inspirations(
        {1: _make_char(1)},
        inspirations={},
        char_to_sponsored={},
    )
    curr = _snap_with_inspirations(
        {1: _make_char(1)},
        inspirations={
            42: _insp(
                42,
                inspiration_type="weapon_inspiration",
                sponsored="930.5.1",
                artisan_character_id=99,
                total_cost=148,
            )
        },
        char_to_sponsored={1: frozenset({42})},
        date="930.5.1",
    )
    events = diff_snapshots(prev, curr)
    sponsored = [e for e in events if isinstance(e.event, InspirationSponsoredEvent)]
    assert len(sponsored) == 1
    assert sponsored[0].event.c == 1
    p = sponsored[0].event.p
    assert p.inspiration_id == 42
    assert p.inspiration_type == "weapon_inspiration"
    assert p.artisan_character_id == 99
    assert p.total_cost == 148


def test_inspiration_sponsored_does_not_re_fire_while_active() -> None:
    """Tracked char was sponsoring inspiration 42 last tick AND this
    tick — no event (membership stable; only transitions fire)."""
    from chronicler.schema import InspirationSponsoredEvent

    prev = _snap_with_inspirations(
        {1: _make_char(1)},
        inspirations={42: _insp(42)},
        char_to_sponsored={1: frozenset({42})},
    )
    curr = _snap_with_inspirations(
        {1: _make_char(1)},
        inspirations={42: _insp(42)},
        char_to_sponsored={1: frozenset({42})},
    )
    assert [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, InspirationSponsoredEvent)
    ] == []


def test_inspiration_sponsored_skipped_for_dead_chars() -> None:
    """Engine cleanup on death may transiently surface or drop entries
    on landed_data; don't synthesise a sponsorship event for dead
    chars."""
    from chronicler.schema import InspirationSponsoredEvent

    prev = _snap_with_inspirations(
        {1: _make_char(1, is_dead=False)},
        inspirations={},
        char_to_sponsored={},
    )
    curr = _snap_with_inspirations(
        {1: _make_char(1, is_dead=True, death_date="930.4.10")},
        inspirations={42: _insp(42, sponsored="930.5.1")},
        char_to_sponsored={1: frozenset({42})},
        date="930.5.1",
    )
    assert [
        e for e in diff_snapshots(prev, curr) if isinstance(e.event, InspirationSponsoredEvent)
    ] == []


def test_inspiration_sponsored_respects_tracked_filter() -> None:
    """Untracked chars whose sponsored_inspirations changes emit
    nothing; only tracked chars surface the sponsorship beat."""
    from chronicler.schema import InspirationSponsoredEvent

    prev = _snap_with_inspirations(
        {1: _make_char(1), 2: _make_char(2)},
        inspirations={},
        char_to_sponsored={},
    )
    curr = _snap_with_inspirations(
        {1: _make_char(1), 2: _make_char(2)},
        inspirations={
            42: _insp(42, sponsored="930.5.1"),
            43: _insp(43, sponsored="930.5.1"),
        },
        char_to_sponsored={1: frozenset({42}), 2: frozenset({43})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    sponsored = [e for e in events if isinstance(e.event, InspirationSponsoredEvent)]
    assert len(sponsored) == 1
    assert sponsored[0].event.c == 1
    assert sponsored[0].event.p.inspiration_id == 42


# --- ck3_chronicler-621o: 8aie slice 6, contract completion ---


def _snap_with_contracts(
    chars: dict[int, CharacterSnapshot],
    *,
    contracts: dict[int, ContractSnapshot] | None = None,
    char_to_contracts: dict[int, frozenset[int]] | None = None,
    date: str = "1070.1.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.5",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        task_contracts=contracts or {},
        character_to_contracts=char_to_contracts or {},
    )


def _contract(
    cid: int,
    *,
    status: str,
    owner_id: int = 1,
    contract_type: str = "laamp_base_6021",
    name: str | None = "Perform in a Play",
    tier: int | None = 2,
    employer_id: int | None = 27944,
    location_province_id: int | None = 10621,
    acceptance_date: str | None = None,
    completion_date: str | None = None,
) -> ContractSnapshot:
    return ContractSnapshot(
        contract_id=cid,
        contract_type=contract_type,
        name=name,
        tier=tier,
        employer_id=employer_id,
        owner_id=owner_id,
        location_province_id=location_province_id,
        status=status,
        acceptance_date=acceptance_date,
        completion_date=completion_date,
    )


def test_contract_completed_emitted_when_status_flips_to_completed() -> None:
    """Live anchor: 2026-05-17 Genji adventurer smoke. Player accepted
    'Perform in a Play' (status=accepted, acceptance_date set); next
    snapshot showed status=completed + completion_date. The diff layer
    was silent; emit contract_completed with outcome='completed'."""
    prev = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="accepted", acceptance_date="1069.4.28")},
        char_to_contracts={1: frozenset({7})},
    )
    curr = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={
            7: _contract(
                7,
                status="completed",
                acceptance_date="1069.4.28",
                completion_date="1070.1.1",
            )
        },
        char_to_contracts={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    completed = [e for e in events if isinstance(e.event, ContractCompletedEvent)]
    assert len(completed) == 1
    e = completed[0].event
    assert e.c == 1
    assert e.p.outcome == "completed"
    assert e.p.contract_type == "laamp_base_6021"
    assert e.p.name == "Perform in a Play"
    assert e.p.tier == 2
    assert e.p.employer_id == 27944
    assert e.p.location_province_id == 10621
    assert e.p.acceptance_date == "1069.4.28"
    assert e.p.completion_date == "1070.1.1"


def test_contract_completed_emitted_when_status_flips_to_invalidated() -> None:
    """A failed / cancelled contract goes to status='invalidated'.
    Same event, outcome maps to 'invalidated'."""
    prev = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="accepted")},
        char_to_contracts={1: frozenset({7})},
    )
    curr = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="invalidated")},
        char_to_contracts={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    completed = [e for e in events if isinstance(e.event, ContractCompletedEvent)]
    assert len(completed) == 1
    assert completed[0].event.p.outcome == "invalidated"


def test_contract_completed_silent_when_status_unchanged() -> None:
    """A contract still in 'accepted' state produces no event."""
    prev = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="accepted")},
        char_to_contracts={1: frozenset({7})},
    )
    curr = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="accepted")},
        char_to_contracts={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, ContractCompletedEvent)] == []


def test_contract_completed_silent_on_terminal_already_in_prev() -> None:
    """If a contract was already 'completed' in prev, don't re-emit on
    a subsequent tick — the event fired on the original transition."""
    prev = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="completed")},
        char_to_contracts={1: frozenset({7})},
    )
    curr = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="completed")},
        char_to_contracts={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, ContractCompletedEvent)] == []


def test_contract_completed_silent_for_dead_chars() -> None:
    """Death-tick cleanup pattern, mirrors _diff_activities."""
    dead = make_char(
        1,
        first_name="Genji",
        is_dead=True,
        birth_date="1045.3.8",
        death_date="1070.1.1",
        culture_id=119,
        faith_id=111,
        location_id=None,
    )
    prev = _snap_with_contracts(
        {1: _make_char(1)},
        contracts={7: _contract(7, status="accepted")},
        char_to_contracts={1: frozenset({7})},
    )
    curr = _snap_with_contracts(
        {1: dead},
        contracts={7: _contract(7, status="completed")},
        char_to_contracts={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, ContractCompletedEvent)] == []


def test_contract_completed_respects_tracked_filter() -> None:
    """Untracked owner whose contract terminates emits nothing."""
    prev = _snap_with_contracts(
        {1: _make_char(1), 2: _make_char(2)},
        contracts={
            7: _contract(7, status="accepted", owner_id=2),
        },
        char_to_contracts={2: frozenset({7})},
    )
    curr = _snap_with_contracts(
        {1: _make_char(1), 2: _make_char(2)},
        contracts={
            7: _contract(7, status="completed", owner_id=2),
        },
        char_to_contracts={2: frozenset({7})},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if isinstance(e.event, ContractCompletedEvent)] == []


# --- ck3_chronicler-mke9: 8aie slice 7, camp companion joined/left ---


def _snap_with_court_positions(
    chars: dict[int, CharacterSnapshot],
    *,
    positions: dict[int, CourtPositionSnapshot] | None = None,
    char_to_cps: dict[int, frozenset[int]] | None = None,
    date: str = "1070.1.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.5",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        court_positions=positions or {},
        character_to_court_positions=char_to_cps or {},
    )


def _cp(
    pid: int,
    *,
    court_position: str = "travel_leader_court_position",
    employee_id: int | None = 100,
    employer_id: int = 1,
    hire_date: str = "1066.9.15",
) -> CourtPositionSnapshot:
    return CourtPositionSnapshot(
        position_id=pid,
        court_position=court_position,
        employee_id=employee_id,
        employer_id=employer_id,
        hire_date=hire_date,
    )


def test_camp_companion_joined_emitted_when_new_position_appears() -> None:
    """Live anchor: Genji recruited Seung_gyeong as court_physician
    on 1069.1.20. A new entry in court_positions.database with
    employer=tracked-char fires camp_companion_joined."""
    employee = _make_char(100, first_name="Seung_gyeong")
    prev = _snap_with_court_positions(
        {1: _make_char(1), 100: employee},
        positions={},
        char_to_cps={},
    )
    curr = _snap_with_court_positions(
        {1: _make_char(1), 100: employee},
        positions={
            7: _cp(
                7,
                court_position="court_physician_court_position",
                employee_id=100,
                hire_date="1069.1.20",
            )
        },
        char_to_cps={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    joined = [e for e in events if isinstance(e.event, CampCompanionJoinedEvent)]
    assert len(joined) == 1
    p = joined[0].event.p
    assert p.position_id == 7
    assert p.court_position == "court_physician_court_position"
    assert p.employee_id == 100
    assert p.employee_name == "Seung_gyeong"
    assert p.hire_date == "1069.1.20"
    assert joined[0].event.c == 1


def test_camp_companion_left_emitted_when_position_disappears() -> None:
    """A companion departing the camp — court_positions row gone in
    curr. Metadata pulled from prev.court_positions."""
    employee = _make_char(100, first_name="Tadanushi")
    prev = _snap_with_court_positions(
        {1: _make_char(1), 100: employee},
        positions={
            7: _cp(
                7,
                court_position="travel_leader_court_position",
                employee_id=100,
                hire_date="1066.9.15",
            )
        },
        char_to_cps={1: frozenset({7})},
    )
    curr = _snap_with_court_positions(
        {1: _make_char(1), 100: employee},
        positions={},
        char_to_cps={},
    )
    events = diff_snapshots(prev, curr)
    left = [e for e in events if isinstance(e.event, CampCompanionLeftEvent)]
    assert len(left) == 1
    p = left[0].event.p
    assert p.position_id == 7
    assert p.court_position == "travel_leader_court_position"
    assert p.employee_id == 100
    assert p.employee_name == "Tadanushi"
    assert left[0].event.c == 1


def test_camp_companion_joined_and_left_emitted_on_simultaneous_swap() -> None:
    """Replacing one companion with another in a single tick — fires
    one joined and one left. Set-diff handles both directions in one
    pass."""
    prev = _snap_with_court_positions(
        {1: _make_char(1), 100: _make_char(100)},
        positions={7: _cp(7, employee_id=100)},
        char_to_cps={1: frozenset({7})},
    )
    curr = _snap_with_court_positions(
        {1: _make_char(1), 200: _make_char(200)},
        positions={8: _cp(8, employee_id=200)},
        char_to_cps={1: frozenset({8})},
    )
    events = diff_snapshots(prev, curr)
    joined = [e for e in events if isinstance(e.event, CampCompanionJoinedEvent)]
    left = [e for e in events if isinstance(e.event, CampCompanionLeftEvent)]
    assert len(joined) == 1
    assert len(left) == 1
    assert joined[0].event.p.position_id == 8
    assert left[0].event.p.position_id == 7


def test_camp_companion_silent_when_set_unchanged() -> None:
    """Roster unchanged — no events."""
    prev = _snap_with_court_positions(
        {1: _make_char(1), 100: _make_char(100)},
        positions={7: _cp(7, employee_id=100)},
        char_to_cps={1: frozenset({7})},
    )
    curr = _snap_with_court_positions(
        {1: _make_char(1), 100: _make_char(100)},
        positions={7: _cp(7, employee_id=100)},
        char_to_cps={1: frozenset({7})},
    )
    events = diff_snapshots(prev, curr)
    assert [
        e for e in events if isinstance(e.event, (CampCompanionJoinedEvent, CampCompanionLeftEvent))
    ] == []


def test_camp_companion_silent_for_dead_chars() -> None:
    """Dead-char guard — death tick purges the deceased's court
    positions, would emit a flood of left events otherwise."""
    dead = make_char(
        1,
        first_name="Genji",
        is_dead=True,
        birth_date="1045.3.8",
        death_date="1070.1.1",
        culture_id=119,
        faith_id=111,
        location_id=None,
    )
    prev = _snap_with_court_positions(
        {1: _make_char(1), 100: _make_char(100)},
        positions={7: _cp(7, employee_id=100)},
        char_to_cps={1: frozenset({7})},
    )
    curr = _snap_with_court_positions(
        {1: dead, 100: _make_char(100)},
        positions={},
        char_to_cps={},
    )
    events = diff_snapshots(prev, curr)
    assert [
        e for e in events if isinstance(e.event, (CampCompanionJoinedEvent, CampCompanionLeftEvent))
    ] == []


def test_camp_companion_respects_tracked_filter() -> None:
    """Untracked employer's roster changes emit nothing."""
    prev = _snap_with_court_positions(
        {1: _make_char(1), 2: _make_char(2), 100: _make_char(100)},
        positions={7: _cp(7, employee_id=100, employer_id=2)},
        char_to_cps={2: frozenset({7})},
    )
    curr = _snap_with_court_positions(
        {1: _make_char(1), 2: _make_char(2), 100: _make_char(100)},
        positions={},
        char_to_cps={},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [
        e for e in events if isinstance(e.event, (CampCompanionJoinedEvent, CampCompanionLeftEvent))
    ] == []


# --- ck3_chronicler-r343: 8aie slice 5, domicile movement ---


def _snap_with_domicile(
    chars: dict[int, CharacterSnapshot],
    *,
    domiciles: dict[int, DomicileSnapshot] | None = None,
    char_to_domicile: dict[int, int] | None = None,
    date: str = "1067.2.1",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.5",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=1,
        characters=chars,
        domiciles=domiciles or {},
        character_to_domicile=char_to_domicile or {},
    )


def _dom(
    did: int,
    *,
    province_id: int | None,
    domicile_type: str | None = "camp",
    owner_title_id: int | None = 18017,
) -> DomicileSnapshot:
    return DomicileSnapshot(
        domicile_id=did,
        owner_title_id=owner_title_id,
        domicile_type=domicile_type,
        province_id=province_id,
    )


def test_domicile_moved_emitted_when_province_changes() -> None:
    """Live anchor: Genji's camp wanders across the map. A province
    change on the tracked character's domicile fires domicile_moved."""
    prev = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={852: _dom(852, province_id=9822)},
        char_to_domicile={1: 852},
    )
    curr = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={852: _dom(852, province_id=9903)},
        char_to_domicile={1: 852},
    )
    events = diff_snapshots(prev, curr)
    moved = [e for e in events if isinstance(e.event, DomicileMovedEvent)]
    assert len(moved) == 1
    p = moved[0].event.p
    assert p.domicile_id == 852
    assert p.domicile_type == "camp"
    assert p.from_province_id == 9822
    assert p.to_province_id == 9903
    assert moved[0].event.c == 1


def test_domicile_silent_when_province_unchanged() -> None:
    """Province unchanged — no event even if other fields shifted."""
    prev = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={852: _dom(852, province_id=9822)},
        char_to_domicile={1: 852},
    )
    curr = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={852: _dom(852, province_id=9822)},
        char_to_domicile={1: 852},
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, DomicileMovedEvent)] == []


def test_domicile_silent_when_no_prev_domicile() -> None:
    """Character had no domicile in prev — skip. Adoption / first-ever
    domicile isn't a meaningful narrative beat for the chronicler."""
    prev = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={},
        char_to_domicile={},
    )
    curr = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={852: _dom(852, province_id=9822)},
        char_to_domicile={1: 852},
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, DomicileMovedEvent)] == []


def test_domicile_silent_for_dead_chars() -> None:
    """Dead-char guard."""
    dead = make_char(
        1,
        first_name="Genji",
        is_dead=True,
        birth_date="1045.3.8",
        death_date="1070.1.1",
        culture_id=119,
        faith_id=111,
        location_id=None,
    )
    prev = _snap_with_domicile(
        {1: _make_char(1)},
        domiciles={852: _dom(852, province_id=9822)},
        char_to_domicile={1: 852},
    )
    curr = _snap_with_domicile(
        {1: dead},
        domiciles={852: _dom(852, province_id=9903)},
        char_to_domicile={1: 852},
    )
    events = diff_snapshots(prev, curr)
    assert [e for e in events if isinstance(e.event, DomicileMovedEvent)] == []


def test_domicile_respects_tracked_filter() -> None:
    """Untracked character's domicile movement emits nothing."""
    prev = _snap_with_domicile(
        {1: _make_char(1), 2: _make_char(2)},
        domiciles={852: _dom(852, province_id=9822)},
        char_to_domicile={2: 852},
    )
    curr = _snap_with_domicile(
        {1: _make_char(1), 2: _make_char(2)},
        domiciles={852: _dom(852, province_id=9903)},
        char_to_domicile={2: 852},
    )
    events = diff_snapshots(prev, curr, tracked_filter={1})
    assert [e for e in events if isinstance(e.event, DomicileMovedEvent)] == []


# --- ck3_chronicler-05va: vanilla_memory falls back to diff window date
#     when CK3 omits creation_date on engine-generated memories ---


def test_vanilla_memory_uses_diff_window_date_when_creation_date_empty() -> None:
    """Two memories of the same type for the same character on the
    same tick must dedup-distinguishably. With creation_date='' on
    both, the previous code produced d='' for both, collapsing the
    dedup key — one would be silently dropped on insert. The fallback
    to the snapshot's current_date gives each event a non-empty,
    stable date."""
    mem1 = MemorySnapshot(
        memory_id=1,
        memory_type="memory_grand_wedding",
        creation_date="",  # CK3 sometimes omits this
        end_date=None,
        participants=(("spouse", 5),),
    )
    mem2 = MemorySnapshot(
        memory_id=2,
        memory_type="memory_grand_wedding",
        creation_date="",
        end_date=None,
        participants=(("spouse", 6),),
    )
    prev = _make_snap({1: _make_char(1, memories=())})
    curr = _make_snap(
        {1: _make_char(1, memories=(mem1, mem2))},
        date="1099.5.10",
    )
    emitted = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, VanillaMemoryEvent)]
    assert len(emitted) == 2
    # Both events carry the diff-window date as fallback, not ''.
    assert all(e.event.d == "1099.5.10" for e in emitted)


# --- ck3_chronicler-70e8: decision-taken keyed by (id, end_date) so
#     re-takes with refreshed cooldown surface as new events ---


def test_decision_retake_with_new_end_date_emits_again() -> None:
    """The same decision_id appearing with a different end_date in
    curr should fire a fresh decision_taken event. Previously the
    prev set was keyed on decision_id only, silently absorbing any
    end_date change as 'already taken'."""
    prev = _make_snap({1: _make_char(1, decisions_taken=(("raise_stele_decision", "1085.1.1"),))})
    curr = _make_snap(
        {1: _make_char(1, decisions_taken=(("raise_stele_decision", "1095.6.1"),))},
        date="1080.6.1",
    )
    emitted = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, DecisionTakenEvent)]
    assert len(emitted) == 1
    assert emitted[0].event.p.decision_id == "raise_stele_decision"


# --- ck3_chronicler-bcbx: spouse promotion is not a marriage ---


def test_promotion_of_existing_spouse_to_primary_does_not_emit_marriage() -> None:
    """Polygamist: primary spouse dies, CK3 promotes another existing
    spouse to primary. The promoted spouse was already in the spouses
    tuple in prev — no new marriage happened. Previously the diff
    fired a phantom MarriageEvent for the promotion."""
    prev = _make_snap(
        {1: _make_char(1, family=FamilySnapshot(primary_spouse=10, spouses=(10, 20)))}
    )
    # primary dies; 20 (already a spouse) is promoted to primary.
    curr = _make_snap(
        {1: _make_char(1, family=FamilySnapshot(primary_spouse=20, spouses=(20,)))},
        date="1080.1.1",
    )
    emitted = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, MarriageEvent)]
    assert emitted == []


def test_brand_new_primary_spouse_still_emits_marriage() -> None:
    """Sanity: when primary_spouse changes to someone who was NOT
    already in prev.family.spouses, that IS a new marriage. Guards
    against the promotion fix over-zealously suppressing legit cases."""
    prev = _make_snap({1: _make_char(1, family=FamilySnapshot(primary_spouse=10, spouses=(10,)))})
    curr = _make_snap(
        {1: _make_char(1, family=FamilySnapshot(primary_spouse=99, spouses=(10, 99)))},
        date="1080.1.1",
    )
    emitted = [e for e in diff_snapshots(prev, curr) if isinstance(e.event, MarriageEvent)]
    assert len(emitted) == 1
    assert emitted[0].event.p.spouse_character_id == 99


# --- ck3_chronicler-qz5y: stale-baseline TitleCreated storm guard ---


def test_diff_titles_short_circuits_on_empty_prev_titles() -> None:
    """A stale or partial baseline (zero titles in prev) used to emit
    a phantom 'title_created' for every title held by every tracked
    character in curr. Now: zero titles in prev → no title events."""
    prev = _snap_with_titles({1: _make_char(1)}, titles={})
    curr = _snap_with_titles(
        {1: _make_char(1)},
        titles={
            100: _t(100, key="c_thomond", holder=1),
            101: _t(101, key="c_munster", holder=1),
            102: _t(102, key="c_kerry", holder=1),
        },
    )
    title_events = [
        d for d in diff_snapshots(prev, curr) if d.event.t in {"title_created", "title_acquired"}
    ]
    assert title_events == []
