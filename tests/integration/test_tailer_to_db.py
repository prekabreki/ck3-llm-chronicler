"""End-to-end tailer pipeline test (CHRONICLER + scope-dump pairing)."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chronicler.db import Base, Event, make_engine_for_path, make_session_factory
from chronicler.db.registry import create_campaign, get_tail_offset
from chronicler.tailer.ingest import process_lines, run_ingest

ENGINE_PREFIX = "[18:54:27][D][effectimpl.cpp:1110]: "


def _tagged(body: str) -> str:
    return f"{ENGINE_PREFIX}CHRONICLER|{body}"


def _scope(name: str, ck3_id: int) -> str:
    return (
        f"{ENGINE_PREFIX}{name} (Internal ID: {ck3_id} - Historical ID 5) "
        f"weak (Character - {ck3_id})!"
    )


# A simulated debug.log capturing three deaths (1234 with killer/cause, 2222
# minimal, 4444 natural) plus a duplicate of 1234, an unknown event type, and
# a malformed line. Each CHRONICLER line is followed by an Internal ID dump
# the way debug_log_scopes = yes produces in real CK3.
FIXTURE_LINES = [
    f"{ENGINE_PREFIX}engine trace, no chronicler tag",
    f"{ENGINE_PREFIX}OTHER_MOD|some other mod",
    _tagged("v=1|t=death|d=1066.10.14|killer=5678|cause=battle"),
    _scope("Harold", 1234),
    _tagged("v=1|t=death|d=1067.01.05"),
    _scope("Other", 2222),
    _tagged("v=1|t=death|d=1066.10.14|garbage_segment_no_equals"),
    # Malformed line is rejected at envelope-build time; pending isn't started.
    _tagged("v=1|t=death|d=1066.10.14|killer=5678|cause=battle"),
    _scope("Harold", 1234),  # duplicate of first
    _tagged("v=1|t=smarch_horror|d=1066.06.06"),
    _scope("Nope", 3333),  # schema_violation: unknown event type
    "",
    _tagged("v=1|t=death|d=1068.03.10|cause=natural"),
    _scope("Natural", 4444),
    f"{ENGINE_PREFIX}another engine trace",
]


@pytest.fixture
def session(tmp_path: Path):
    engine = make_engine_for_path(tmp_path / "campaign.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def test_process_lines_classifies_each_outcome(session: Session) -> None:
    results = process_lines(FIXTURE_LINES, session=session)
    outcomes = Counter(r.outcome for r in results)
    # Every line maps to either an outcome or 'skipped'. CHRONICLER lines
    # plus the scope-dump line each pair produce one ingested/duplicate/
    # quarantined result; everything else is skipped.
    assert outcomes["ingested"] == 3
    assert outcomes["duplicate"] == 1
    assert outcomes["quarantined"] == 2  # invalid format + unknown 't'


def test_process_lines_persists_three_unique_events(session: Session) -> None:
    process_lines(FIXTURE_LINES, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    assert len(rows) == 3
    assert {r.primary_character_id for r in rows} == {1234, 2222, 4444}


def test_idempotent_replay(session: Session) -> None:
    """Re-processing the same fixture yields zero new events."""
    process_lines(FIXTURE_LINES, session=session)
    session.commit()
    second_pass = process_lines(FIXTURE_LINES, session=session)
    session.commit()
    outcomes = Counter(r.outcome for r in second_pass)
    assert outcomes["ingested"] == 0
    assert outcomes["duplicate"] == 4


def test_quarantine_table_populated(session: Session) -> None:
    from chronicler.db import Quarantine

    process_lines(FIXTURE_LINES, session=session)
    session.commit()
    rows = session.execute(select(Quarantine)).scalars().all()
    assert len(rows) == 2


# --- ck3_chronicler-fkw: quarantine.event_kind + suppression hook ---


def test_quarantine_records_event_kind_when_known(session: Session) -> None:
    """The two fixture failures: an invalid_format (no recoverable kind)
    and a schema_violation with t=smarch_horror (kind preserved)."""
    from chronicler.db import Quarantine

    process_lines(FIXTURE_LINES, session=session)
    session.commit()
    rows = session.execute(select(Quarantine).order_by(Quarantine.id)).scalars().all()
    kinds = {r.event_kind for r in rows}
    # The two fixture failures: invalid_format (kind None) and
    # schema_violation with t=smarch_horror (kind preserved).
    assert kinds == {None, "smarch_horror"}


def test_suppression_drops_failure_silently(session: Session) -> None:
    """When is_kind_suppressed returns True for the failing kind, no
    quarantine row is written and the outcome flips to 'suppressed'.

    Suppress 'smarch_horror' — only the schema_violation row gets
    silenced; the kindless invalid_format row still reaches the table."""
    from chronicler.db import Quarantine

    suppressed_kinds: set[str] = {"smarch_horror"}
    results = process_lines(
        FIXTURE_LINES,
        session=session,
        is_kind_suppressed=lambda k: k is not None and k in suppressed_kinds,
    )
    session.commit()
    rows = session.execute(select(Quarantine)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_kind is None  # the invalid_format one
    suppressed = [r for r in results if r.outcome == "suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0].suppressed_kind == "smarch_horror"


def test_suppression_skips_unknown_kinds(session: Session) -> None:
    """Suppressing 'death' only — failures with no recoverable kind still
    reach the quarantine table."""
    from chronicler.db import Quarantine

    invalid_format_line = "[18:54:27][D][effectimpl.cpp:1110]: CHRONICLER|garbage_no_equals_signs"
    process_lines(
        [invalid_format_line],
        session=session,
        is_kind_suppressed=lambda k: k == "death",
    )
    session.commit()
    rows = session.execute(select(Quarantine)).scalars().all()
    # invalid_format failure has event_kind=None which suppression always
    # lets through, so the row IS persisted
    assert len(rows) == 1
    assert rows[0].event_kind is None


def test_event_participants_recorded(session: Session) -> None:
    from chronicler.db import EventParticipant

    process_lines(FIXTURE_LINES, session=session)
    session.commit()
    rows = session.execute(select(EventParticipant)).scalars().all()
    assert len(rows) == 1
    assert rows[0].character_id == 5678
    assert rows[0].role == "killer"


def test_event_date_iso_populated_from_long_form_date(session: Session) -> None:
    """V02-N02 / 7pe: real CK3 dates are long-form English; the ingest
    layer should compute the ISO normalisation and persist both."""
    from chronicler.db import Event

    real_format = [
        _tagged("v=1|t=death|d=16th of September, 1066 AD"),
        _scope("William de Normandie", 32134),
        f"{ENGINE_PREFIX}terminator",
    ]
    process_lines(real_format, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_date == "16th of September, 1066 AD"
    assert rows[0].event_date_iso == "1066-09-16"


def test_event_date_iso_null_when_unparseable(session: Session) -> None:
    """If the date string doesn't match the long-form pattern (e.g. an
    earlier wire-format leftover in YYYY.M.D), event_date_iso stays NULL
    rather than blocking ingest."""
    from chronicler.db import Event

    # YYYY.M.D format from a hypothetical prior version
    legacy = [
        _tagged("v=1|t=death|d=1066.10.14"),
        _scope("Harold", 31175),
        f"{ENGINE_PREFIX}terminator",
    ]
    process_lines(legacy, session=session)
    session.commit()
    rows = session.execute(select(Event)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_date == "1066.10.14"
    assert rows[0].event_date_iso is None


def test_events_ordered_chronologically_by_iso(session: Session) -> None:
    """list_events_for_character orders by event_date_iso so multi-year
    histories come back in chronological order — the v0.2 biography prompt
    needs this ordering for proper narrative arc."""
    from chronicler.db.repository import list_events_for_character

    # Insert in non-chronological order
    out_of_order = [
        _tagged("v=1|t=death|d=16th of September, 1100 AD"),
        _scope("late death", 9999),
        _tagged("v=1|t=death|d=1st of January, 1066 AD"),
        _scope("early death", 9999),  # same character, earlier date
        f"{ENGINE_PREFIX}terminator",
    ]
    process_lines(out_of_order, session=session)
    session.commit()
    rows = list_events_for_character(session, 9999)
    assert [e.event_date_iso for e in rows] == ["1066-01-01", "1100-09-16"]


def test_scope_dump_participants_persisted_to_db(session: Session) -> None:
    """V02-P01: a CHRONICLER event with a Saved event targets section
    produces event_participants rows for each named scope."""
    from chronicler.db import EventParticipant

    rich_dump = [
        _tagged("v=1|t=death|d=1066.10.14|cause=natural"),
        _scope("Harold (root)", 7777),
        "Root: Harold (root) (Internal ID: 7777) weak (Character - 7777)!",
        "",
        "Saved event targets:",
        "surviving_consort: Edith (Internal ID: 8888) weak (Character - 8888)!",
        "dead_character: Harold (Internal ID: 7777) weak (Character - 7777)!",
        "old_house_head: Harold (Internal ID: 7777) weak (Character - 7777)!",
        "",
        "Saved list targets:",
        "death_witnesses:",
        "Witness1 (Internal ID: 9001) weak (Character - 9001)!",
        "Witness2 (Internal ID: 9002) weak (Character - 9002)!",
        f"{ENGINE_PREFIX}next event terminates the dump",
    ]
    process_lines(rich_dump, session=session)
    session.commit()
    rows = session.execute(select(EventParticipant)).scalars().all()

    # Expect: surviving_consort=8888, dead_character=7777, old_house_head=7777,
    # death_witnesses=9001, death_witnesses=9002. 5 rows total.
    role_to_ids = {(r.role, r.character_id) for r in rows}
    assert ("surviving_consort", 8888) in role_to_ids
    assert ("dead_character", 7777) in role_to_ids
    assert ("old_house_head", 7777) in role_to_ids
    assert ("death_witnesses", 9001) in role_to_ids
    assert ("death_witnesses", 9002) in role_to_ids


async def test_run_ingest_triggers_biography_on_death(tmp_path: Path) -> None:
    """V02-N04: a death event ingested via run_ingest produces both an
    events row and a biographies row from the auto-trigger."""
    from chronicler.db import Biography

    log_path = tmp_path / "debug.log"
    log_path.write_bytes(b"")
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign = create_campaign("test", db_path=str(db_path), registry=registry_path)
    # Opt William in for biography generation — without this the scheduler
    # skips him (default: only tracked characters get biographies).
    from chronicler.db.registry import add_tracked_character

    add_tracked_character(campaign.id, 32134, note="test", registry=registry_path)
    stop = asyncio.Event()

    body = (
        f"{ENGINE_PREFIX}CHRONICLER|v=1|t=death|d=16th of September, 1066 AD\n"
        f"{ENGINE_PREFIX}William (Internal ID: 32134) weak (Character - 32134)!\n"
        f"{ENGINE_PREFIX}terminator engine line\n"
    )

    from tests.helpers.providers import RecordingProvider

    provider = RecordingProvider(
        provider_name="fake-int:v1",
        response_text="A short integration biography.",
        input_tokens=10,
        output_tokens=20,
        latency_ms=1,
    )

    async def writer() -> None:
        await asyncio.sleep(0.5)
        with log_path.open("ab") as fh:
            fh.write(body.encode("utf-8"))
        # Wait for the tail offset to advance past our written bytes
        for _ in range(50):
            if get_tail_offset(campaign.id, registry=registry_path) >= len(body):
                break
            await asyncio.sleep(0.2)
        stop.set()

    ingest_task = asyncio.create_task(
        run_ingest(
            log_path=log_path,
            db_path=db_path,
            campaign_id=campaign.id,
            registry_path=registry_path,
            stop_event=stop,
            biography_provider=provider,
        )
    )
    await writer()
    await asyncio.wait_for(ingest_task, timeout=10)

    # Both an event row AND a biography row should exist.
    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    with factory() as s:
        events = s.execute(select(Event)).scalars().all()
        bios = s.execute(select(Biography)).scalars().all()
    engine.dispose()
    assert len(events) == 1
    assert events[0].primary_character_id == 32134
    assert len(bios) == 1
    assert bios[0].character_id == 32134
    assert bios[0].body == "A short integration biography."
    assert bios[0].provider == "fake-int:v1"


async def test_run_ingest_processes_appended_lines(tmp_path: Path) -> None:
    """Async smoke: append CHRONICLER + scope dump while run_ingest runs."""
    log_path = tmp_path / "debug.log"
    log_path.write_bytes(b"")
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign = create_campaign("test", db_path=str(db_path), registry=registry_path)
    stop = asyncio.Event()

    body = (
        f"{ENGINE_PREFIX}CHRONICLER|v=1|t=death|d=1066.10.14|cause=battle\n"
        f"{ENGINE_PREFIX}Harold (Internal ID: 1234 - Historical ID 5) weak (Character - 1234)!\n"
    )

    async def writer() -> None:
        await asyncio.sleep(0.5)
        with log_path.open("ab") as fh:
            fh.write(body.encode("utf-8"))
        for _ in range(50):
            if get_tail_offset(campaign.id, registry=registry_path) >= len(body):
                break
            await asyncio.sleep(0.2)
        stop.set()

    ingest_task = asyncio.create_task(
        run_ingest(
            log_path=log_path,
            db_path=db_path,
            campaign_id=campaign.id,
            registry_path=registry_path,
            stop_event=stop,
        )
    )
    await writer()
    await asyncio.wait_for(ingest_task, timeout=10)

    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    with factory() as s:
        rows = s.execute(select(Event)).scalars().all()
    engine.dispose()
    assert len(rows) == 1
    assert rows[0].primary_character_id == 1234
    assert get_tail_offset(campaign.id, registry=registry_path) == len(body)
