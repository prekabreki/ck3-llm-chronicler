"""run_save_ingest startup: persisted-baseline gap catch-up, auto-import
on fresh/pre-armed DBs, pending-cache drain at bootstrap, and
cache-clear on playthrough mismatch.

Split from the test_save_ingest monolith (ck3_chronicler-27ov.72)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from chronicler.db import (
    Base,
    Event,
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.repository import (
    insert_event_idempotent,
    upsert_character,
)
from chronicler.save.baseline import baseline_path_for, load_baseline, save_baseline
from chronicler.save.ingest import (
    run_save_ingest,
)
from chronicler.save.parse import (
    SaveSnapshot,
)
from tests.helpers.ingest import (
    _char,
    _no_op_watch,
    _patch_parse_save,
)


# --- ck3_chronicler-96m: persisted-baseline gap catch-up ---
@pytest.mark.asyncio
async def test_startup_loads_persisted_baseline_and_catches_up_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance test from ck3_chronicler-96m:

    Persisted baseline at T1 → on-disk autosave at T2 = T1+10 months →
    restart save-tail → baseline loaded from disk → first diff catches
    the 10 months of state changes.

    A death between T1 and T2 must materialise in the events table on
    startup, not be silently rebaselined away.
    """
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    save_file = save_dir / "autosave.ck3"
    save_file.write_bytes(b"opaque save content")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    # Initialise the campaign DB schema so insert_event_idempotent can
    # write to the events table.
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-96m"
    playthrough = "uuid-A"

    # Mark character 1234 as tracked so the diff filter retains the
    # death event we expect to catch.
    from chronicler.db.registry import add_tracked_character

    add_tracked_character(campaign_id, 1234, note="test", registry=registry_path)

    # Persisted baseline at T1: char 1234 alive.
    snap_t1 = SaveSnapshot(
        playthrough_id=playthrough,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    baseline_file = baseline_path_for(db_path)
    save_baseline(baseline_file, snap_t1)

    # On-disk autosave at T2 = T1 + 10 months: char 1234 has died.
    snap_t2 = SaveSnapshot(
        playthrough_id=playthrough,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.7.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1067.4.10")},
    )

    # Stub out rakaly so we don't need a real .ck3 fixture: any call to
    # _parse_save_at_with_raw returns ({}, T2 snapshot). The empty raw
    # dict is fine — extract_character_record returns None, so no
    # save_snapshot_json is persisted, which is correct for this test.
    _patch_parse_save(monkeypatch, lambda path: ({}, snap_t2))
    # Bypass the watcher loop body — we only care about the startup
    # catch-up path here. The async generator yields nothing.
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    # Death event from the gap should have been ingested.
    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            rows = s.execute(select(Event)).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "death"
        assert rows[0].primary_character_id == 1234
        assert rows[0].event_date == "1067.4.10"
    finally:
        engine.dispose()

    # Persisted baseline must have advanced to T2 so the next restart
    # picks up where this one left off.
    # ck3_chronicler-elll: load_baseline now returns BaselineLoad.
    advanced_load = load_baseline(baseline_file)
    assert advanced_load is not None
    advanced = advanced_load.snapshot
    assert advanced.current_date == "1067.7.15"
    assert advanced.playthrough_id == playthrough


@pytest.mark.asyncio
async def test_startup_ignores_persisted_baseline_when_playthrough_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisted baseline from a different playthrough than the on-disk
    save must be discarded — the alternative is emitting cross-campaign
    phantom events on the first diff. Today's silent-rebaseline behavior
    is the safe fallback."""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    save_file = save_dir / "autosave.ck3"
    save_file.write_bytes(b"opaque")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-mismatch"

    from chronicler.db.registry import add_tracked_character

    add_tracked_character(campaign_id, 1234, note="test", registry=registry_path)

    # Persisted baseline from a *different* campaign with char 1234 alive.
    snap_old = SaveSnapshot(
        playthrough_id="uuid-OLD-campaign",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    baseline_file = baseline_path_for(db_path)
    save_baseline(baseline_file, snap_old)

    # On-disk save from the current campaign — character is dead but
    # this is a different playthrough so we must NOT diff old → new.
    snap_current = SaveSnapshot(
        playthrough_id="uuid-CURRENT-campaign",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.7.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1067.4.10")},
    )

    _patch_parse_save(monkeypatch, lambda path: ({}, snap_current))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    # auto_import_first_save=False isolates the baseline-discard decision under
    # test. ck3_chronicler-27ov.12 unified the startup drain onto the live
    # consumer, so the fresh-adopt-after-discard path now routes through the
    # consumer's cij bootstrap (covered by the dedicated auto-import tests);
    # disabling it here keeps this test focused on the silent rebaseline.
    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        auto_import_first_save=False,
    )

    # No events should have been emitted — the cross-playthrough diff
    # was correctly skipped.
    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            rows = s.execute(select(Event)).scalars().all()
        assert rows == []
    finally:
        engine.dispose()

    # The persisted baseline should now reflect the current playthrough,
    # so the next restart against this campaign behaves as today.
    advanced_load = load_baseline(baseline_file)
    assert advanced_load is not None
    advanced = advanced_load.snapshot
    assert advanced.playthrough_id == "uuid-CURRENT-campaign"
    assert advanced.current_date == "1067.7.15"


@pytest.mark.asyncio
async def test_startup_silent_baseline_when_auto_import_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``auto_import_first_save=False`` preserves the legacy behaviour:
    no persisted baseline + fresh DB → silent baseline from the on-disk
    save, no events emitted on startup, and the campaign DB stays
    empty until events arrive via the diff path. Caller-driven imports
    use this opt-out (the test setup pre-pins the playthrough or
    expects no characters at all)."""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-fresh"

    from chronicler.db.registry import add_tracked_character

    add_tracked_character(campaign_id, 1234, note="test", registry=registry_path)

    snap = SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.7.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1067.4.10")},
    )
    _patch_parse_save(monkeypatch, lambda p: ({}, snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        auto_import_first_save=False,
    )

    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            rows = s.execute(select(Event)).scalars().all()
        assert rows == []
    finally:
        engine.dispose()

    # First-time persistence: baseline file should now exist with the
    # silent baseline state.
    persisted_load = load_baseline(baseline_path_for(db_path))
    assert persisted_load is not None
    persisted = persisted_load.snapshot
    assert persisted.current_date == "1067.7.15"


# --- ck3_chronicler-cij: auto-import on fresh campaign DB ---
@pytest.mark.asyncio
async def test_startup_auto_imports_on_fresh_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default behaviour for a fresh DB + no baseline + no cached saves:
    save-tail invokes import_save synchronously before the watcher
    arms, so the user can run ``chronicler save-tail`` against a fresh
    campaign and get characters + vanilla memories backfilled without
    needing a separate ``import-save`` step.

    Acceptance: characters table populated, playthrough_id pinned,
    baseline.json persisted at the imported snap's date."""
    from unittest.mock import patch

    from chronicler.db import Character
    from chronicler.db.registry import add_tracked_character
    from chronicler.db.repository import get_meta
    from chronicler.save.parse import parse_save

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque save content")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-cij"
    add_tracked_character(campaign_id, 100, note="player", registry=registry_path)

    # Synthetic raw save in the shape the importer expects. Two
    # characters, one with a vanilla memory so the import has events to
    # backfill (proves the import path actually ran, not just the
    # silent baseline fallback).
    fake_raw = {
        "playthrough_id": "uuid-cij-test",
        "meta_data": {"version": "1.19.0", "meta_date": "1066.9.15"},
        "bookmark_date": "1066.9.15",
        "living": {
            "100": {
                "first_name": "Erik",
                "birth": "1031.1.1",
                "alive_data": {"memories": [1]},
            },
            "200": {"first_name": "Sigrid", "birth": "1035.1.1", "female": True},
        },
        "dead_unprunable": {},
        "character_memory_manager": {
            "database": {
                "1": {
                    "type": "memory_grand_wedding",
                    "creation_date": "1066.6.1",
                    "participants": {"spouse": 200},
                }
            }
        },
        "dynasties": {"dynasty_house": {}},
    }
    parsed_snap = parse_save(fake_raw)

    _patch_parse_save(monkeypatch, lambda path: (fake_raw, parsed_snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    with patch("chronicler.save.importer.convert_save_to_json", return_value=fake_raw):
        await run_save_ingest(
            save_dir=save_dir,
            db_path=db_path,
            campaign_id=campaign_id,
            registry_path=registry_path,
        )

    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            chars = s.execute(select(Character)).scalars().all()
            events = s.execute(select(Event)).scalars().all()
            pinned = get_meta(s, "playthrough_id")
        # Both characters from the fake save persisted
        assert {c.ck3_id for c in chars} == {100, 200}
        # The vanilla memory event was backfilled by the import
        assert len(events) == 1
        assert events[0].event_type == "vanilla_memory"
        # Playthrough was pinned by the import (silent baseline path
        # would have left it None — pin_if_unset=False on save-tail's
        # own verify pass).
        assert pinned == "uuid-cij-test"
    finally:
        engine.dispose()

    # Baseline persisted at the imported snap's date so a restart is
    # idempotent.
    baseline_load = load_baseline(baseline_path_for(db_path))
    assert baseline_load is not None
    baseline = baseline_load.snapshot
    assert baseline.playthrough_id == "uuid-cij-test"


@pytest.mark.asyncio
async def test_auto_import_skipped_when_db_already_has_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-import only fires on a *fresh* DB. A campaign DB that
    already has events (e.g. from a prior import-save the user ran
    explicitly) must not be re-imported on save-tail startup —
    otherwise we'd double-write characters and re-pin the
    playthrough."""
    from unittest.mock import patch

    from chronicler.db import Character
    from chronicler.db.registry import add_tracked_character

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    # Seed: pre-existing event + character so the DB is not "fresh".
    with factory() as s:
        upsert_character(s, ck3_id=100, first_name="Erik")
        insert_event_idempotent(
            s,
            schema_version=1,
            event_type="death",
            event_date="1066.9.15",
            event_date_iso="1066-09-15",
            wall_clock_at="2026-05-01T00:00:00+00:00",
            primary_character_id=100,
            payload_json='{"v":1,"t":"death","d":"1066.9.15","c":100,"p":{}}',
            raw_line="seeded",
        )
        s.commit()
    engine.dispose()

    campaign_id = "test-campaign-cij-skip"
    add_tracked_character(campaign_id, 100, note="player", registry=registry_path)

    # Snap shows a different character set; if auto-import fired, char
    # 999 would land in the DB. The test asserts it does not.
    snap = SaveSnapshot(
        playthrough_id="uuid-skip",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.7.15",
        player_character_id=999,
        characters={999: _char(999)},
    )
    _patch_parse_save(monkeypatch, lambda p: ({}, snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    # If auto-import incorrectly fired, this patch would catch it via
    # the call (importer would invoke convert_save_to_json). Treat any
    # call as a test failure.
    def _import_should_not_fire(*_args, **_kwargs):
        raise AssertionError("import_save was invoked on a non-fresh DB")

    with patch(
        "chronicler.save.importer.convert_save_to_json",
        side_effect=_import_should_not_fire,
    ):
        await run_save_ingest(
            save_dir=save_dir,
            db_path=db_path,
            campaign_id=campaign_id,
            registry_path=registry_path,
        )

    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            chars = s.execute(select(Character)).scalars().all()
        # Only the seeded character — char 999 from the snap was NOT
        # imported. (Save-tail's catch_up path may upsert it as part of
        # the silent baseline, so this is not a strict equality assert
        # — what matters is that import_save didn't run.)
        assert any(c.ck3_id == 100 for c in chars)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_cij_seed_populates_registry_overview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-8fo regression: the cij auto-import seed must
    sync the registry row's last_event_at and the cqo identity overview
    columns. Before the fix, fields populated by _advance_baseline
    (current_player_name, current_in_game_date, etc.) stayed NULL on
    fresh campaigns until a SECOND autosave landed and triggered the
    diff path — meaning the Library card's marquee byline (cqo T8)
    was blank for one tick on first impression.
    """
    from unittest.mock import patch

    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
        get_campaign_by_id,
    )
    from chronicler.save.parse import parse_save

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    # Real registry row so update_campaign_overview has a row to UPDATE.
    camp = create_campaign(
        "8fo seed test",
        db_path=str(db_path),
        ck3_playthrough_id=None,
        registry=registry_path,
    )
    campaign_id = camp.id
    add_tracked_character(campaign_id, 100, note="player", registry=registry_path)

    fake_raw = {
        "playthrough_id": "uuid-8fo-seed",
        "meta_data": {
            "version": "1.19.0",
            "meta_date": "1066.9.15",
            "meta_main_portrait": {"id": 100},
        },
        "bookmark_date": "1066.9.15",
        "living": {
            "100": {
                "first_name": "Hoel",
                "birth": "1031.1.1",
                "alive_data": {"memories": []},
            },
        },
        "dead_unprunable": {},
        "character_memory_manager": {"database": {}},
        "dynasties": {"dynasty_house": {}},
    }
    parsed_snap = parse_save(fake_raw)

    _patch_parse_save(monkeypatch, lambda path: (fake_raw, parsed_snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    with patch("chronicler.save.importer.convert_save_to_json", return_value=fake_raw):
        await run_save_ingest(
            save_dir=save_dir,
            db_path=db_path,
            campaign_id=campaign_id,
            registry_path=registry_path,
        )

    refreshed = get_campaign_by_id(campaign_id, registry=registry_path)
    assert refreshed is not None

    assert refreshed.last_event_at is not None, (
        "cij seed must populate registry.last_event_at — without it the "
        "Library card freshness indicator stays blank until the second "
        "autosave lands."
    )
    assert refreshed.current_player_name == "Hoel", (
        "cij seed must mirror the player's identity onto the registry "
        "so the cqo byline (T8) renders on first impression. Got: "
        f"{refreshed.current_player_name!r}"
    )
    assert refreshed.bookmark_date == "1066.9.15"
    assert refreshed.current_in_game_date == "1066.9.15"
    assert refreshed.current_player_character_id == 100
    assert refreshed.ck3_playthrough_id == "uuid-8fo-seed"


# --- ck3_chronicler-qtz: --campaign-pre-armed flow auto-import on first save ---
@pytest.mark.asyncio
async def test_watch_and_adopt_first_save_runs_auto_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-qtz regression: ``chronicler dev --campaign <name>``
    started against an EMPTY save dir must run auto-import on the first
    save that arrives via the watcher — not just silently baseline.

    Pre-fix behaviour: tracked_characters stayed empty after the first
    save landed, so the diff layer dropped every subsequent event until
    the user ran ``chronicler auto-track`` manually. Filed during smoke
    session 2026-05-04 v2.

    Acceptance: characters table populated from the first save, vanilla
    memory events ingested, playthrough pinned. Equivalent to the
    startup-time test_startup_auto_imports_on_fresh_db but driven through
    the watcher's first-snapshot-via-consumer arrival path.
    """
    from unittest.mock import patch

    from chronicler.db import Character
    from chronicler.db.registry import add_tracked_character
    from chronicler.db.repository import get_meta
    from chronicler.save.parse import parse_save
    from chronicler.save.watcher import SaveFileEvent

    # Empty save dir at startup — this is the qtz scenario. The save file
    # is dropped only after the watcher yields, modelling a CK3 user who
    # starts chronicler before producing their first autosave.
    save_dir = tmp_path / "saves"
    save_dir.mkdir()

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-qtz"
    add_tracked_character(campaign_id, 100, note="player", registry=registry_path)

    fake_raw = {
        "playthrough_id": "uuid-qtz-test",
        "meta_data": {"version": "1.19.0", "meta_date": "1066.9.15"},
        "bookmark_date": "1066.9.15",
        "living": {
            "100": {
                "first_name": "Erik",
                "birth": "1031.1.1",
                "alive_data": {"memories": [1]},
            },
            "200": {"first_name": "Sigrid", "birth": "1035.1.1", "female": True},
        },
        "dead_unprunable": {},
        "character_memory_manager": {
            "database": {
                "1": {
                    "type": "memory_grand_wedding",
                    "creation_date": "1066.6.1",
                    "participants": {"spouse": 200},
                }
            }
        },
        "dynasties": {"dynasty_house": {}},
    }
    parsed_snap = parse_save(fake_raw)

    save_path = save_dir / "autosave.ck3"

    async def _drop_save_then_yield(*_args, **_kwargs):
        # Simulate the user producing their first autosave AFTER chronicler
        # is already running. Drop the file then yield the watcher event.
        save_path.write_bytes(b"opaque save content")
        yield SaveFileEvent(path=save_path, mtime_ns=1, size_bytes=len(save_path.read_bytes()))

    _patch_parse_save(monkeypatch, lambda path: (fake_raw, parsed_snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _drop_save_then_yield)

    with patch("chronicler.save.importer.convert_save_to_json", return_value=fake_raw):
        await run_save_ingest(
            save_dir=save_dir,
            db_path=db_path,
            campaign_id=campaign_id,
            registry_path=registry_path,
        )

    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            chars = s.execute(select(Character)).scalars().all()
            events = s.execute(select(Event)).scalars().all()
            pinned = get_meta(s, "playthrough_id")
        # Both characters from the first save persisted by auto-import,
        # not just the silent baseline: characters table is populated.
        assert {c.ck3_id for c in chars} == {100, 200}, (
            "auto-import must seed characters when the first save arrives "
            "via the watcher on an empty-save-dir startup"
        )
        # The vanilla memory was backfilled — proves import_save ran rather
        # than the silent-baseline fallback.
        assert len(events) == 1
        assert events[0].event_type == "vanilla_memory"
        # Playthrough pinned by the import (silent baseline path leaves
        # this None on save-tail's verify pass per pin_if_unset=False).
        assert pinned == "uuid-qtz-test"
    finally:
        engine.dispose()

    # Baseline persisted at the imported snap so a restart is idempotent.
    baseline_load = load_baseline(baseline_path_for(db_path))
    assert baseline_load is not None
    baseline = baseline_load.snapshot
    assert baseline.playthrough_id == "uuid-qtz-test"


# --- ck3_chronicler-1js: pending-cache drain at bootstrap ---
@pytest.mark.asyncio
async def test_startup_drains_pending_cached_saves_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached saves left behind by a previous crashed run should be
    parsed + diffed in chronological (seqno) order before the watcher
    starts. Each cached save's parse advances the baseline; events
    that landed in the gap (here, a death) materialise instead of
    being silently rebaselined away."""
    from chronicler.save.cache import SaveCache, cache_dir_for

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    save_file = save_dir / "autosave.ck3"
    save_file.write_bytes(b"opaque content")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "chronicler-data"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-1js"
    playthrough = "uuid-A"

    from chronicler.db.registry import add_tracked_character

    add_tracked_character(campaign_id, 1234, note="test", registry=registry_path)

    # Persisted baseline at T1: char 1234 alive.
    snap_t1 = SaveSnapshot(
        playthrough_id=playthrough,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    save_baseline(baseline_path_for(db_path), snap_t1)

    # Two cached saves left over from the prior run:
    #   T2 (cache seqno=1): char 1234 dies on 1066.12.10
    #   T3 (cache seqno=2): char 1234 still dead, dummy advance
    snap_t2 = SaveSnapshot(
        playthrough_id=playthrough,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.12.31",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1066.12.10")},
    )
    snap_t3 = SaveSnapshot(
        playthrough_id=playthrough,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.4.30",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1066.12.10")},
    )
    # On-disk current save (T3 = same as last cached one, so the
    # post-drain catch-up stale-read-guards out cleanly).
    snap_current = snap_t3

    cache_dir = cache_dir_for(data_dir, campaign_id)
    cache = SaveCache(cache_dir)
    cached_t2 = cache.cache_save(save_file)
    assert cached_t2 is not None
    cached_t3 = cache.cache_save(save_file)
    assert cached_t3 is not None
    cached_paths = {cached_t2.path: snap_t2, cached_t3.path: snap_t3}

    # Per-path parse stub: cached files return their respective T2/T3
    # snapshots; the on-disk save returns T3.
    def _parse_stub(path: Path):
        if path in cached_paths:
            return ({}, cached_paths[path])
        return ({}, snap_current)

    _patch_parse_save(monkeypatch, _parse_stub)
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    # Death event from cached T2 should have been ingested during drain.
    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            rows = s.execute(select(Event)).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "death"
        assert rows[0].primary_character_id == 1234
        assert rows[0].event_date == "1066.12.10"
    finally:
        engine.dispose()

    # Both cached files must have been deleted via mark_processed.
    assert SaveCache(cache_dir).pending() == []

    # Persisted baseline must reflect the latest snapshot we've seen.
    advanced_load = load_baseline(baseline_path_for(db_path))
    assert advanced_load is not None
    advanced = advanced_load.snapshot
    assert advanced.current_date == "1067.4.30"


@pytest.mark.asyncio
async def test_startup_clears_cache_on_playthrough_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the on-disk save's playthrough_id differs from the persisted
    baseline's, the persisted baseline AND the pending cached saves are
    stale (they belong to the abandoned playthrough). Both must be
    discarded so the new playthrough doesn't ingest cross-campaign
    phantom events when the watcher takes over."""
    from chronicler.save.cache import SaveCache, cache_dir_for

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    save_file = save_dir / "autosave.ck3"
    save_file.write_bytes(b"opaque")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "chronicler-data"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    campaign_id = "test-campaign-1js-mismatch"

    from chronicler.db.registry import add_tracked_character

    add_tracked_character(campaign_id, 1234, note="test", registry=registry_path)

    # Persisted baseline + cached save under playthrough A.
    snap_a = SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.10.15",
        player_character_id=1234,
        characters={1234: _char(1234)},
    )
    save_baseline(baseline_path_for(db_path), snap_a)
    cache_dir = cache_dir_for(data_dir, campaign_id)
    cache = SaveCache(cache_dir)
    cached_a = cache.cache_save(save_file)
    assert cached_a is not None

    # On-disk save belongs to playthrough B (user switched campaigns).
    snap_b = SaveSnapshot(
        playthrough_id="uuid-B",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1100.1.1",
        player_character_id=9999,
        characters={9999: _char(9999)},
    )

    _patch_parse_save(monkeypatch, lambda p: ({}, snap_b))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    # auto_import_first_save=False: see the sibling mismatch test — 27ov.12
    # routes fresh adopts through the consumer's cij bootstrap, so disabling it
    # keeps this test focused on the cache-clear + silent rebaseline decision.
    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        auto_import_first_save=False,
    )

    # No events ingested — the gap was discarded along with the cache.
    engine = make_engine_for_path(db_path)
    factory = make_session_factory(engine)
    try:
        with factory() as s:
            rows = s.execute(select(Event)).scalars().all()
        assert rows == []
    finally:
        engine.dispose()

    # Cache directory is empty — the stale cached save was purged.
    assert SaveCache(cache_dir).pending() == []

    # New baseline reflects the on-disk (playthrough-B) save.
    advanced_load = load_baseline(baseline_path_for(db_path))
    assert advanced_load is not None
    advanced = advanced_load.snapshot
    assert advanced.playthrough_id == "uuid-B"
