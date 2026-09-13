"""_advance_baseline tick side-effects: registry-overview persistence,
SSE frames (cache_state, save_pair_completed, save_dropped_foreign,
heartbeat) and per-event ingest error/savepoint isolation.

Split from the test_save_ingest monolith (ck3_chronicler-27ov.72)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.db import (
    Base,
    make_engine_for_path,
)
from chronicler.db.repository import (
    upsert_character,
)
from chronicler.save.ingest import (
    run_save_ingest,
)
from chronicler.save.parse import (
    SaveSnapshot,
)
from tests.helpers.ingest import (
    _char,
    _patch_parse_save,
    _snap,
    _snap_pt,
)
from tests.helpers.snapshots import make_char


# --- ck3_chronicler-cqo: registry overview persisted on each save-tail tick ---
def test_advance_baseline_writes_campaign_overview_to_registry(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-cqo: after _advance_baseline ingests a tick, the
    registry's Campaign row has bookmark_date / current_in_game_date /
    current_player_* / current_house_name populated. Library cards are
    one query per card after this lands."""
    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
        get_campaign_by_id,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    db_path = tmp_path / "campaign.db"
    # Pre-create the campaign + the per-campaign DB schema so save-tail
    # can write events.
    camp = create_campaign(
        "Erik 1066-9-15",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-cqo",
        registry=registry_path,
    )
    add_tracked_character(camp.id, 1234, registry=registry_path)
    # Seed the per-campaign DB with stale data — first_name differs
    # from snap. The registry must reflect the SNAP's first_name
    # ("Erik"), not the DB's ("StaleErik"). This guards against the
    # earlier rejected implementation that read from the DB.
    with session_factory() as s:
        upsert_character(
            s,
            ck3_id=1234,
            first_name="StaleErik",
            nickname="the Heathen",
            house_name="house_munso",
        )
        s.commit()

    # Build characters whose snap data matches what the test expects
    # the registry to record (first_name, nickname, house_name).
    erik_baseline = make_char(
        1234,
        first_name="Erik",
        nickname="the Heathen",
        birth_date="1031.1.1",
        dynasty_house_id=10566,
    )
    erik_next = make_char(
        1234,
        first_name="Erik",
        nickname="the Heathen",
        birth_date="1031.1.1",
        dynasty_house_id=10566,
        location_id=200,  # travel triggers a real diff event
    )
    baseline = SaveSnapshot(
        playthrough_id="uuid-cqo",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: erik_baseline},
        houses_lookup={10566: "house_munso"},
    )
    next_tick = SaveSnapshot(
        playthrough_id="uuid-cqo",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.3.1",
        player_character_id=1234,
        characters={1234: erik_next},
        houses_lookup={10566: "house_munso"},
    )
    _advance_baseline(
        next_tick,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=None,
    )

    fresh = get_campaign_by_id(camp.id, registry=registry_path)
    assert fresh is not None
    assert fresh.bookmark_date == "1066.9.15"
    assert fresh.current_in_game_date == "1067.3.1"
    assert fresh.current_player_character_id == 1234
    assert fresh.current_player_name == "Erik"
    assert fresh.current_player_nickname == "the Heathen"
    # ck3_chronicler-6d1c: decode_house_name strips the ``house_`` namespace
    # prefix CK3 emits in houses_lookup so the byline reads readably.
    assert fresh.current_house_name == "munso"


def test_advance_baseline_overview_handles_heir_succession(tmp_path: Path, session_factory) -> None:
    """ck3_chronicler-cqo: when snap.player_character_id flips (heir
    succession), the registry's current_player_* + current_house_name
    update on the next tick. bookmark_date stays."""
    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
        get_campaign_by_id,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    db_path = tmp_path / "campaign.db"
    camp = create_campaign(
        "Erik 1066-9-15",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-succession",
        registry=registry_path,
    )
    add_tracked_character(camp.id, 1234, registry=registry_path)
    add_tracked_character(camp.id, 5678, registry=registry_path)
    with session_factory() as s:
        upsert_character(s, ck3_id=1234, first_name="Erik", house_name="house_munso")
        upsert_character(
            s,
            ck3_id=5678,
            first_name="Asbjorn",
            nickname=None,
            house_name="house_munso",
        )
        s.commit()

    erik_alive = make_char(1234, first_name="Erik", birth_date="1031.1.1", dynasty_house_id=10566)
    erik_dead = make_char(
        1234,
        first_name="Erik",
        birth_date="1031.1.1",
        dynasty_house_id=10566,
        is_dead=True,
        death_date="1075.5.9",
    )
    asbjorn = make_char(5678, first_name="Asbjorn", birth_date="1050.1.1", dynasty_house_id=10566)

    snap_a = SaveSnapshot(
        playthrough_id="uuid-succession",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1075.4.1",
        player_character_id=1234,
        characters={1234: erik_alive, 5678: asbjorn},
        houses_lookup={10566: "house_munso"},
    )
    snap_b = SaveSnapshot(
        playthrough_id="uuid-succession",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1075.5.10",
        player_character_id=5678,  # the heir is now the player
        characters={1234: erik_dead, 5678: asbjorn},
        houses_lookup={10566: "house_munso"},
    )
    _advance_baseline(
        snap_b,
        last_snapshot=snap_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=None,
    )

    fresh = get_campaign_by_id(camp.id, registry=registry_path)
    assert fresh is not None
    assert fresh.bookmark_date == "1066.9.15"  # never moves
    assert fresh.current_player_character_id == 5678
    assert fresh.current_player_name == "Asbjorn"


def test_advance_baseline_overview_clears_stale_nickname_on_succession(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-cqo: when the player succeeds to an heir without a
    nickname, the registry's current_player_nickname is cleared (not
    left stale from the predecessor). Catches the None-skip semantics
    bug where the heir's None-nickname was silently dropped at the
    update_campaign_overview filter, leaving 'Asbjorn, called the
    Heathen' on the Library card byline."""
    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
        get_campaign_by_id,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    db_path = tmp_path / "campaign.db"
    camp = create_campaign(
        "Erik 1066-9-15",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-stale-nick",
        registry=registry_path,
    )
    add_tracked_character(camp.id, 1234, registry=registry_path)
    add_tracked_character(camp.id, 5678, registry=registry_path)
    with session_factory() as s:
        upsert_character(
            s,
            ck3_id=1234,
            first_name="Erik",
            nickname="the Heathen",
            house_name="house_munso",
        )
        upsert_character(s, ck3_id=5678, first_name="Asbjorn", house_name="house_munso")
        s.commit()

    erik_alive = make_char(
        1234,
        first_name="Erik",
        nickname="the Heathen",
        birth_date="1031.1.1",
        dynasty_house_id=10566,
    )
    erik_dead = make_char(
        1234,
        first_name="Erik",
        nickname="the Heathen",
        birth_date="1031.1.1",
        dynasty_house_id=10566,
        is_dead=True,
        death_date="1075.5.9",
    )
    asbjorn = make_char(  # KEY: heir has no nickname
        5678, first_name="Asbjorn", birth_date="1055.1.1", dynasty_house_id=10566
    )

    snap_a = SaveSnapshot(
        playthrough_id="uuid-stale-nick",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1075.4.1",
        player_character_id=1234,
        characters={1234: erik_alive, 5678: asbjorn},
        houses_lookup={10566: "house_munso"},
    )
    snap_b = SaveSnapshot(
        playthrough_id="uuid-stale-nick",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1075.5.10",
        player_character_id=5678,
        characters={1234: erik_dead, 5678: asbjorn},
        houses_lookup={10566: "house_munso"},
    )
    # Tick A: establish baseline, capture Erik's full identity in registry.
    _advance_baseline(
        snap_a,
        last_snapshot=SaveSnapshot(
            playthrough_id="uuid-stale-nick",
            ck3_version="1.19.0.4",
            bookmark_date="1066.9.15",
            current_date="1066.9.15",  # earlier date so snap_a's date is "newer"
            player_character_id=1234,
            characters={1234: erik_alive, 5678: asbjorn},
            houses_lookup={10566: "house_munso"},
        ),
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=None,
    )
    fresh_a = get_campaign_by_id(camp.id, registry=registry_path)
    assert fresh_a is not None
    assert fresh_a.current_player_nickname == "the Heathen"  # tick A populated

    # Tick B: succession. Asbjorn has no nickname.
    _advance_baseline(
        snap_b,
        last_snapshot=snap_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=None,
    )
    fresh_b = get_campaign_by_id(camp.id, registry=registry_path)
    assert fresh_b is not None
    assert fresh_b.current_player_character_id == 5678
    assert fresh_b.current_player_name == "Asbjorn"
    # The bug being fixed: this MUST be None, not stale "the Heathen".
    assert fresh_b.current_player_nickname is None


# --- ck3_chronicler-7w5: cache_state SSE events ---
@pytest.mark.asyncio
async def test_cache_state_published_on_each_cache_and_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every cache_save (producer) and mark_processed (consumer) hop
    publishes a 'cache_state' event on the bus so the v0.7 Save-tail
    page can render lag + GC banners live."""
    from chronicler.api.events import EventBus
    from chronicler.save.watcher import SaveFileEvent

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()
    campaign_id = "test-7w5"

    save_files = [save_dir / f"autosave_{i}.ck3" for i in range(3)]
    for f in save_files:
        f.write_bytes(b"opaque")

    snap = SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.1.1",
        player_character_id=1234,
        characters={1234: _char(1234)},
    )

    async def _burst_watcher(*_args, **_kwargs):
        for f in save_files:
            yield SaveFileEvent(path=f, mtime_ns=1, size_bytes=len(f.read_bytes()))

    _patch_parse_save(monkeypatch, lambda p: ({}, snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _burst_watcher)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(cid, event):
        received.append({"campaign_id": cid, **event})
        monkey_publish(cid, event)

    bus.publish = _capture  # type: ignore[method-assign]

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
        event_bus=bus,
    )

    cache_state_events = [e for e in received if e.get("kind") == "cache_state"]
    # Per producer cache_save (3) + per consumer mark_processed (3) = 6
    # publications minimum. Race-tolerant lower bound check.
    assert len(cache_state_events) >= 3
    # Final event should reflect drained cache (pending=0)
    final_state = next(e for e in reversed(cache_state_events) if e.get("kind") == "cache_state")
    assert final_state["pending"] == 0
    assert final_state["gc_drops_lifetime"] == 0  # no GC under default cap
    # Every event carries the right campaign + the snapshot keys
    for e in cache_state_events:
        assert e["campaign_id"] == campaign_id
        assert "pending" in e
        assert "bytes" in e
        assert "gc_drops_lifetime" in e


# --- ck3_chronicler-bges: _advance_baseline last-tick persistence + SSE ---
def test_advance_baseline_persists_last_tick_on_registry(
    tmp_path: Path,
    session_factory,
) -> None:
    """ck3_chronicler-bges: end of an _advance_baseline tick persists the
    last-tick info to the campaigns row, so a cold load (no SSE) can
    still render the strip + per-card line."""
    import json

    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
        get_campaign_by_id,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    db_path = tmp_path / "campaign.db"
    camp = create_campaign(
        "bges-persist test",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-bges-persist",
        registry=registry_path,
    )
    add_tracked_character(camp.id, 1234, registry=registry_path)

    baseline = SaveSnapshot(
        playthrough_id="uuid-bges-persist",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    curr = SaveSnapshot(
        playthrough_id="uuid-bges-persist",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.2.1",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1067.1.15")},
    )

    _advance_baseline(
        curr,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=tmp_path / "baseline.json",
        event_bus=None,
    )

    fetched = get_campaign_by_id(camp.id, registry=registry_path)
    assert fetched is not None
    assert fetched.last_save_filename == "autosave.ck3"
    assert fetched.last_save_in_game_date == "1067.2.1"
    assert fetched.last_tick_event_count == 1
    assert json.loads(fetched.last_tick_event_type_tally or "{}") == {"death": 1}
    assert fetched.last_save_ingested_at is not None


def test_advance_baseline_publishes_save_pair_completed(
    tmp_path: Path,
    session_factory,
) -> None:
    """ck3_chronicler-bges: the same _advance_baseline tick fires the SSE
    frame after persisting."""
    from chronicler.api.events import EventBus
    from chronicler.db.registry import add_tracked_character, create_campaign
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    db_path = tmp_path / "campaign.db"
    camp = create_campaign(
        "bges-sse test",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-bges-sse",
        registry=registry_path,
    )
    add_tracked_character(camp.id, 1234, registry=registry_path)

    baseline = SaveSnapshot(
        playthrough_id="uuid-bges-sse",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    curr = SaveSnapshot(
        playthrough_id="uuid-bges-sse",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.2.1",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1067.1.15")},
    )

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(campaign_id: str, event: dict) -> None:
        received.append({"campaign_id": campaign_id, **event})
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    _advance_baseline(
        curr,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=tmp_path / "baseline.json",
        event_bus=bus,
    )

    completed = [e for e in received if e.get("kind") == "save_pair_completed"]
    assert len(completed) == 1
    assert completed[0]["save_filename"] == "autosave.ck3"
    assert completed[0]["event_count"] == 1
    assert completed[0]["campaign_id"] == camp.id

    # Ordering: event_ingested frames must precede the save_pair_completed frame.
    kinds = [e.get("kind") for e in received]
    assert kinds[-1] == "save_pair_completed"
    assert "event_ingested" in kinds[:-1]


def test_ingest_diff_events_uses_error_outcome_on_per_event_exception(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    """ck3_chronicler-zopa: a per-event exception in _ingest_diff_event
    yields IngestResult(outcome='error'), not 'duplicate', so the
    error count never inflates the duplicate tally."""
    from chronicler.save import tick as tick_module
    from chronicler.save.diff import diff_snapshots
    from chronicler.save.ingest import _ingest_diff_events

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated per-event failure")

    # 27ov.40: _ingest_diff_events + _ingest_diff_event moved to save.tick;
    # patch the per-event helper where its caller now resolves it.
    monkeypatch.setattr(tick_module, "_ingest_diff_event", _boom)

    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap({1234: _char(1234, is_dead=True, death_date="1067.1.15")})
    diffs = list(diff_snapshots(prev, curr))
    assert len(diffs) >= 1

    _, results = _ingest_diff_events(
        diffs,
        factory=session_factory,
        save_path_name="zopa-test.ck3",
        scheduler=None,
        event_bus=None,
        campaign_id="camp-zopa",
    )

    assert len(results) == len(diffs)
    assert all(r.outcome == "error" for r in results)
    assert not any(r.outcome == "duplicate" for r in results)


def test_ingest_diff_events_isolates_a_poison_event_with_savepoint(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    """ck3_chronicler-27ov.20 (audit M-I1): a single event that errors at the
    DB layer must roll back ALONE, not poison the shared session. Pre-fix, one
    SQLAlchemy error put the session in pending-rollback, every later event
    failed with PendingRollbackError, the final commit raised (rolling back the
    WHOLE tick's events), and the exception propagated into the unguarded
    consumer (audit H5) — stopping ingestion until restart. With a per-event
    SAVEPOINT, good events before AND after the poison commit, and
    _ingest_diff_events never raises."""
    from sqlalchemy import text

    from chronicler.db.models import Character
    from chronicler.save import tick as tick_module
    from chronicler.save.diff import diff_snapshots
    from chronicler.save.ingest import IngestResult, _ingest_diff_events

    # A scratch table to prove what commits vs rolls back, free of any real
    # schema coupling.
    with session_factory() as setup:
        setup.execute(text("CREATE TABLE IF NOT EXISTS _mi1_probe (id INTEGER PRIMARY KEY)"))
        setup.commit()

    # Three diff events; the fake keys on call order (good, poison, good).
    prev = _snap({1: _char(1), 2: _char(2), 3: _char(3)})
    curr = _snap(
        {
            1: _char(1, is_dead=True, death_date="1067.1.1"),
            2: _char(2, is_dead=True, death_date="1067.1.2"),
            3: _char(3, is_dead=True, death_date="1067.1.3"),
        }
    )
    diffs = list(diff_snapshots(prev, curr))
    assert len(diffs) >= 3

    calls = {"n": 0}

    def _fake(d, *, session, **_kwargs):
        calls["n"] += 1
        n = calls["n"]
        # Every event writes a probe row first (id 1001, 1002, 1003).
        session.execute(text("INSERT INTO _mi1_probe (id) VALUES (:i)"), {"i": 1000 + n})
        if n == 2:
            # Force an ORM flush IntegrityError (duplicate PK) — the
            # session-POISONING failure M-I1 is about. Without a savepoint this
            # leaves the session in pending-rollback, so event 3 and the final
            # commit both raise PendingRollbackError and the whole tick is lost.
            session.add(Character(ck3_id=777))
            session.add(Character(ck3_id=777))
            session.flush()
        return IngestResult(outcome="ingested")

    monkeypatch.setattr(tick_module, "_ingest_diff_event", _fake)

    # Must NOT raise (pre-fix: outer commit raised PendingRollbackError).
    _, results = _ingest_diff_events(
        diffs[:3],
        factory=session_factory,
        save_path_name="m-i1.ck3",
        scheduler=None,
        event_bus=None,
        campaign_id="camp-mi1",
    )

    assert [r.outcome for r in results] == ["ingested", "error", "ingested"]

    # Good events (1001, 1003) committed; the poison event's write (1002) rolled
    # back alone.
    with session_factory() as verify:
        ids = {r[0] for r in verify.execute(text("SELECT id FROM _mi1_probe")).all()}
    assert ids == {1001, 1003}, f"savepoint isolation failed; probe rows = {ids}"


def test_ingest_diff_events_publishes_no_event_ingested_when_tick_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    """ck3_chronicler-27ov.76 (audit L7): event_ingested frames are buffered
    and published only AFTER the tick's session_scope commits. If the tick
    rolls back (commit fails / body raises), the buffered frames are never
    flushed — so a subscriber never sees a phantom feed entry for a row that
    didn't persist. Pre-fix, the frame fired inside the per-event ingest,
    before commit, so a rolled-back tick still published it."""
    import contextlib

    from chronicler.save import tick as tick_module
    from chronicler.save.diff import diff_snapshots
    from chronicler.save.ingest import _ingest_diff_events

    published: list[tuple[str, dict]] = []

    class _SpyBus:
        def publish(self, campaign_id: str, frame: dict) -> None:
            published.append((campaign_id, frame))

    real_scope = tick_module.session_scope

    @contextlib.contextmanager
    def _scope_that_fails_to_commit(factory):
        # Run the real scope (events flush into savepoints) but blow up
        # before it can commit, so the whole tick rolls back.
        with real_scope(factory) as session:
            yield session
            raise RuntimeError("simulated tick commit failure")

    monkeypatch.setattr(tick_module, "session_scope", _scope_that_fails_to_commit)

    prev = _snap({1234: _char(1234, is_dead=False)})
    curr = _snap({1234: _char(1234, is_dead=True, death_date="1067.1.15")})
    diffs = list(diff_snapshots(prev, curr))
    assert diffs

    with pytest.raises(RuntimeError, match="simulated tick commit failure"):
        _ingest_diff_events(
            diffs,
            factory=session_factory,
            save_path_name="l7.ck3",
            scheduler=None,
            event_bus=_SpyBus(),
            campaign_id="camp-l7",
        )

    assert published == [], "phantom event_ingested frame published despite rolled-back tick"


def test_advance_baseline_empty_tracked_set_publishes_heartbeat(
    tmp_path: Path,
    session_factory,
) -> None:
    """ck3_chronicler-87pi: when no characters are tracked, _advance_baseline
    used to silently advance the baseline — no SSE frame, no last-tick
    persistence — leaving the IngestActivityStrip blank even though save-tail
    was working. Now publishes a 0-event heartbeat + persists the last-tick
    fields so the user sees a signal that the chronicler is alive."""
    import json

    from chronicler.api.events import EventBus
    from chronicler.db.registry import create_campaign, get_campaign_by_id
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    db_path = tmp_path / "campaign.db"
    camp = create_campaign(
        "87pi heartbeat test",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-87pi",
        registry=registry_path,
    )
    # Deliberately do NOT add any tracked characters — exercises the
    # empty-tracked-set early-return.

    baseline = SaveSnapshot(
        playthrough_id="uuid-87pi",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    curr = SaveSnapshot(
        playthrough_id="uuid-87pi",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.2.1",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=True, death_date="1067.1.15")},
    )

    bus = EventBus()
    received: list[dict] = []
    monkey_publish = bus.publish

    def _capture(campaign_id: str, event: dict) -> None:
        received.append({"campaign_id": campaign_id, **event})
        monkey_publish(campaign_id, event)

    bus.publish = _capture  # type: ignore[method-assign]

    _advance_baseline(
        curr,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=camp.id,
        save_path_name="autosave.ck3",
        persist_path=tmp_path / "baseline.json",
        event_bus=bus,
    )

    # Persisted last-tick reflects the 0-event tick: filename + dates
    # populated, count is 0, tally is empty.
    fetched = get_campaign_by_id(camp.id, registry=registry_path)
    assert fetched is not None
    assert fetched.last_save_filename == "autosave.ck3"
    assert fetched.last_save_in_game_date == "1067.2.1"
    assert fetched.last_tick_event_count == 0
    assert json.loads(fetched.last_tick_event_type_tally or "{}") == {}
    assert fetched.last_save_ingested_at is not None

    # SSE frame fires with the heartbeat shape.
    completed = [e for e in received if e.get("kind") == "save_pair_completed"]
    assert len(completed) == 1
    assert completed[0]["save_filename"] == "autosave.ck3"
    assert completed[0]["event_count"] == 0
    assert completed[0]["event_type_tally"] == {}
    assert completed[0]["campaign_id"] == camp.id


# --- ck3_chronicler-kgqa: save_dropped_foreign SSE frame ---
class _RecorderBus:
    """Minimal EventBus stand-in that records every publish."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, campaign_id: str, event: dict) -> None:
        self.published.append((campaign_id, event))


def test_advance_baseline_publishes_save_dropped_foreign(tmp_path: Path, session_factory) -> None:
    """ck3_chronicler-kgqa: a save from an unrecognized playthrough is
    dropped AND announced on the bus so the FE can render the red
    'wrong game' badge. Today an unknown foreign save is silent."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    cid = "test-kgqa-foreign-frame"
    add_tracked_character(cid, 1234, note="player", registry=registry_path)

    base = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234, is_dead=False)})
    foreign = _snap_pt("uuid-UNKNOWN", "1068.4.15", {1234: _char(1234, is_dead=False)})
    bus = _RecorderBus()

    advanced = _advance_baseline(
        foreign,
        last_snapshot=base,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=cid,
        save_path_name="autosave.ck3",
        persist_path=None,
        event_bus=bus,
    )

    assert advanced is base  # baseline untouched
    frames = [e for _c, e in bus.published if e.get("kind") == "save_dropped_foreign"]
    assert len(frames) == 1
    assert frames[0]["observed_playthrough_id"] == "uuid-UNKNOWN"
    assert frames[0]["campaign_playthrough_id"] == "uuid-A"
    assert frames[0]["save_filename"] == "autosave.ck3"


def test_advance_baseline_no_foreign_frame_on_stale_read(tmp_path: Path, session_factory) -> None:
    """ck3_chronicler-kgqa: a stale read (same playthrough, earlier date)
    is NOT a wrong-game signal — no save_dropped_foreign frame."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    cid = "test-kgqa-stale-frame"
    add_tracked_character(cid, 1234, note="player", registry=registry_path)

    base = _snap_pt("uuid-A", "1068.4.15", {1234: _char(1234, is_dead=False)})
    stale = _snap_pt("uuid-A", "1067.1.1", {1234: _char(1234, is_dead=False)})
    bus = _RecorderBus()

    _advance_baseline(
        stale,
        last_snapshot=base,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=cid,
        save_path_name="autosave.ck3",
        persist_path=None,
        event_bus=bus,
    )

    frames = [e for _c, e in bus.published if e.get("kind") == "save_dropped_foreign"]
    assert frames == []
