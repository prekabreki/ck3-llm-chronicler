"""_advance_baseline playthrough/foreign-save guards: wrong-playthrough
drop+recover, foreign-playthrough auto-resume (and its SSE frame),
stale-read handling, cache-drain refusal, and diff ValueError safety.

Split from the test_save_ingest monolith (ck3_chronicler-27ov.72)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from chronicler.db import (
    Event,
)
from tests.helpers.ingest import (
    _char,
    _snap_pt,
)


# --- ck3_chronicler-7on: wrong-playthrough save must not wedge baseline ---
def test_advance_baseline_drops_wrong_playthrough_save_without_resetting_baseline(
    tmp_path: Path, session_factory
) -> None:
    """v0.7 smoke wedge: a stray cached save from a different playthrough
    has a later in-game date than our pinned baseline. The pre-fix code
    bypassed the date guard, hit the diff layer's playthrough_id check,
    raised ValueError, and silently installed the wrong-playthrough
    snap as the new baseline. Every subsequent right-playthrough save
    then got swallowed by the forward-only stale-read guard.

    Post-fix: wrong-playthrough save is dropped immediately, baseline
    untouched, ingest loop keeps moving."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_id = "test-campaign-7on"
    add_tracked_character(campaign_id, 1234, note="player", registry=registry_path)

    baseline = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234, is_dead=False)})
    # Stray save from a different campaign cached by the watcher before
    # the pin; later in-game date than the baseline, so the date guard
    # alone wouldn't catch it.
    stray = _snap_pt(
        "uuid-B",
        "1068.4.15",
        {1234: _char(1234, is_dead=True, death_date="1067.6.1")},
    )

    advanced = _advance_baseline(
        stray,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_id,
        save_path_name="autosave_2.ck3",
        persist_path=None,
    )

    # Baseline must be unchanged — same object identity since we didn't
    # advance.
    assert advanced is baseline
    # No events ingested from the wrong-playthrough diff.
    with session_factory() as s:
        rows = s.execute(select(Event)).scalars().all()
    assert rows == []


def test_advance_baseline_recovers_after_wrong_playthrough_save(
    tmp_path: Path, session_factory
) -> None:
    """The right-playthrough save that follows a stray must still ingest
    cleanly even though its in-game date is *earlier* than the strayed
    save's. Pre-fix, the stray installed itself as the baseline and the
    forward-only guard then dropped every right-playthrough save."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_id = "test-campaign-7on-recovery"
    add_tracked_character(campaign_id, 1234, note="player", registry=registry_path)

    baseline = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234, is_dead=False)})
    stray = _snap_pt("uuid-B", "1068.4.15", {1234: _char(1234, is_dead=False)})
    # Right-playthrough save with a date earlier than the stray's, but
    # later than the baseline — the realistic catch-up case.
    real_next = _snap_pt(
        "uuid-A",
        "1067.1.15",
        {1234: _char(1234, is_dead=True, death_date="1066.12.1")},
    )

    after_stray = _advance_baseline(
        stray,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_id,
        save_path_name="autosave_2.ck3",
        persist_path=None,
    )
    advanced = _advance_baseline(
        real_next,
        last_snapshot=after_stray,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_id,
        save_path_name="autosave.ck3",
        persist_path=None,
    )

    assert advanced is real_next
    with session_factory() as s:
        rows = s.execute(select(Event)).scalars().all()
    deaths = [r for r in rows if r.event_type == "death"]
    assert len(deaths) == 1
    assert deaths[0].event_date == "1066.12.1"


def test_is_advance_candidate_returns_reason() -> None:
    """ck3_chronicler-kgqa: the guard returns WHY a save is dropped so
    _advance_baseline can emit save_dropped_foreign only on a true
    playthrough mismatch (not a stale read)."""
    from chronicler.save.ingest import _is_advance_candidate

    base = _snap_pt("uuid-A", "1068.4.15", {1234: _char(1234, is_dead=False)})
    ok = _snap_pt("uuid-A", "1069.1.1", {1234: _char(1234, is_dead=False)})
    foreign = _snap_pt("uuid-B", "1069.1.1", {1234: _char(1234, is_dead=False)})
    stale = _snap_pt("uuid-A", "1067.1.1", {1234: _char(1234, is_dead=False)})

    assert _is_advance_candidate(ok, base, "autosave.ck3") == "ok"
    assert _is_advance_candidate(foreign, base, "autosave.ck3") == "foreign"
    assert _is_advance_candidate(stale, base, "autosave.ck3") == "stale"


# --- ck3_chronicler-3v0s: auto-resume known foreign campaign on mismatch ---
def test_advance_baseline_calls_on_foreign_playthrough_when_save_matches_other_campaign(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-3v0s: when save-tail is pinned to campaign A and a
    save from a *different* known-active campaign B arrives, we drop the
    save (preserving A's baseline) AND fire the on_foreign_playthrough
    callback with B's campaign_id + db_path. The orchestrator wires that
    callback to spawn_save_ingest_for_campaign so B starts being tailed
    concurrently — alt-tabbing between in-progress campaigns becomes
    hands-off."""
    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    # Campaign A is what save-tail is pinned to. Campaign B is the other
    # in-progress campaign whose save just landed.
    campaign_a_id = "test-3v0s-a"
    add_tracked_character(campaign_a_id, 1234, note="player", registry=registry_path)

    campaign_b = create_campaign(
        "Sleggja 867",
        db_path=str(tmp_path / "campaign_b.db"),
        ck3_playthrough_id="uuid-B",
        registry=registry_path,
    )

    baseline_a = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234, is_dead=False)})
    foreign_save = _snap_pt("uuid-B", "0870.4.15", {1234: _char(1234, is_dead=False)})

    calls: list[tuple[str, Path]] = []

    def _on_foreign(camp_id: str, db_path: Path) -> None:
        calls.append((camp_id, db_path))

    advanced = _advance_baseline(
        foreign_save,
        last_snapshot=baseline_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_a_id,
        save_path_name="autosave.ck3",
        persist_path=None,
        on_foreign_playthrough=_on_foreign,
    )

    # Baseline must be unchanged — same drop-without-touching-baseline
    # contract as ck3_chronicler-7on.
    assert advanced is baseline_a
    # And the callback fired exactly once with campaign B's coordinates.
    assert calls == [(campaign_b.id, Path(campaign_b.db_path))]


def test_advance_baseline_does_not_call_on_foreign_playthrough_when_save_is_unknown(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-3v0s: when the foreign save's playthrough_id does
    NOT match any active campaign in the registry, we keep the existing
    drop-with-warning behavior. Auto-adopting an unknown save is a user
    decision (the '+ Adopt save' button) — never automatic."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_a_id = "test-3v0s-unknown"
    add_tracked_character(campaign_a_id, 1234, note="player", registry=registry_path)

    baseline_a = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234, is_dead=False)})
    # uuid-UNKNOWN doesn't exist in the registry — a save from a CK3
    # playthrough we've never seen before.
    unknown_save = _snap_pt("uuid-UNKNOWN", "1068.4.15", {1234: _char(1234, is_dead=False)})

    calls: list[tuple[str, Path]] = []

    def _on_foreign(camp_id: str, db_path: Path) -> None:
        calls.append((camp_id, db_path))

    advanced = _advance_baseline(
        unknown_save,
        last_snapshot=baseline_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_a_id,
        save_path_name="autosave.ck3",
        persist_path=None,
        on_foreign_playthrough=_on_foreign,
    )

    assert advanced is baseline_a
    assert calls == []


def test_advance_baseline_does_not_call_on_foreign_playthrough_for_stale_reads(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-3v0s: stale-read drops (same playthrough, earlier
    date — CK3's atomic-write race) must NOT fire the foreign-playthrough
    callback. Only true playthrough mismatches should trigger the
    auto-resume lookup."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_a_id = "test-3v0s-stale"
    add_tracked_character(campaign_a_id, 1234, note="player", registry=registry_path)

    baseline_a = _snap_pt("uuid-A", "1068.4.15", {1234: _char(1234, is_dead=False)})
    # Same playthrough, earlier date — stale read.
    stale_read = _snap_pt("uuid-A", "1067.1.15", {1234: _char(1234, is_dead=False)})

    calls: list[tuple[str, Path]] = []

    def _on_foreign(camp_id: str, db_path: Path) -> None:
        calls.append((camp_id, db_path))

    advanced = _advance_baseline(
        stale_read,
        last_snapshot=baseline_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_a_id,
        save_path_name="autosave.ck3",
        persist_path=None,
        on_foreign_playthrough=_on_foreign,
    )

    assert advanced is baseline_a
    assert calls == []


def test_advance_baseline_does_not_self_resume_when_match_is_current_campaign(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-27ov.2 (audit H4): if a hijacked baseline makes the
    campaign's OWN current-playthrough saves look 'foreign',
    find_active_campaign_for_playthrough resolves the lookup back to THIS
    campaign. Firing on_foreign_playthrough with our own id makes the
    orchestrator cancel our own ingest (self-cancel wedge). The notifier must
    exclude self-matches."""
    from chronicler.db.registry import create_campaign
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    # Campaign A's TRUE playthrough is uuid-B; its baseline got hijacked to
    # uuid-A by a stale autosave during the startup drain.
    campaign_a = create_campaign(
        "Sleggja 867",
        db_path=str(tmp_path / "campaign_a.db"),
        ck3_playthrough_id="uuid-B",
        registry=registry_path,
    )
    hijacked_baseline = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234)})
    # A real save from campaign A's own playthrough — looks foreign vs the
    # hijacked baseline and resolves back to campaign A itself.
    own_save = _snap_pt("uuid-B", "0870.4.15", {1234: _char(1234)})

    calls: list[tuple[str, Path]] = []

    def _on_foreign(camp_id: str, db_path: Path) -> None:
        calls.append((camp_id, db_path))

    advanced = _advance_baseline(
        own_save,
        last_snapshot=hijacked_baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_a.id,
        save_path_name="autosave.ck3",
        persist_path=None,
        on_foreign_playthrough=_on_foreign,
    )

    assert advanced is hijacked_baseline
    assert calls == [], "must not auto-resume/cancel against our own campaign"


@pytest.mark.asyncio
async def test_startup_drain_refuses_foreign_save_as_baseline_when_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-27ov.2 (audit H4): with no prior baseline, draining the
    startup backlog must NOT adopt a foreign-playthrough cached save as the
    baseline when the campaign DB is pinned to a different playthrough.
    Otherwise a leftover autosave_exit.ck3 hijacks the baseline and every
    subsequent right-playthrough save is dropped as foreign — wedging ingest.

    27ov.12 collapsed the bespoke startup drain into the live consumer, so the
    foreign cached save is seeded into the queue (no baseline yet → it can't be
    sidecar-skipped) and dropped by the consumer's bootstrap pin gate. This
    drives the whole path through run_save_ingest rather than the old
    _drain_pending_cache helper."""
    from chronicler.db import Base, make_engine_for_path, make_session_factory
    from chronicler.db.engine import session_scope
    from chronicler.db.repository import assert_playthrough_or_pin
    from chronicler.save.baseline import baseline_path_for, load_baseline
    from chronicler.save.cache import SaveCache, cache_dir_for
    from chronicler.save.ingest import run_save_ingest
    from tests.helpers.ingest import _no_op_watch, _patch_parse_save

    # save_dir empty: no current on-disk save to seed, so the foreign cached
    # entry is the only seeded item.
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "chronicler-data"
    campaign_id = "test-27ov2"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    # Pin the campaign DB to our playthrough.
    with session_scope(factory) as session:
        assert_playthrough_or_pin(session, observed="uuid-OURS", pin_if_unset=True)
    engine.dispose()

    # A cached save from a DIFFERENT playthrough (no sidecar tag → it parses).
    cache = SaveCache(cache_dir_for(data_dir, campaign_id))
    src = tmp_path / "autosave_exit.ck3"
    src.write_bytes(b"fake save bytes")
    cached = cache.cache_save(src)
    assert cached is not None

    foreign_snap = _snap_pt("uuid-FOREIGN", "1066.9.15", {1234: _char(1234)})
    _patch_parse_save(monkeypatch, lambda path: ({}, foreign_snap))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    # Refused: no baseline established from the foreign save.
    assert load_baseline(baseline_path_for(db_path)) is None
    # Entry consumed so the drain doesn't spin on it forever.
    assert SaveCache(cache_dir_for(data_dir, campaign_id)).pending() == []


# --- ck3_chronicler-n52f: SSE frame for auto-resume ---
def test_advance_baseline_publishes_campaign_auto_resumed_sse_frame(
    tmp_path: Path, session_factory
) -> None:
    """ck3_chronicler-n52f: when auto-resume fires, _advance_baseline
    publishes a 'campaign_auto_resumed' SSE frame on the CURRENT
    campaign's bus (the one the SPA is subscribed to). The frame carries
    the foreign campaign's id+name so the SPA can render a toast with
    a one-click [Switch] action."""
    from chronicler.api.events import EventBus
    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_a_id = "test-n52f-current"
    add_tracked_character(campaign_a_id, 1234, note="player", registry=registry_path)

    campaign_b = create_campaign(
        "Sleggja 867",
        db_path=str(tmp_path / "campaign_b.db"),
        ck3_playthrough_id="uuid-B",
        registry=registry_path,
    )

    baseline_a = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234)})
    foreign_save = _snap_pt("uuid-B", "0870.4.15", {1234: _char(1234)})

    bus = EventBus()
    published: list[tuple[str, dict]] = []
    bus.publish = lambda cid, event: published.append((cid, event))  # type: ignore[method-assign]

    _advance_baseline(
        foreign_save,
        last_snapshot=baseline_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_a_id,
        save_path_name="autosave.ck3",
        persist_path=None,
        event_bus=bus,
        on_foreign_playthrough=lambda *args: None,
    )

    auto_resumed = [
        (cid, evt) for cid, evt in published if evt.get("kind") == "campaign_auto_resumed"
    ]
    assert len(auto_resumed) == 1
    cid, evt = auto_resumed[0]
    # Frame published on the CURRENT campaign's bus (so the SPA, which
    # is subscribed to whatever campaign it's focused on, sees it).
    assert cid == campaign_a_id
    assert evt["campaign_id"] == campaign_b.id
    assert evt["campaign_name"] == "Sleggja 867"
    assert evt["save_path_name"] == "autosave.ck3"
    assert evt["in_game_date"] == "0870.4.15"


def test_advance_baseline_does_not_publish_auto_resumed_without_bus(
    tmp_path: Path, session_factory
) -> None:
    """No event_bus → no publish → no AttributeError. Headless callers
    (CLI save-tail standalone) must keep working without a bus."""
    from chronicler.db.registry import (
        add_tracked_character,
        create_campaign,
    )
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_a_id = "test-n52f-nobus"
    add_tracked_character(campaign_a_id, 1234, note="player", registry=registry_path)

    create_campaign(
        "Sleggja 867",
        db_path=str(tmp_path / "campaign_b.db"),
        ck3_playthrough_id="uuid-B",
        registry=registry_path,
    )

    baseline_a = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234)})
    foreign_save = _snap_pt("uuid-B", "0870.4.15", {1234: _char(1234)})

    # Should complete without error despite no event_bus.
    _advance_baseline(
        foreign_save,
        last_snapshot=baseline_a,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_a_id,
        save_path_name="autosave.ck3",
        persist_path=None,
        event_bus=None,
        on_foreign_playthrough=lambda *args: None,
    )


# --- ck3_chronicler-2faj: cache sidecar lets drain skip rakaly ---
@pytest.mark.asyncio
async def test_startup_drain_skips_rakaly_for_foreign_tagged_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-2faj: when a cached save's playthrough_id sidecar says it
    belongs to a different playthrough than the campaign's baseline, the
    startup backlog drain must skip the rakaly parse entirely — the ~7s/save
    win. 27ov.12 preserves this by sidecar-filtering the seeded backlog BEFORE
    it reaches the consumer (which would otherwise parse-then-skip); the live
    tail is single-playthrough and never hits the foreign branch anyway.

    Assertion mechanism: a persisted baseline pins last_snapshot to our
    playthrough, and the parse stub raises if called. A passing test means the
    seeding fast-path dropped the foreign-tagged entry before any parse."""
    from chronicler.db import Base, make_engine_for_path
    from chronicler.save.baseline import baseline_path_for, load_baseline, save_baseline
    from chronicler.save.cache import SaveCache, cache_dir_for
    from chronicler.save.ingest import run_save_ingest
    from tests.helpers.ingest import _no_op_watch, _patch_parse_save

    # save_dir empty: no current on-disk save (which the parse stub would
    # otherwise be asked to parse) — the foreign-tagged cached entry is the
    # only backlog item.
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"
    data_dir = tmp_path / "chronicler-data"
    campaign_id = "test-2faj"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    # Persisted baseline pins last_snapshot to our playthrough at startup, so
    # the seeding step can recognise the cached entry as foreign by sidecar.
    baseline = _snap_pt("uuid-OURS", "1066.9.15", {1234: _char(1234)})
    save_baseline(baseline_path_for(db_path), baseline)

    # A cached entry tagged with a foreign playthrough_id sidecar.
    cache = SaveCache(cache_dir_for(data_dir, campaign_id))
    src = tmp_path / "autosave.ck3"
    src.write_bytes(b"fake save bytes - should never be rakaly-parsed")
    cached = cache.cache_save(src)
    assert cached is not None
    cache.tag_playthrough(cached, "uuid-FOREIGN")

    # If rakaly gets called for this entry, fail the test loudly.
    def _fail_if_called(path):
        raise AssertionError(
            f"rakaly parse should be skipped for foreign-tagged cache "
            f"entry, but was invoked on {path}"
        )

    _patch_parse_save(monkeypatch, _fail_if_called)
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)
    monkeypatch.setattr("chronicler.save.ingest.get_data_dir", lambda: data_dir)

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id=campaign_id,
        registry_path=registry_path,
    )

    # Baseline untouched (foreign entry never advanced it).
    loaded = load_baseline(baseline_path_for(db_path))
    assert loaded is not None
    assert loaded.snapshot.playthrough_id == "uuid-OURS"
    # The cached entry was mark_processed: file + sidecar deleted.
    assert not cached.path.exists()
    assert SaveCache(cache_dir_for(data_dir, campaign_id)).pending() == []


def test_advance_baseline_preserves_baseline_on_diff_value_error(
    tmp_path: Path,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """audit F-26 / ck3_chronicler-71cf: belt-and-suspenders for the
    post-diff ValueError handler (ingest.py:497-512). The pre-diff
    playthrough check normally drops wrong-playthrough saves before
    they reach the diff layer, but if diff_snapshots raises ValueError
    for some other reason, the handler must drop the save and preserve
    the existing baseline.

    Pre-fix behaviour the comment describes: installing snap as the new
    baseline wedged ingest because the next right-playthrough save
    arrived with an earlier date and got swallowed by the stale-read
    guard. We assert here that on a forced ValueError, the returned
    snapshot identity is the *original* baseline, not snap, and no
    events are ingested."""
    from chronicler.db.registry import add_tracked_character
    from chronicler.save import tick as tick_mod
    from chronicler.save.ingest import _advance_baseline

    registry_path = tmp_path / "registry.db"
    campaign_id = "test-campaign-71cf"
    add_tracked_character(campaign_id, 1234, note="player", registry=registry_path)

    baseline = _snap_pt("uuid-A", "1066.9.15", {1234: _char(1234, is_dead=False)})
    # Same playthrough, later date — passes the pre-diff guards so we
    # actually reach the diff_snapshots call.
    snap = _snap_pt(
        "uuid-A",
        "1067.1.15",
        {1234: _char(1234, is_dead=True, death_date="1066.12.1")},
    )

    # Force diff_snapshots to raise ValueError for an unrelated reason
    # (e.g. malformed diff input, non-playthrough invariant violation).
    # 27ov.40: _safe_diff (and its diff_snapshots call) moved to save.tick.
    def _explode(*_args, **_kwargs):
        raise ValueError("synthetic diff error")

    monkeypatch.setattr(tick_mod, "diff_snapshots", _explode)

    advanced = _advance_baseline(
        snap,
        last_snapshot=baseline,
        factory=session_factory,
        scheduler=None,
        registry_path=registry_path,
        campaign_id=campaign_id,
        save_path_name="autosave.ck3",
        persist_path=None,
    )

    # Baseline preserved (object identity), no events written.
    assert advanced is baseline
    with session_factory() as s:
        rows = s.execute(select(Event)).scalars().all()
    assert rows == []
