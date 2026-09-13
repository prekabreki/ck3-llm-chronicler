"""Crash-recovery integration tests (ck3_chronicler-elll).

Validates the acceptance criterion: kill chronicler mid-tick → next
start replays the missed saves with no manual cleanup, no double-
counted events.

Runs in-process — monkeypatches ``save_baseline`` to raise so we get
deterministic "crash" timing without subprocess complexity. The events
table is the source of truth: it has the idempotency guard
(``insert_event_idempotent`` short-circuits on duplicate
``(schema_version, event_type, event_date, primary_character_id,
payload_json)``), and these tests assert the row count is stable across
the replay window.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from chronicler.db import (
    Base,
    Event,
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.repository import count_events
from chronicler.save.baseline import (
    baseline_path_for,
    load_baseline,
    save_baseline,
)
from chronicler.save.ingest import _advance_baseline
from chronicler.save.parse import (
    CharacterSnapshot,
    SaveSnapshot,
)
from tests.helpers.snapshots import make_char


@pytest.fixture
def factory(tmp_path: Path) -> Iterator:
    engine = make_engine_for_path(tmp_path / "campaign.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _char(
    cid: int,
    *,
    is_dead: bool = False,
    death_date: str | None = None,
) -> CharacterSnapshot:
    return make_char(cid, is_dead=is_dead, death_date=death_date)


def _snap(
    chars: dict[int, CharacterSnapshot],
    *,
    date: str = "1067.2.1",
    playthrough_id: str = "test-pid",
) -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id=playthrough_id,
        ck3_version="1.19.0",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=next(iter(chars), None),
        characters=chars,
    )


def test_mid_tick_crash_replay_does_not_double_count(
    tmp_path: Path, factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a crash between event ingest and baseline persist;
    confirm that restarting catches up cleanly with no duplicated rows.

    Sequence:

    1. Tick 1: persist baseline at snap_a (char 1234 alive).
    2. Tick 2: snap_b includes char 1234's death. ``_advance_baseline``
       inserts the death event into the DB, then poisoned
       ``save_baseline`` raises — the baseline on disk stays at gen=N.
       (``_persist_baseline_safely`` catches the OSError and logs.)
    3. Restart: load_baseline returns snap_a (the gen=N file). The startup
       backlog drain re-runs ``_advance_baseline`` on snap_b against snap_a
       (27ov.12 routes the seeded current save through the same kernel the
       live consumer uses). The death event lands again at
       ``insert_event_idempotent`` — but the dedup key matches the
       row already inserted in step 2, so the second insert is a
       no-op. Events table row count is unchanged. Baseline on disk
       now advances to gen=N+1 because the poisoned save_baseline is
       restored before the catch-up.

    The invariant under test: ``count_events`` is stable across the
    poisoned tick + the recovery pass. If insert_event_idempotent ever
    regressed (e.g. dropped a dedup column) the second pass would
    double the row count.
    """
    from chronicler.db.registry import add_tracked_character

    db_path = tmp_path / "campaign.db"
    persist_path = baseline_path_for(db_path)
    registry_path = tmp_path / "registry.db"
    campaign_id = "test-campaign-elll"

    # Track char 1234 so the diff filter retains its death event.
    add_tracked_character(campaign_id, 1234, note="test", registry=registry_path)

    snap_a = _snap({1234: _char(1234, is_dead=False)}, date="1066.9.15")
    snap_b = _snap(
        {1234: _char(1234, is_dead=True, death_date="1067.4.10")},
        date="1067.4.10",
    )

    # Tick 1: persist baseline. No events because we're seeding the
    # baseline, not diffing yet.
    save_baseline(persist_path, snap_a)
    loaded_after_first = load_baseline(persist_path)
    assert loaded_after_first is not None
    gen_after_first = loaded_after_first.generation
    assert gen_after_first == 1

    with factory() as session:
        events_after_tick1 = count_events(session)
    assert events_after_tick1 == 0

    # Tick 2 with poisoned persist: _advance_baseline inserts the death
    # event into the DB, then _persist_baseline_safely tries to write
    # the new baseline. Our poison makes save_baseline raise; the safely
    # wrapper catches and logs, so _advance_baseline returns normally
    # but the baseline on disk did NOT advance.
    # 27ov.40: _advance_baseline (and its _persist_baseline_safely ->
    # save_baseline call) moved to chronicler.save.tick, so the poison patch
    # targets tick.save_baseline, not ingest.
    import chronicler.save.tick as tick_module

    original_save_baseline = tick_module.save_baseline

    def _raise(path, snap):  # noqa: ANN001
        raise OSError("simulated mid-tick crash")

    monkeypatch.setattr(tick_module, "save_baseline", _raise)
    advanced = _advance_baseline(
        snap_b,
        last_snapshot=snap_a,
        factory=factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_id,
        save_path_name="autosave_b.ck3",
        persist_path=persist_path,
        raw_save_data=None,
        event_bus=None,
    )
    # advance returned the new snap (in-memory baseline advanced).
    assert advanced is snap_b

    # Death event landed in the DB despite the poisoned baseline persist.
    with factory() as session:
        events_after_tick2 = count_events(session)
        rows = session.execute(select(Event)).scalars().all()
    assert events_after_tick2 == 1, (
        "death event should have been inserted before the baseline crash"
    )
    assert rows[0].event_type == "death"
    assert rows[0].primary_character_id == 1234
    assert rows[0].event_date == "1067.4.10"

    # Baseline on disk did NOT advance: gen stays at 1.
    loaded_after_crash = load_baseline(persist_path)
    assert loaded_after_crash is not None
    assert loaded_after_crash.generation == gen_after_first
    assert loaded_after_crash.snapshot.current_date == snap_a.current_date

    # Restore the real save_baseline and simulate a restart by re-running
    # _advance_baseline on the on-disk save against the stale baseline — what
    # the startup backlog drain (27ov.12) does through the live consumer when
    # it finds a persisted baseline older than the latest on-disk save.
    monkeypatch.setattr(tick_module, "save_baseline", original_save_baseline)
    recovered_snap = _advance_baseline(
        snap_b,
        last_snapshot=loaded_after_crash.snapshot,
        factory=factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_id,
        save_path_name="autosave_b.ck3",
        persist_path=persist_path,
        raw_save_data=None,
        event_bus=None,
    )
    assert recovered_snap is not None
    assert recovered_snap.current_date == snap_b.current_date

    # Idempotency: no new event rows landed during the catch-up.
    # insert_event_idempotent dedup'd the second insert.
    with factory() as session:
        events_after_recovery = count_events(session)
    assert events_after_recovery == events_after_tick2, (
        "catch-up must not double-count events that were already in the DB"
    )

    # Baseline on disk has now advanced past the crash window.
    loaded_after_recovery = load_baseline(persist_path)
    assert loaded_after_recovery is not None
    assert loaded_after_recovery.generation is not None
    assert loaded_after_recovery.generation > gen_after_first
    assert loaded_after_recovery.snapshot.current_date == snap_b.current_date


@pytest.mark.asyncio
async def test_recovery_sse_frames_published_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real run_save_ingest startup with a surviving persisted
    baseline publishes chronicler_recovering before chronicler_recovered
    on the campaign channel, exactly once each.

    M-T4 (27ov.70): the previous version of this test published the
    frames itself and asserted the order it had published —
    run_save_ingest was never invoked. Now the frames are captured from
    the real bus while the real startup path runs (watcher no-op'd, the
    test_save_ingest.py pattern)."""
    from chronicler.api.events import EventBus
    from chronicler.save.ingest import run_save_ingest
    from tests.helpers.ingest import _no_op_watch, _patch_parse_save

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")

    db_path = tmp_path / "frames-campaign.db"
    registry_path = tmp_path / "registry.db"
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-frames"

    # Persisted baseline whose playthrough matches the on-disk save so
    # _select_starting_baseline keeps it — the gate that triggers the
    # recovering frame.
    persist_path = baseline_path_for(db_path)
    save_baseline(persist_path, _snap({1: _char(1)}, date="867.4.12"))

    # The on-disk save parses to a later date in the same playthrough.
    snap_current = _snap({1: _char(1)}, date="867.6.1")
    _patch_parse_save(monkeypatch, lambda path: ({}, snap_current))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    bus = EventBus()
    queue, unsubscribe = bus.register(campaign_id)
    try:
        await run_save_ingest(
            save_dir=save_dir,
            db_path=db_path,
            campaign_id=campaign_id,
            registry_path=registry_path,
            event_bus=bus,
        )
        captured: list[dict] = []
        while queue.qsize() > 0:
            captured.append(queue.get_nowait())
    finally:
        unsubscribe()

    kinds = [f["kind"] for f in captured]
    assert kinds.count("chronicler_recovering") == 1
    assert kinds.count("chronicler_recovered") == 1
    assert kinds.index("chronicler_recovering") < kinds.index("chronicler_recovered")

    # Wire contract of the opening frame: forensic baseline fields the
    # AppShell banner renders.
    recovering = captured[kinds.index("chronicler_recovering")]
    assert recovering["baseline_date"] == "867.4.12"
    assert recovering["pending_cache_count"] == 0
    assert "baseline_persisted_at" in recovering
    assert "baseline_generation" in recovering


def test_auto_track_new_candidates_is_idempotent_under_replay(
    tmp_path: Path,
) -> None:
    """Calling ``add_tracked_character`` twice with the same args must
    leave exactly one row per (campaign, character) — no duplicate-key
    crashes, no row count growth on the second call. Concrete proof of
    the audit's "add_tracked_character is ON CONFLICT DO UPDATE" claim,
    which is what makes _auto_track_new_candidates safe to replay
    during catch-up."""
    from chronicler.db.registry import (
        add_tracked_character,
        get_tracked_character_ids,
    )

    registry = tmp_path / "registry.db"
    add_tracked_character("test-campaign", 1234, note="player", registry=registry)
    add_tracked_character("test-campaign", 1234, note="player", registry=registry)
    add_tracked_character("test-campaign", 5678, note="spouse", registry=registry)

    tracked = get_tracked_character_ids("test-campaign", registry=registry)
    # get_tracked_character_ids returns a set[int]; the order assertion
    # via sorted() preserves test readability either way.
    assert sorted(tracked) == [1234, 5678]


def test_death_replay_does_not_double_schedule_biography(tmp_path: Path, factory) -> None:
    """A death event re-applied through _ingest_diff_event after the
    original was already inserted must NOT schedule a second biography.
    The gate is that insert_event_idempotent returns event_id=None on
    the second call, and the scheduler is only fired when event_id is
    not None (see _ingest_diff_event around the early-return on
    ``if event_id is None``).
    """
    from chronicler.db.repository import upsert_character
    from chronicler.save.diff import DiffEvent
    from chronicler.save.ingest import _ingest_diff_event
    from chronicler.schema.events import DeathEvent, DeathPayload

    death_event = DeathEvent(
        v=1,
        t="death",
        d="867.4.12",
        c=1234,
        p=DeathPayload(),
    )
    diff = DiffEvent(event=death_event, participants=())

    second_call_scheduler_invocations: list[int] = []

    class _FirstScheduler:
        """First-call scheduler — drops the schedule on a list to
        confirm the first ingest DID fire the biography schedule.
        """

        def __init__(self) -> None:
            self.calls: list[int] = []

        def schedule(self, character_id: int) -> None:
            self.calls.append(character_id)

    class _SecondScheduler:
        """Second-call scheduler — drops on a closure list so the
        outer-scope assertion can confirm no extra schedule fired."""

        def schedule(self, character_id: int) -> None:
            second_call_scheduler_invocations.append(character_id)

    with factory() as session:
        upsert_character(session, ck3_id=1234)
        session.commit()

    first_scheduler = _FirstScheduler()
    with factory() as session:
        first = _ingest_diff_event(
            diff,
            session=session,
            save_path_name="autosave.ck3",
            snap=None,
            scheduler=first_scheduler,  # type: ignore[arg-type]
        )
        session.commit()
    assert first.outcome == "ingested"
    assert first_scheduler.calls == [1234], (
        "first ingest should have scheduled the biography exactly once"
    )

    with factory() as session:
        second = _ingest_diff_event(
            diff,
            session=session,
            save_path_name="autosave.ck3",
            snap=None,
            scheduler=_SecondScheduler(),  # type: ignore[arg-type]
        )
        session.commit()
    assert second.outcome == "duplicate", (
        "replay of the same death event must dedup at insert_event_idempotent"
    )
    assert second_call_scheduler_invocations == [], (
        "duplicate ingest must NOT invoke the scheduler — that would "
        "schedule the same biography twice on every save-tail catch-up"
    )
