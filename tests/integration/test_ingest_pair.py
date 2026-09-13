"""Save-pair ingest core: process_save_pair persistence, participants,
war/memory/travel events, idempotency, event-bus publication,
save_pair_completed frames, and death-driven biography scheduling.

Split from the test_save_ingest monolith (ck3_chronicler-27ov.72)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chronicler.db import (
    Biography,
    Event,
    EventParticipant,
)
from chronicler.db.repository import (
    get_latest_biography_for_character,
    list_events_for_character,
    upsert_character,
)
from chronicler.narrative.scheduler import BiographyScheduler
from chronicler.save.ingest import (
    DEFAULT_SAVE_PATTERN,
    _startup_catchup_scan,
    process_save_pair,
)
from chronicler.save.parse import (
    FamilySnapshot,
    MemorySnapshot,
    SaveSnapshot,
)
from tests.helpers.ingest import (
    _char,
    _FakeProvider,
    _snap,
)
from tests.helpers.snapshots import make_char


def test_save_pair_with_death_persists_event(session: Session) -> None:
    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap(
        {1234: _char(1234, is_dead=True, death_date="1067.1.15")},
        date="1067.2.1",
    )
    results = process_save_pair(prev, curr, session=session)
    session.commit()
    assert len(results) == 1
    assert results[0].outcome == "ingested"

    rows = session.execute(select(Event)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "death"
    assert rows[0].primary_character_id == 1234
    assert rows[0].event_date == "1067.1.15"
    assert rows[0].event_date_iso == "1067-01-15"


def test_save_pair_persists_family_participants(session: Session) -> None:
    family = FamilySnapshot(mother=10, father=11, primary_spouse=20, children=(30,))
    prev = _snap({1234: _char(1234, is_dead=False, family=family)})
    curr = _snap({1234: _char(1234, is_dead=True, death_date="1067.1.15", family=family)})
    process_save_pair(prev, curr, session=session)
    session.commit()
    rows = session.execute(select(EventParticipant)).scalars().all()
    role_to_ids = {(r.role, r.character_id) for r in rows}
    assert ("mother", 10) in role_to_ids
    assert ("father", 11) in role_to_ids
    assert ("primary_spouse", 20) in role_to_ids
    assert ("child", 30) in role_to_ids


def test_save_pair_hydrates_new_participants_from_snapshot(session: Session) -> None:
    """ck3_chronicler-6gs: a participant referenced for the first time by a
    diff event must land in the characters table with first_name +
    birth_date populated from the current snapshot. Without this, the
    consolidation prompt sees ``{name: null, birth_date: null}`` and
    confabulates death narratives around the faceless ID."""
    from chronicler.db import Character

    # The newborn child is in `curr.characters` (the engine emits them in
    # `living` the moment they're born) but appears in the ingest only as
    # a participant on the parent's birth event.
    _char(1234, is_dead=False)
    child_snap = make_char(99999, first_name="Asbjorn", birth_date="1069.6.19", location_id=None)
    birth_mem = MemorySnapshot(
        memory_id=77,
        memory_type="memory_first_born",
        creation_date="1069.6.19",
        end_date=None,
        participants=(("child", 99999),),
    )
    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap({1234: _char(1234, is_dead=False, memories=(birth_mem,)), 99999: child_snap})
    process_save_pair(prev, curr, session=session)
    session.commit()

    child = session.get(Character, 99999)
    assert child is not None
    assert child.first_name == "Asbjorn"
    assert child.birth_date == "1069.6.19"
    assert child.death_date is None


def test_save_pair_emits_war_declared_then_concluded(session: Session) -> None:
    """ck3_chronicler-o7j: war events must flow through the diff +
    insert path end-to-end. Acceptance: a war that appears with the
    tracked character as the cb.attacker emits war_declared; when the
    war later vanishes from active_wars, war_concluded fires for the
    same tracked character."""
    from chronicler.save.parse import WarSnapshot

    war = WarSnapshot(
        war_id=67108864,
        name="War for Munster",
        start_date="1066.10.1",
        casus_belli_type="holy_war",
        targeted_titles=(1024,),
        primary_attacker_id=1234,
        primary_defender_id=999,
        claimant_id=None,
        attacker_participants=frozenset({1234}),
        defender_participants=frozenset({999}),
    )
    prev = _snap({1234: _char(1234)})
    declared = SaveSnapshot(
        playthrough_id=prev.playthrough_id,
        ck3_version=prev.ck3_version,
        bookmark_date=prev.bookmark_date,
        current_date="1067.1.1",
        player_character_id=1234,
        characters={1234: _char(1234)},
        wars={67108864: war},
        character_to_wars={1234: frozenset({67108864}), 999: frozenset({67108864})},
    )
    process_save_pair(prev, declared, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    war_rows = [r for r in rows if r.event_type.startswith("war_")]
    assert len(war_rows) == 1
    assert war_rows[0].event_type == "war_declared"
    import json

    payload = json.loads(war_rows[0].payload_json)
    assert payload["p"]["casus_belli_type"] == "holy_war"
    assert payload["p"]["side"] == "attacker"
    assert payload["p"]["primary_defender_id"] == 999

    # Tick forward: war is no longer in active_wars → war_concluded
    concluded = SaveSnapshot(
        playthrough_id=prev.playthrough_id,
        ck3_version=prev.ck3_version,
        bookmark_date=prev.bookmark_date,
        current_date="1068.6.1",
        player_character_id=1234,
        characters={1234: _char(1234)},
        wars={},
        character_to_wars={},
    )
    process_save_pair(declared, concluded, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    concluded_rows = [r for r in rows if r.event_type == "war_concluded"]
    assert len(concluded_rows) == 1
    payload = json.loads(concluded_rows[0].payload_json)
    assert payload["p"]["war_id"] == 67108864


def test_save_pair_does_not_clobber_existing_data_for_pruned_participant(
    session: Session,
) -> None:
    """A participant ID not present in the current snapshot (e.g. a pruned
    ancestor) gets a stub row, not an error — and any prior data on that
    row stays intact. Guards the ck3_chronicler-6gs hydration helper
    against accidentally wiping known fields with NULLs."""
    from chronicler.db import Character

    upsert_character(session, ck3_id=42, first_name="Already Known")
    session.commit()

    pruned_mem = MemorySnapshot(
        memory_id=88,
        memory_type="memory_grand_wedding",
        creation_date="1066.6.1",
        end_date=None,
        participants=(("witness", 42),),  # 42 not in curr.characters
    )
    prev = _snap({1234: _char(1234)})
    curr = _snap({1234: _char(1234, memories=(pruned_mem,))})
    process_save_pair(prev, curr, session=session)
    session.commit()

    pre_existing = session.get(Character, 42)
    assert pre_existing is not None
    assert pre_existing.first_name == "Already Known"


def test_save_pair_idempotent_replay(session: Session) -> None:
    """Re-running the same diff produces duplicates, not new rows."""
    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap({1234: _char(1234, is_dead=True, death_date="1067.1.15")})

    first = process_save_pair(prev, curr, session=session)
    session.commit()
    second = process_save_pair(prev, curr, session=session)
    session.commit()

    assert first[0].outcome == "ingested"
    assert second[0].outcome == "duplicate"
    rows = session.execute(select(Event)).scalars().all()
    assert len(rows) == 1


def test_vanilla_memory_event_persisted_with_payload(session: Session) -> None:
    mem = MemorySnapshot(
        memory_id=42,
        memory_type="memory_grand_wedding",
        creation_date="1066.6.1",
        end_date="1099.1.1",
        participants=(("spouse", 5678),),
    )
    prev = _snap({1234: _char(1234, memories=())})
    curr = _snap({1234: _char(1234, memories=(mem,))})
    process_save_pair(prev, curr, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    vm = [r for r in rows if r.event_type == "vanilla_memory"]
    assert len(vm) == 1
    assert vm[0].event_date == "1066.6.1"
    assert vm[0].event_date_iso == "1066-06-01"
    # Payload JSON should contain the memory_type
    import json

    payload = json.loads(vm[0].payload_json)
    assert payload["p"]["memory_type"] == "memory_grand_wedding"


def test_travel_event_persisted_with_iso_date(session: Session) -> None:
    prev = _snap({1234: _char(1234, location_id=100)}, date="1067.1.1")
    curr = _snap({1234: _char(1234, location_id=200)}, date="1067.2.1")
    process_save_pair(prev, curr, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    travel = [r for r in rows if r.event_type == "travel"]
    assert len(travel) == 1
    assert travel[0].event_date == "1067.2.1"
    assert travel[0].event_date_iso == "1067-02-01"


def test_chronological_order_preserved_in_repository(session: Session) -> None:
    """Events emitted via save-diff sort chronologically by event_date_iso."""
    m_old = MemorySnapshot(
        memory_id=1,
        memory_type="had_sex",
        creation_date="1050.1.1",
        end_date=None,
        participants=(),
    )
    m_new = MemorySnapshot(
        memory_id=2,
        memory_type="memory_won_battle",
        creation_date="1066.10.14",
        end_date=None,
        participants=(),
    )
    prev = _snap({1234: _char(1234, memories=())})
    curr = _snap({1234: _char(1234, memories=(m_new, m_old))})  # out of order
    process_save_pair(prev, curr, session=session)
    session.commit()

    events = list_events_for_character(session, 1234)
    iso_dates = [e.event_date_iso for e in events]
    assert iso_dates == sorted(iso_dates)


# --- biography scheduler integration ---
# --- ck3_chronicler-ek2: event bus publication during ingest ---
def test_process_save_pair_publishes_to_event_bus(session: Session) -> None:
    """Ingested events trigger bus.publish per row; duplicates are skipped."""
    from chronicler.api.events import EventBus

    bus = EventBus()
    received: list[dict] = []
    # Synchronously stash everything published — bypass async subscribe
    # so this stays a fast unit-style integration test.
    monkey_publish = bus.publish

    def _capture(campaign_id, event):
        received.append({"campaign_id": campaign_id, **event})
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    # Death of char 100 → one event ingested
    upsert_character(session, ck3_id=100)
    session.commit()
    prev = _snap({100: _char(100, is_dead=False)})
    curr = _snap({100: _char(100, is_dead=True, death_date="1067.1.15")})
    process_save_pair(prev, curr, session=session, event_bus=bus, bus_campaign_id="camp-1")
    session.commit()

    ingested = [e for e in received if e.get("kind") == "event_ingested"]
    assert len(ingested) == 1
    e = ingested[0]
    assert e["campaign_id"] == "camp-1"
    assert e["kind"] == "event_ingested"
    assert e["character_id"] == 100
    assert e["event_type"] == "death"
    assert e["event_date"] == "1067.1.15"
    assert isinstance(e["event_id"], int)


def test_process_save_pair_no_publish_without_bus(session: Session) -> None:
    """Calling process_save_pair without an event_bus is silent — no
    accidental publish to a nonexistent bus."""
    upsert_character(session, ck3_id=100)
    session.commit()
    prev = _snap({100: _char(100, is_dead=False)})
    curr = _snap({100: _char(100, is_dead=True, death_date="1067.1.15")})
    # Should not raise even with no bus
    results = process_save_pair(prev, curr, session=session)
    session.commit()
    assert len(results) == 1
    assert results[0].outcome == "ingested"


def test_process_save_pair_does_not_publish_duplicates(session: Session) -> None:
    """Re-ingesting the same diff (idempotency hits) skips the publish too."""
    from chronicler.api.events import EventBus

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(campaign_id, event):
        received.append(event)
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    upsert_character(session, ck3_id=100)
    session.commit()
    prev = _snap({100: _char(100, is_dead=False)})
    curr = _snap({100: _char(100, is_dead=True, death_date="1067.1.15")})

    process_save_pair(prev, curr, session=session, event_bus=bus, bus_campaign_id="camp-1")
    session.commit()
    process_save_pair(prev, curr, session=session, event_bus=bus, bus_campaign_id="camp-1")
    session.commit()

    # Only the first call should produce an event_ingested frame;
    # the second is a duplicate so only save_pair_completed frames remain.
    ingested = [e for e in received if e.get("kind") == "event_ingested"]
    assert len(ingested) == 1


# --- ck3_chronicler-bges: save_pair_completed end-of-tick frame ---
def test_process_save_pair_publishes_save_pair_completed(session: Session) -> None:
    """End of tick emits a save_pair_completed frame after the per-event frames."""
    from chronicler.api.events import EventBus

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(campaign_id: str, event: dict) -> None:
        received.append({"campaign_id": campaign_id, **event})
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    upsert_character(session, ck3_id=200)
    session.commit()
    prev = _snap({200: _char(200, is_dead=False)})
    curr = _snap({200: _char(200, is_dead=True, death_date="1067.1.15")})

    process_save_pair(
        prev,
        curr,
        session=session,
        save_path_name="autosave_test.ck3",
        event_bus=bus,
        bus_campaign_id="camp-bges-1",
    )

    completed = [e for e in received if e.get("kind") == "save_pair_completed"]
    assert len(completed) == 1
    frame = completed[0]
    assert frame["campaign_id"] == "camp-bges-1"
    assert frame["save_filename"] == "autosave_test.ck3"
    assert frame["in_game_date"] == curr.current_date
    assert frame["event_count"] == 1
    assert frame["event_type_tally"] == {"death": 1}
    assert isinstance(frame["completed_at"], str) and frame["completed_at"]


def test_save_pair_completed_fires_after_event_ingested(session: Session) -> None:
    """The completed frame is the LAST frame of the tick."""
    from chronicler.api.events import EventBus

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(campaign_id: str, event: dict) -> None:
        received.append({"campaign_id": campaign_id, **event})
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    upsert_character(session, ck3_id=201)
    session.commit()
    prev = _snap({201: _char(201, is_dead=False)})
    curr = _snap({201: _char(201, is_dead=True, death_date="1067.1.15")})

    process_save_pair(
        prev,
        curr,
        session=session,
        save_path_name="autosave_test.ck3",
        event_bus=bus,
        bus_campaign_id="camp-bges-2",
    )

    kinds = [e.get("kind") for e in received]
    assert kinds[-1] == "save_pair_completed"
    assert "event_ingested" in kinds[:-1]
    assert all(e["campaign_id"] == "camp-bges-2" for e in received)


def test_save_pair_completed_with_zero_events(session: Session) -> None:
    """Even when no events differ, we publish the completed frame."""
    from chronicler.api.events import EventBus

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(campaign_id: str, event: dict) -> None:
        received.append({"campaign_id": campaign_id, **event})
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    # Diffing a snapshot against itself yields zero events.
    snap = _snap({}, date="1066.9.15")

    process_save_pair(
        snap,
        snap,
        session=session,
        save_path_name="autosave_quiet.ck3",
        event_bus=bus,
        bus_campaign_id="camp-bges-3",
    )

    completed = [e for e in received if e.get("kind") == "save_pair_completed"]
    assert len(completed) == 1
    assert completed[0]["event_count"] == 0
    assert completed[0]["event_type_tally"] == {}
    assert all(e["campaign_id"] == "camp-bges-3" for e in received)


@pytest.mark.asyncio
async def test_death_event_schedules_biography(session_factory, session: Session) -> None:
    """A death event ingested via save-diff should fire the BiographyScheduler
    just like the debug_log path."""
    upsert_character(session, ck3_id=1234)
    session.commit()

    provider = _FakeProvider()
    scheduler = BiographyScheduler(session_factory, provider, is_tracked=lambda cid: cid == 1234)

    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap({1234: _char(1234, is_dead=True, death_date="1067.1.15")})

    process_save_pair(prev, curr, session=session, scheduler=scheduler)
    session.commit()

    await scheduler.drain()

    # Confirm a biography landed in the same DB
    with session_factory() as s2:
        bio = get_latest_biography_for_character(s2, 1234)
    assert bio is not None
    assert bio.body == "bio for 1234"


@pytest.mark.asyncio
async def test_untracked_death_skips_biography(session_factory, session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    session.commit()

    provider = _FakeProvider()
    scheduler = BiographyScheduler(session_factory, provider, is_tracked=lambda cid: False)

    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap({1234: _char(1234, is_dead=True, death_date="1067.1.15")})

    process_save_pair(prev, curr, session=session, scheduler=scheduler)
    session.commit()
    await scheduler.drain()

    with session_factory() as s2:
        rows = s2.execute(select(Biography)).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_startup_catchup_reschedules_deaths(
    session_factory, session: Session, tmp_path
) -> None:
    """ck3_chronicler-xy69: chronicler startup must re-schedule
    death-biography work that was lost when the previous process exited
    with tasks queued.

    Tracked character with death_date set but no biography row →
    biography is scheduled. Consolidation scheduling removed (plan:
    cozy-coalescing-shannon).

    The live save-tail path doesn't cover the death-bio case; the startup
    scan is the only safety net.
    """
    from chronicler.db.registry import add_tracked_character

    campaign_id = "xy69-test-campaign"
    registry_path = tmp_path / "registry.db"

    # Char 5678: tracked, dead, no biography.
    upsert_character(
        session,
        ck3_id=5678,
        first_name="DeadCandidate",
        death_date="1075.8.27",
    )
    # Char 9999: untracked, dead, no biography — should NOT be scheduled.
    upsert_character(
        session,
        ck3_id=9999,
        first_name="UntrackedDead",
        death_date="1075.9.1",
    )
    session.commit()

    add_tracked_character(campaign_id, 5678, note="t2", registry=registry_path)

    scheduled: list[tuple[int, str]] = []

    class _ProbeScheduler:
        """Captures schedule calls so we can assert the catch-up scan
        invoked the right kinds without running the full LLM pipeline."""

        def schedule(self, character_id: int) -> None:
            scheduled.append((character_id, "biography"))

    _startup_catchup_scan(
        factory=session_factory,
        scheduler=_ProbeScheduler(),  # type: ignore[arg-type]
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    assert (5678, "biography") in scheduled, (
        "tracked dead char with no biography must have a biography scheduled"
    )
    # Untracked dead must not appear.
    assert all(c != 9999 for c, _ in scheduled), (
        "untracked characters must not be scheduled even when dead"
    )


def test_default_save_pattern_matches_all_ck3_saves() -> None:
    """ck3_chronicler 2026-05-09: pattern broadened from autosave-only
    to *.ck3 so user-named manual saves (_start.ck3, Godwin_1066.ck3,
    numbered quicksaves, etc.) are watched too. Without this, anyone
    not relying on CK3's auto-save cadence saw 'tail isn't working'
    even though save-tail was running fine.

    The original rotation-backup risk (CK3 keeps autosave_1.ck3,
    autosave_2.ck3 as rolling backups, which have stale playthrough_ids
    and produced thousands of phantom events in v0.5) is now mitigated
    at runtime by:
      - the playthrough_id pin gate (different-playthrough backups dropped),
      - the forward-only date guard (same-playthrough older backups dropped).
    The pattern doesn't need to do this filtering itself anymore.
    """
    import fnmatch

    assert DEFAULT_SAVE_PATTERN == ("*.ck3",)
    # All save names — autosave variants, rolling backups, manual saves —
    # are matched by the glob. Filtering happens at the diff layer, not
    # the file-pattern layer.
    for name in (
        "autosave.ck3",
        "autosave_exit.ck3",
        "autosave_1.ck3",
        "autosave_2.ck3",
        "Godwin_1066.ck3",
        "_start.ck3",
        "000000000001.ck3",
        "quicksave.ck3",
    ):
        assert any(fnmatch.fnmatch(name, p) for p in DEFAULT_SAVE_PATTERN), name


def test_refresh_persists_save_snapshot_json_from_snapshot_extractions(
    session_factory,
) -> None:
    """ck3_chronicler-j86v: the save-tail refresh reads tracked extractions
    from the snapshot (resolved in the parse worker), NOT from a raw save
    dict. Calling _refresh_tracked_characters without a raw dict must still
    persist save_snapshot_json / coa_json when snap.tracked_* carry them."""
    import dataclasses
    import json

    from chronicler.db import Character
    from chronicler.save.tick import _refresh_tracked_characters

    base = _snap({7: _char(7)})
    snap = dataclasses.replace(
        base,
        tracked_raw_records={7: {"first_name": "Eadmund", "extra": 1}},
        tracked_coa={7: {"pattern": "solid.dds"}},
    )

    # New signature: no raw_save_data — extractions ride on the snapshot.
    _refresh_tracked_characters(snap=snap, tracked_set={7}, factory=session_factory)

    with session_factory() as s:
        char = s.get(Character, 7)
        assert char is not None
        assert char.save_snapshot_json is not None
        assert json.loads(char.save_snapshot_json)["extra"] == 1
        assert char.coa_json is not None
        assert json.loads(char.coa_json)["pattern"] == "solid.dds"
