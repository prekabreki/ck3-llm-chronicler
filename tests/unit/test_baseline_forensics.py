"""Tests for baseline v2 forensic fields (ck3_chronicler-elll)."""

from __future__ import annotations

import json
from pathlib import Path

from chronicler.save.baseline import (
    _BASELINE_FORMAT_VERSION,
    BaselineLoad,
    load_baseline,
    save_baseline,
)
from chronicler.save.parse import SaveSnapshot


def _empty_snapshot(
    *,
    playthrough_id: str = "test-pid",
    current_date: str = "867.1.1",
) -> SaveSnapshot:
    """Minimal SaveSnapshot for round-trip tests.

    SaveSnapshot's required fields are ``playthrough_id`` /
    ``ck3_version`` / ``bookmark_date`` / ``current_date`` /
    ``player_character_id``; every collection / lookup field carries a
    ``default_factory`` so the rest fall through to empty containers.
    """
    return SaveSnapshot(
        playthrough_id=playthrough_id,
        ck3_version="1.19.0",
        bookmark_date=current_date,
        current_date=current_date,
        player_character_id=None,
    )


def test_save_baseline_writes_v2_with_generation_and_persisted_at(
    tmp_path: Path,
) -> None:
    """First persist of a fresh baseline writes format=2, gen=1, and
    an ISO persisted_at timestamp."""
    path = tmp_path / "b.json"
    save_baseline(path, _empty_snapshot())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 2
    assert payload["generation"] == 1
    assert "T" in payload["persisted_at"]
    assert payload["persisted_at"].endswith("+00:00")


def test_save_baseline_increments_generation_on_subsequent_writes(
    tmp_path: Path,
) -> None:
    """Every persist increments generation, reading the prior value off
    disk so the counter survives restarts."""
    path = tmp_path / "b.json"
    save_baseline(path, _empty_snapshot())
    save_baseline(path, _empty_snapshot())
    save_baseline(path, _empty_snapshot())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["generation"] == 3


def test_load_baseline_returns_baseline_load_with_forensic_fields(
    tmp_path: Path,
) -> None:
    """The new BaselineLoad dataclass exposes snapshot + gen + persisted_at."""
    path = tmp_path / "b.json"
    save_baseline(path, _empty_snapshot())
    loaded = load_baseline(path)
    assert loaded is not None
    assert isinstance(loaded, BaselineLoad)
    assert loaded.snapshot.playthrough_id == "test-pid"
    assert loaded.generation == 1
    assert loaded.persisted_at is not None


def test_load_baseline_discards_sparse_legacy_v1_files(tmp_path: Path) -> None:
    """ck3_chronicler-27ov.19 (audit M-D2): a legacy file whose snapshot
    dict is missing SaveSnapshot fields is DISCARDED, not tolerantly
    decoded. The tolerant decoders turn missing keys into empty prev
    indexes, and the first post-restart tick then re-fires whole
    histories (every dynasty perk, every ongoing war, the artifact
    vault) as phantom events. Rebaseline beats the phantom storm.

    This intentionally supersedes the original elll contract that
    sparse v1 files load with forensic fields None.
    """
    path = tmp_path / "b.json"
    legacy_payload = {
        "format_version": 1,
        "snapshot": {
            "playthrough_id": "legacy-pid",
            "ck3_version": "1.19.0",
            "bookmark_date": "867.1.1",
            "current_date": "867.1.1",
            "player_character_id": None,
            "characters": {},
        },
    }
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    assert load_baseline(path) is None


def test_load_baseline_v1_with_all_fields_still_loads(tmp_path: Path) -> None:
    """A v1 file that carries every SaveSnapshot field (i.e. written by
    a build with the same schema, just pre-elll forensics) still loads,
    with forensic fields None — the 27ov.19 gate only rejects files
    missing fields, not old format versions per se."""
    path = tmp_path / "b.json"
    save_baseline(path, _empty_snapshot())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["format_version"] = 1
    del payload["generation"]
    del payload["persisted_at"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot.playthrough_id == "test-pid"
    assert loaded.generation is None
    assert loaded.persisted_at is None


def test_load_baseline_discards_when_any_index_field_missing(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-27ov.19: drop ONE sister-index key from an
    otherwise-current baseline (simulating a persist from a build that
    predates that index) and the whole baseline is discarded — the
    generic gate covers every index without per-helper qz5y guards."""
    for dropped in ("dynasty_perks", "character_to_wars", "character_to_artifacts"):
        path = tmp_path / f"missing-{dropped}.json"
        save_baseline(path, _empty_snapshot())
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["snapshot"][dropped]
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_baseline(path) is None, dropped


def test_load_baseline_missing_file_returns_none(tmp_path: Path) -> None:
    """Pre-existing contract: no file → None, not raise."""
    assert load_baseline(tmp_path / "missing.json") is None


def test_load_baseline_unknown_future_format_returns_none(
    tmp_path: Path,
) -> None:
    """Pre-existing contract: format newer than we understand → ignore."""
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"format_version": 999, "snapshot": {}}), encoding="utf-8")
    assert load_baseline(path) is None


def test_baseline_format_version_constant_is_2() -> None:
    """Sanity check on the version constant — wire format is locked at v2.
    Bumping again requires updating the v1 legacy-load test above (and
    likely adding a v2 → v3 migration shim in load_baseline)."""
    assert _BASELINE_FORMAT_VERSION == 2


def test_chronicler_recovered_frame_includes_events_ingested() -> None:
    """The recovered frame closes the recovery window the recovering
    frame opened. events_ingested is the count delta of the events
    table across drain + catch-up."""
    from chronicler.api.events import EventBus

    bus = EventBus()
    queue, unsubscribe = bus.register("test-campaign")
    try:
        bus.publish(
            "test-campaign",
            {"kind": "chronicler_recovered", "events_ingested": 17},
        )
        assert queue.qsize() == 1
        frame = queue.get_nowait()
        assert frame["kind"] == "chronicler_recovered"
        assert frame["events_ingested"] == 17
    finally:
        unsubscribe()


def test_chronicler_recovering_frame_includes_forensic_fields(
    tmp_path: Path,
) -> None:
    """The chronicler_recovering frame surfaces baseline_generation +
    baseline_persisted_at so the AppShell banner can render the
    timestamp. Lives in the baseline-forensics file because it tests
    the wire surface of the new v2 fields, not the wider ingest loop.

    EventBus uses ``register`` + ``aiter_queue`` (no add_listener); for
    a synchronous test we use ``register`` to get the underlying queue
    and drain it via ``get_nowait``.
    """
    from chronicler.api.events import EventBus

    path = tmp_path / "b.json"
    save_baseline(path, _empty_snapshot())
    loaded = load_baseline(path)
    assert loaded is not None

    bus = EventBus()
    queue, unsubscribe = bus.register("test-campaign")
    try:
        bus.publish(
            "test-campaign",
            {
                "kind": "chronicler_recovering",
                "baseline_persisted_at": loaded.persisted_at,
                "baseline_date": loaded.snapshot.current_date,
                "baseline_generation": loaded.generation,
                "pending_cache_count": 0,
            },
        )
        assert queue.qsize() == 1
        frame = queue.get_nowait()
        assert frame["kind"] == "chronicler_recovering"
        assert frame["baseline_generation"] == 1
        assert frame["baseline_persisted_at"] is not None
        assert frame["baseline_date"] == "867.1.1"
        assert frame["pending_cache_count"] == 0
    finally:
        unsubscribe()
