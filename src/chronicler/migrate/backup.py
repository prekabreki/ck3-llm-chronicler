"""ck3_chronicler-72a: file-copy backup + restore for the migration tool.

Snapshots registry.db plus each campaign DB named in a MigrationPlan
into ``<data-dir>/backups/<ISO-timestamp>/``. Restore helpers copy
files back over their live locations. No compression, no incremental
diffs — sqlite chronicler files are small and simplicity wins.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chronicler.migrate.detector import MigrationPlan

_MANIFEST_SCHEMA_VERSION = 1


def _sqlite_backup(src: Path, dst: Path) -> None:
    """WAL-safe copy of a SQLite DB (ck3_chronicler-27ov.26 / audit M-P1).

    ``shutil.copy2`` copies only the main ``.db`` file — never the ``-wal`` /
    ``-shm`` sidecars, and never checkpoints. A backup taken while
    ``chronicler dev`` is writing in another terminal would silently miss every
    un-checkpointed WAL transaction, so a later restore loses them — exactly the
    post-patch scenario the playbook drives users into. ``Connection.backup()``
    reads through the WAL and is correct under concurrent writers (the same
    approach archive_export.py already uses)."""
    source = sqlite3.connect(str(src))
    try:
        dest = sqlite3.connect(str(dst))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


@dataclass(frozen=True)
class BackupListing:
    """One entry surfaced by GET /api/migrate/backups."""

    timestamp: str
    path: Path
    campaign_count: int


def _default_now_iso() -> str:
    """Path-safe timestamp: '2026-05-06T14-22-31' (colons → hyphens
    so Windows + zip tooling don't choke)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")


def snapshot(
    plan: MigrationPlan,
    *,
    registry_path: Path,
    data_dir: Path,
    chronicler_version: str = "0.3.0.dev0",
    _now_iso: Callable[[], str] = _default_now_iso,
) -> Path:
    """Write a backup to ``<data_dir>/backups/<timestamp>/``. Returns
    the backup directory path."""
    timestamp = _now_iso()
    backup_dir = data_dir / "backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "campaigns").mkdir(exist_ok=True)

    # Always copy the registry — it's small and the manifest needs to
    # describe registry state regardless of whether registry itself
    # needs migration.
    if registry_path.is_file():
        _sqlite_backup(registry_path, backup_dir / "registry.db")

    for pending in plan.needs_migration:
        src = Path(pending.db_path)
        if src.is_file():
            _sqlite_backup(src, backup_dir / "campaigns" / f"{pending.campaign_id}.db")

    manifest = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "chronicler_version": chronicler_version,
        "registry": {
            "pre_migration_missing_columns": list(plan.registry_missing_columns),
        },
        "campaigns": [
            {
                "id": p.campaign_id,
                "name": p.name,
                "pre_migration_head": p.current_head,
                "target_head": p.target_head,
            }
            for p in plan.needs_migration
        ],
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return backup_dir


def restore_one(backup_dir: Path, *, campaign_id: str, target_path: Path) -> None:
    """Copy a single per-campaign DB from a backup back over its live
    path. Caller is responsible for releasing any open SQLAlchemy
    connections beforehand."""
    src = backup_dir / "campaigns" / f"{campaign_id}.db"
    if not src.is_file():
        raise FileNotFoundError(f"backup missing for {campaign_id}: {src}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target_path)


def restore_registry(backup_dir: Path, *, target_path: Path) -> None:
    """Copy registry.db from a backup back over its live path."""
    src = backup_dir / "registry.db"
    if not src.is_file():
        raise FileNotFoundError(f"backup missing registry.db: {src}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target_path)


def list_backups(*, data_dir: Path) -> list[BackupListing]:
    """Enumerate timestamped backup directories under ``<data_dir>/backups/``.
    Newest first."""
    root = data_dir / "backups"
    if not root.is_dir():
        return []
    entries: list[BackupListing] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        campaign_count = 0
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text("utf-8"))
                campaign_count = len(manifest.get("campaigns") or [])
            except (OSError, ValueError):
                pass
        entries.append(
            BackupListing(timestamp=child.name, path=child, campaign_count=campaign_count)
        )
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries
