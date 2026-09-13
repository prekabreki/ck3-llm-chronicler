"""Tests for chronicler.migrate.migrator — orchestrator + auto-restore."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from chronicler.db.registry import create_campaign
from chronicler.migrate.migrator import (
    SaveTailRunningError,
    restore_from_backup,
    run_migration,
)


def _table_names(db_path: Path) -> set[str]:
    """User tables in a SQLite DB. Used to assert restored DB content/state
    instead of raw bytes — snapshot() uses WAL-safe Connection.backup() which
    is a logical copy, not a byte-identical one (ck3_chronicler-27ov.26)."""
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _seed_pre_alembic_campaign(tmp_path: Path, name: str = "Alpha") -> Path:
    """Build an *empty* per-campaign DB file + register it.

    Empty (no tables, no alembic_version) so alembic upgrade head runs
    the full migration chain from baseline without colliding with
    pre-existing tables. Mirrors the production "fresh campaign DB"
    state: cli/main.py:_alembic_upgrade calls into an empty file."""
    registry = tmp_path / "registry.db"
    db = tmp_path / "campaigns" / f"{name.lower()}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.touch()
    create_campaign(name, db_path=str(db), registry=registry)
    return registry


def test_run_migration_empty_plan_returns_success_no_backup(tmp_path: Path) -> None:
    registry = tmp_path / "registry.db"
    result = run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=False)
    assert result.success is True
    assert result.backup_dir is None
    assert result.results == []


def test_run_migration_refuses_when_scheduler_running(tmp_path: Path) -> None:
    registry = _seed_pre_alembic_campaign(tmp_path)
    with pytest.raises(SaveTailRunningError):
        run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=True)


def test_run_migration_happy_path_upgrades_pre_alembic_db(tmp_path: Path) -> None:
    """A fresh DB without alembic_version gets upgraded to head; backup
    is taken; per-row result is ok=True."""
    registry = _seed_pre_alembic_campaign(tmp_path)
    result = run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=False)
    assert result.success is True
    assert result.backup_dir is not None and result.backup_dir.is_dir()
    assert any(r.id != "registry" and r.ok for r in result.results)


def test_run_migration_still_upgrades_archived_campaign(tmp_path: Path) -> None:
    """ck3_chronicler-4zdg: archived (completed) campaigns are hidden from
    the migration UI but must STILL be migrated under the hood, or their
    read-only pages 500 on a stale schema (sj31). run_migration re-scans
    and sweeps everything, so a sealed campaign upgrades to head like any
    other. This guards against a future change that filters archived out
    of the migrator instead of only the UI."""
    from chronicler.db.registry import archive_campaign, get_campaign_by_name

    registry = _seed_pre_alembic_campaign(tmp_path, name="Sealed")
    sealed = get_campaign_by_name("Sealed", include_archived=True, registry=registry)
    assert sealed is not None
    archive_campaign(sealed.id, registry=registry)

    result = run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=False)
    assert result.success is True
    assert any(r.id == sealed.id and r.ok for r in result.results), (
        "archived campaign must still be migrated by run_migration"
    )


def _make_archive_dir(tmp_path: Path) -> Path:
    """Issue #24: an archive dir the user syncs with git — a .git marker
    above it, so the migrator's snapshot refresh takes the commit path."""
    repo = tmp_path / "syncrepo"
    (repo / ".git").mkdir(parents=True)
    archive_dir = repo / "archived"
    archive_dir.mkdir(parents=True)
    return archive_dir


def test_run_migration_skips_bootstrapped_in_repo_snapshots(tmp_path: Path) -> None:
    """ck3_chronicler-27ov.28 (M-P3): a bootstrapped registry row whose
    db_path points inside the archive dir is a projection of another
    machine's export — alembic-upgrading it in place rewrites bytes the
    owning machine is authoritative for (and dirties the user's sync repo
    when that dir is one). It must be excluded from the sweep."""
    from chronicler.db.registry import archive_campaign, get_campaign_by_name

    archive_dir = _make_archive_dir(tmp_path)
    snap_db = archive_dir / "boot.db"
    snap_db.touch()  # empty pre-alembic stub — scan flags it as pending
    registry = tmp_path / "registry.db"
    create_campaign("Boot", db_path=str(snap_db), registry=registry)
    camp = get_campaign_by_name("Boot", include_archived=True, registry=registry)
    assert camp is not None
    archive_campaign(camp.id, registry=registry)

    result = run_migration(
        registry_path=registry,
        data_dir=tmp_path,
        scheduler_running=False,
        archive_dir=archive_dir,
    )

    assert result.success is True
    assert all(r.id != camp.id for r in result.results)
    # The git-tracked snapshot bytes were not touched.
    assert snap_db.read_bytes() == b""


def test_run_migration_refreshes_archived_snapshot_after_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-27ov.28 (M-P3): when the machine owning the live
    DB migrates an archived campaign that already has a snapshot, the
    snapshot is refreshed through the sync layer so secondary machines
    pick up a head-schema copy instead of 500ing on a stale one."""
    from chronicler.db.registry import archive_campaign, get_campaign_by_name
    from chronicler.sync.archive_export import ExportResult

    archive_dir = _make_archive_dir(tmp_path)
    registry = _seed_pre_alembic_campaign(tmp_path, name="Sealed")
    sealed = get_campaign_by_name("Sealed", include_archived=True, registry=registry)
    assert sealed is not None
    archive_campaign(sealed.id, registry=registry)
    # Snapshot already exported by a prior seal on this machine.
    (archive_dir / f"{sealed.id}.db").touch()

    refreshed: list[str] = []

    def _fake_export(campaign, *, archive_dir=None):
        refreshed.append(campaign.id)
        return ExportResult(
            snapshot_written=True,
            sidecar_written=True,
            committed=True,
            pushed=True,
            message="ok",
        )

    monkeypatch.setattr("chronicler.sync.archive_export.export_sealed_campaign", _fake_export)

    result = run_migration(
        registry_path=registry,
        data_dir=tmp_path,
        scheduler_running=False,
        archive_dir=archive_dir,
    )

    assert result.success is True
    assert refreshed == [sealed.id]


def test_run_migration_does_not_export_when_no_snapshot_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snapshot creation belongs to the txuo seal/backfill flow — the
    migrator only refreshes snapshots that already exist."""
    from chronicler.db.registry import archive_campaign, get_campaign_by_name

    archive_dir = _make_archive_dir(tmp_path)
    registry = _seed_pre_alembic_campaign(tmp_path, name="Sealed")
    sealed = get_campaign_by_name("Sealed", include_archived=True, registry=registry)
    assert sealed is not None
    archive_campaign(sealed.id, registry=registry)

    called: list[str] = []
    monkeypatch.setattr(
        "chronicler.sync.archive_export.export_sealed_campaign",
        lambda campaign, *, archive_dir=None: called.append(campaign.id),
    )

    result = run_migration(
        registry_path=registry,
        data_dir=tmp_path,
        scheduler_running=False,
        archive_dir=archive_dir,
    )

    assert result.success is True
    assert called == []


def test_run_migration_restores_on_alembic_failure(tmp_path: Path) -> None:
    """Patch the canonical upgrade runner (27ov.52) to raise on the
    first call. The DB must be byte-restored from the backup taken
    seconds earlier; the per-row result must record ok=False."""
    registry = _seed_pre_alembic_campaign(tmp_path)
    db_path = tmp_path / "campaigns" / "alpha.db"

    with patch("chronicler.migrate.migrator.upgrade_to_head") as up:
        up.side_effect = RuntimeError("boom")
        result = run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=False)
    assert result.success is False
    failing = [r for r in result.results if not r.ok and r.id != "registry"]
    assert failing
    assert "boom" in failing[0].error
    # The failed upgrade was rolled back to the pre-alembic state (no
    # alembic_version table) — the DB isn't left half-migrated.
    assert "alembic_version" not in _table_names(db_path)


def test_restore_from_backup_replaces_live_files(tmp_path: Path) -> None:
    """End-to-end: snapshot via run_migration → corrupt → restore_from_backup → bytes restored."""
    registry = _seed_pre_alembic_campaign(tmp_path)
    db_path = tmp_path / "campaigns" / "alpha.db"
    result = run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=False)
    assert result.backup_dir is not None
    db_path.write_bytes(b"CORRUPT_DB")
    registry.write_bytes(b"CORRUPT_REGISTRY")
    restored = restore_from_backup(
        result.backup_dir,
        registry_path=registry,
        data_dir=tmp_path,
        scheduler_running=False,
    )
    assert restored >= 1
    # Registry restored to a valid SQLite DB carrying its campaigns table
    # (the corrupt bytes are gone, and the real schema is back).
    assert "campaigns" in _table_names(registry)
    assert db_path.read_bytes() != b"CORRUPT_DB"


def test_restore_from_backup_refuses_when_scheduler_running(tmp_path: Path) -> None:
    registry = _seed_pre_alembic_campaign(tmp_path)
    result = run_migration(registry_path=registry, data_dir=tmp_path, scheduler_running=False)
    with pytest.raises(SaveTailRunningError):
        restore_from_backup(
            result.backup_dir,
            registry_path=registry,
            data_dir=tmp_path,
            scheduler_running=True,
        )
