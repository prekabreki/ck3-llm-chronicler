"""Tests for chronicler.migrate.backup — file-copy snapshot + restore."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from chronicler.migrate.backup import (
    list_backups,
    restore_one,
    restore_registry,
    snapshot,
)
from chronicler.migrate.detector import (
    MigrationPlan,
    PendingCampaignMigration,
)


def _make_sqlite_db(path: Path, marker: str) -> None:
    """Create a real SQLite DB carrying a single marker row. snapshot() now
    uses sqlite3 Connection.backup() (WAL-safe), so backup sources must be
    genuine SQLite files, not opaque bytes (ck3_chronicler-27ov.26)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _read_marker(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def _seed_files(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    """Create a tmp registry.db and two per-campaign DBs as real SQLite files
    with known marker rows."""
    registry = tmp_path / "registry.db"
    _make_sqlite_db(registry, "REGISTRY")
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    a = camp_dir / "alpha.db"
    b = camp_dir / "beta.db"
    _make_sqlite_db(a, "ALPHA")
    _make_sqlite_db(b, "BETA")
    return registry, {"alpha-id": a, "beta-id": b}


def test_snapshot_writes_manifest_and_db_copies(tmp_path: Path) -> None:
    registry, dbs = _seed_files(tmp_path)
    plan = MigrationPlan(
        needs_migration=[
            PendingCampaignMigration("alpha-id", "Alpha", str(dbs["alpha-id"]), None, "head1"),
            PendingCampaignMigration("beta-id", "Beta", str(dbs["beta-id"]), "old", "head1"),
        ],
        registry_needs_migration=True,
        registry_missing_columns=["campaigns.bookmark_date"],
    )
    backup_dir = snapshot(plan, registry_path=registry, data_dir=tmp_path)

    assert backup_dir.is_dir()
    assert _read_marker(backup_dir / "registry.db") == "REGISTRY"
    assert _read_marker(backup_dir / "campaigns" / "alpha-id.db") == "ALPHA"
    assert _read_marker(backup_dir / "campaigns" / "beta-id.db") == "BETA"
    manifest = json.loads((backup_dir / "manifest.json").read_text("utf-8"))
    assert manifest["schema_version"] == 1
    assert {c["id"] for c in manifest["campaigns"]} == {"alpha-id", "beta-id"}
    assert manifest["registry"]["pre_migration_missing_columns"] == ["campaigns.bookmark_date"]


def test_snapshot_skips_campaigns_not_in_plan(tmp_path: Path) -> None:
    """Campaigns not in plan.needs_migration aren't copied — backup only
    snapshots what we're about to touch (plus registry, always)."""
    registry, dbs = _seed_files(tmp_path)
    plan = MigrationPlan(
        needs_migration=[
            PendingCampaignMigration("alpha-id", "Alpha", str(dbs["alpha-id"]), None, "head1"),
        ],
    )
    backup_dir = snapshot(plan, registry_path=registry, data_dir=tmp_path)
    assert (backup_dir / "campaigns" / "alpha-id.db").is_file()
    assert not (backup_dir / "campaigns" / "beta-id.db").exists()


def test_restore_one_copies_backup_back(tmp_path: Path) -> None:
    registry, dbs = _seed_files(tmp_path)
    plan = MigrationPlan(
        needs_migration=[
            PendingCampaignMigration("alpha-id", "Alpha", str(dbs["alpha-id"]), None, "head1"),
        ],
    )
    backup_dir = snapshot(plan, registry_path=registry, data_dir=tmp_path)
    # Simulate a botched migration by overwriting the live file.
    dbs["alpha-id"].write_bytes(b"CORRUPT")
    restore_one(backup_dir, campaign_id="alpha-id", target_path=dbs["alpha-id"])
    assert _read_marker(dbs["alpha-id"]) == "ALPHA"


def test_restore_registry_copies_back(tmp_path: Path) -> None:
    registry, _dbs = _seed_files(tmp_path)
    plan = MigrationPlan(registry_needs_migration=True, registry_missing_columns=["x"])
    backup_dir = snapshot(plan, registry_path=registry, data_dir=tmp_path)
    registry.write_bytes(b"CORRUPT_REGISTRY")
    restore_registry(backup_dir, target_path=registry)
    assert _read_marker(registry) == "REGISTRY"


def test_snapshot_captures_uncheckpointed_wal_transactions(tmp_path: Path) -> None:
    """ck3_chronicler-27ov.26 (audit M-P1): a WAL-mode DB written by a
    concurrent process (`chronicler dev` in another terminal) keeps committed
    rows in the -wal sidecar until a checkpoint. shutil.copy2 copies only the
    main .db and misses them, so a later restore silently loses every WAL
    transaction — defeating the safety net for a destructive op. The backup
    must use sqlite3 Connection.backup(), which reads through the WAL.

    Simulated by keeping a writer connection open (no checkpoint) across the
    backup."""
    registry = tmp_path / "registry.db"
    _make_sqlite_db(registry, "REGISTRY")
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    live = camp_dir / "live.db"

    writer = sqlite3.connect(str(live))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE t (v TEXT)")
        writer.execute("INSERT INTO t (v) VALUES ('uncheckpointed')")
        writer.commit()
        # Deliberately do NOT checkpoint or close — the row lives in -wal,
        # exactly as it would with a concurrent save-tail writer.
        plan = MigrationPlan(
            needs_migration=[
                PendingCampaignMigration("live-id", "Live", str(live), None, "head1"),
            ],
        )
        backup_dir = snapshot(plan, registry_path=registry, data_dir=tmp_path)
    finally:
        writer.close()

    backed_up = backup_dir / "campaigns" / "live-id.db"
    assert _read_marker(backed_up) == "uncheckpointed", (
        "backup missed an un-checkpointed WAL transaction (audit M-P1)"
    )


def test_list_backups_returns_sorted_descending(tmp_path: Path) -> None:
    registry, _dbs = _seed_files(tmp_path)
    plan = MigrationPlan(registry_needs_migration=True)
    a = snapshot(
        plan,
        registry_path=registry,
        data_dir=tmp_path,
        _now_iso=lambda: "2026-05-01T10-00-00",
    )
    b = snapshot(
        plan,
        registry_path=registry,
        data_dir=tmp_path,
        _now_iso=lambda: "2026-05-02T10-00-00",
    )
    listings = list_backups(data_dir=tmp_path)
    assert [x.timestamp for x in listings] == ["2026-05-02T10-00-00", "2026-05-01T10-00-00"]
    assert listings[0].path == b
    assert listings[1].path == a
