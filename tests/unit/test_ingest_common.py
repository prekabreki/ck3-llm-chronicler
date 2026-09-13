"""ck3_chronicler-ejfa: the one ingest_event() seam.

Both transports (save-diff + debug_log) now route through
:func:`chronicler.ingest_common.ingest_event`. These tests lock the
seam contract directly — the steps that must not drift between the two
paths: primary + participant upsert, idempotent insert, duplicate
handling, the death-schedule gate, and the post-insert hook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import get_character, upsert_character
from chronicler.ingest_common import ingest_event
from chronicler.schema import DeathEvent, DeathPayload


@pytest.fixture
def session(tmp_path: Path):
    engine = make_engine_for_path(tmp_path / "seam.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


class _RecordingScheduler:
    def __init__(self) -> None:
        self.scheduled: list[int] = []

    def schedule(self, ck3_id: int) -> None:
        self.scheduled.append(ck3_id)


def _death(ck3_id: int = 100, killer: int | None = 200) -> DeathEvent:
    return DeathEvent(v=1, t="death", d="1066.9.15", c=ck3_id, p=DeathPayload(killer=killer))


def _real_upsert(session):
    """An upsert_character_fn that actually persists the row, as both
    real transports' fns do (events FK primary_character_id → characters)."""

    def _fn(ck3_id: int) -> None:
        upsert_character(session, ck3_id=ck3_id)

    return _fn


def test_ingest_event_upserts_primary_and_participants(session) -> None:
    """Primary + every payload/scope participant get hydrated via the
    supplied upsert_character_fn."""
    seen: list[int] = []
    persist = _real_upsert(session)

    def _record(cid: int) -> None:
        seen.append(cid)
        persist(cid)

    ingest_event(
        _death(ck3_id=100, killer=200),
        session=session,
        event_date_iso="1066-09-15",
        wall_clock_at="2026-05-31T00:00:00",
        raw_line="t=death c=100",
        upsert_character_fn=_record,
        scope_participants=[(300, "liege")],
    )
    # primary 100, scope 300, payload killer 200 all hydrated.
    assert set(seen) == {100, 200, 300}
    assert get_character(session, 300) is not None


def test_ingest_event_duplicate_returns_none(session) -> None:
    """A second identical event hits the payload_json UNIQUE and returns
    None so the caller can report 'duplicate' without re-scheduling."""
    args = dict(
        session=session,
        event_date_iso="1066-09-15",
        wall_clock_at="2026-05-31T00:00:00",
        raw_line="t=death c=100",
        upsert_character_fn=_real_upsert(session),
    )
    first = ingest_event(_death(), **args)
    second = ingest_event(_death(), **args)
    assert isinstance(first, int)
    assert second is None


def test_ingest_event_schedules_biography_on_death(session) -> None:
    sched = _RecordingScheduler()
    ingest_event(
        _death(ck3_id=100),
        session=session,
        event_date_iso="1066-09-15",
        wall_clock_at="2026-05-31T00:00:00",
        raw_line="t=death c=100",
        upsert_character_fn=_real_upsert(session),
        scheduler=sched,
    )
    assert sched.scheduled == [100]


def test_ingest_event_death_schedule_gated_off(session) -> None:
    """should_schedule_death=False (the save path's LLM-pause gate) skips
    the schedule and fires on_death_schedule_skipped instead."""
    sched = _RecordingScheduler()
    skipped: list[int] = []
    ingest_event(
        _death(ck3_id=100),
        session=session,
        event_date_iso="1066-09-15",
        wall_clock_at="2026-05-31T00:00:00",
        raw_line="t=death c=100",
        upsert_character_fn=_real_upsert(session),
        scheduler=sched,
        should_schedule_death=lambda: False,
        on_death_schedule_skipped=skipped.append,
    )
    assert sched.scheduled == []
    assert skipped == [100]


def test_ingest_event_on_ingested_hook_fires_with_event_id(session) -> None:
    """The save path's SSE publish rides on_ingested; it gets the new
    event_id and does not fire on a duplicate."""
    published: list[int] = []
    args = dict(
        session=session,
        event_date_iso="1066-09-15",
        wall_clock_at="2026-05-31T00:00:00",
        raw_line="t=death c=100",
        upsert_character_fn=_real_upsert(session),
        on_ingested=published.append,
    )
    event_id = ingest_event(_death(), **args)
    ingest_event(_death(), **args)  # duplicate — must not re-publish
    assert published == [event_id]
