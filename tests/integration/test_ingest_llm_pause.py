"""Global LLM-pause gating: deaths persist but do not schedule while
paused, schedule normally when unpaused, and drain pending
biographies after unpause.

Split from the test_save_ingest monolith (ck3_chronicler-27ov.72)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chronicler.db import (
    Event,
)
from chronicler.db.repository import (
    upsert_character,
)
from chronicler.narrative.pause import (
    _reset_llm_paused_cache,
    drain_for_campaign,
)
from chronicler.narrative.provider import NarrativeProvider
from chronicler.narrative.scheduler import NarrativeScheduler
from chronicler.save.ingest import (
    process_save_pair,
)
from tests.helpers.ingest import (
    _char,
    _snap,
)


# --- ck3_chronicler-gx7b: global LLM pause + drain ---
class _RecordingScheduler:
    """Captures schedule() calls without running any LLM work. Used by
    the pause-gate tests to assert the schedule calls are gated correctly
    under llm_paused settings.
    """

    def __init__(self) -> None:
        self.biographies: list[int] = []
        # The drain reads scheduler._queue_state to count what landed.
        # None is safe — _queue_size handles it.
        self._queue_state = None
        # drain_for_campaign reads scheduler._factory to call the SQL
        # helpers. Filled in by the test fixture per call.
        self._factory = None

    def schedule(self, character_id: int) -> None:
        self.biographies.append(character_id)


def _write_llm_pause_settings(tmp_path: Path, *, paused: bool) -> None:
    """Point settings_store at a tmp settings.json so the test's pause
    flag is isolated. ``_is_llm_paused`` reads via load_settings(path=
    DEFAULT_SETTINGS_PATH) — we monkeypatch the path so the test
    doesn't touch the user's real settings file.
    """
    settings_path = tmp_path / "settings.json"
    if paused:
        settings_path.write_text('{"llm_paused": true}', encoding="utf-8")
    else:
        settings_path.write_text("{}", encoding="utf-8")


def test_death_event_while_llm_paused_persists_but_does_not_schedule(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pause toggle must NOT block ingest — only narrative scheduling.
    A death lands in the events table normally; only the biography
    schedule call is short-circuited."""
    from chronicler import settings_store

    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH", tmp_path / "settings.json")
    _write_llm_pause_settings(tmp_path, paused=True)
    _reset_llm_paused_cache()

    sched = _RecordingScheduler()
    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap(
        {1234: _char(1234, is_dead=True, death_date="1067.1.15")},
        date="1067.2.1",
    )
    results = process_save_pair(prev, curr, session=session, scheduler=sched)  # type: ignore[arg-type]
    session.commit()

    # Death event landed in the DB regardless of pause state.
    assert len(results) == 1
    assert results[0].outcome == "ingested"
    rows = session.execute(select(Event)).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "death"
    assert rows[0].primary_character_id == 1234
    # But the biography schedule was NOT called.
    assert sched.biographies == [], "biography schedule must be short-circuited while LLM is paused"


def test_death_event_while_llm_unpaused_schedules_normally(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity counterpart: with the pause flag OFF, the death event
    schedules a biography. Cross-check that the gate is what's blocking
    in the paused test, not something unrelated."""
    from chronicler import settings_store

    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH", tmp_path / "settings.json")
    _write_llm_pause_settings(tmp_path, paused=False)
    _reset_llm_paused_cache()

    sched = _RecordingScheduler()
    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap(
        {1234: _char(1234, is_dead=True, death_date="1067.1.15")},
        date="1067.2.1",
    )
    process_save_pair(prev, curr, session=session, scheduler=sched)  # type: ignore[arg-type]
    session.commit()

    assert sched.biographies == [1234]


@pytest.mark.asyncio
async def test_drain_after_unpause_schedules_pending_biographies(
    session_factory, session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end shape of the unpause flow: pause is flipped OFF (by
    writing settings + resetting cache before the drain call); drain
    re-enumerates tracked characters and schedules death biographies.

    Consolidation scheduling removed (plan: cozy-coalescing-shannon).
    """
    from chronicler import settings_store
    from chronicler.db.registry import add_tracked_character

    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH", tmp_path / "settings.json")
    _write_llm_pause_settings(tmp_path, paused=False)
    _reset_llm_paused_cache()

    campaign_id = "drain-test-campaign"
    registry_path = tmp_path / "registry.db"

    # Tracked char 5678: dead, no biography.
    upsert_character(
        session,
        ck3_id=5678,
        first_name="DeadNoBio",
        death_date="1075.8.27",
    )
    session.commit()

    add_tracked_character(campaign_id, 5678, note="t2", registry=registry_path)

    sched = _RecordingScheduler()
    report = drain_for_campaign(
        factory=session_factory,
        scheduler=sched,  # type: ignore[arg-type]
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    assert 5678 in sched.biographies
    assert isinstance(report.biographies_scheduled, int)


def test_scheduler_global_pause_gate_blocks_schedule(monkeypatch) -> None:
    """ck3_chronicler-gx7b: scheduler's defense-in-depth gate. Even if a
    code path bypasses the ingest call-site short-circuit, schedule()
    must still no-op when is_globally_paused() returns True."""

    class _Stub(NarrativeProvider):
        # Issue #46: subclasses the ABC so it inherits max_concurrent (the
        # scheduler now reads the width off the provider). A bare object
        # here was only ever passing because the width was hardcoded.
        @property
        def name(self) -> str:
            return "stub"

        async def generate(self, req):
            raise NotImplementedError

    pause_state = [True]
    sched = NarrativeScheduler(
        factory=lambda: None,
        provider=_Stub(),
        is_globally_paused=lambda: pause_state[0],
    )
    # No running loop, but the gate runs BEFORE asyncio.get_running_loop()
    # in _spawn, so calling schedule() in a sync test still exercises the
    # gate path. The expected outcome is "logged + early return" — no
    # crash and no task created.
    sched.schedule(character_id=1234)
    assert sched._tasks == set(), "no task should be spawned while globally paused"
