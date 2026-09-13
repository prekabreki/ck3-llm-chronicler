"""ck3_chronicler-72a: migration orchestrator + auto-restore.

Composes detector + backup + alembic.command.upgrade. Single failure
mode: alembic raises → that DB is byte-restored from the backup taken
just before the upgrade started. Per-row results returned to the
caller so the API + CLI can surface "2 of 3 succeeded; 1 restored".
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from chronicler.db.migrate_runner import upgrade_to_head
from chronicler.db.registry import connect as _registry_connect
from chronicler.migrate.backup import (
    list_backups,
    restore_one,
    restore_registry,
    snapshot,
)
from chronicler.migrate.detector import (
    PendingCampaignMigration,
    scan,
)

log = logging.getLogger(__name__)


class SaveTailRunningError(RuntimeError):
    """Raised by run_migration / restore_from_backup when the active
    save-tail loop holds open handles to the campaign DBs. Mapped to
    HTTP 409 by the route layer."""


@dataclass(frozen=True)
class MigrationRowResult:
    """One row in the per-campaign / per-registry result list."""

    id: str  # campaign_id or 'registry'
    ok: bool
    error: str | None = None


@dataclass
class MigrationResult:
    success: bool
    backup_dir: Path | None
    results: list[MigrationRowResult] = field(default_factory=list)


def _upgrade_one(pending: PendingCampaignMigration) -> None:
    """Run alembic upgrade head on one per-campaign DB via the
    canonical shared runner (ck3_chronicler-27ov.52)."""
    upgrade_to_head(Path(pending.db_path))


def _drop_bootstrapped_snapshots(plan, *, archive_dir: Path | None) -> None:
    """ck3_chronicler-27ov.28 (audit M-P3): remove bootstrapped snapshot
    rows from the sweep — rows whose db_path points inside the archive
    dir are projections of another machine's export, not campaigns this
    machine owns. Upgrading one in place rewrites a file whose bytes the
    owning machine is authoritative for (and, when the archive dir is a
    git repo, leaves that checkout permanently dirty). Their bytes refresh
    through the sync layer instead: the machine that owns the live DB
    migrates it and re-exports (see :func:`_refresh_archived_snapshots`)."""
    if archive_dir is None:
        return
    from chronicler.sync.archive_export import _is_snapshot_projection

    kept = []
    for pending in plan.needs_migration:
        if _is_snapshot_projection(pending.db_path, archive_dir):
            log.info(
                "migrate: skipping bootstrapped snapshot %s (%s) — in-repo "
                "archive snapshots refresh via the owning machine's export, "
                "never by in-place migration",
                pending.name,
                pending.campaign_id,
            )
        else:
            kept.append(pending)
    plan.needs_migration = kept


def _refresh_archived_snapshots(
    migrated: list[PendingCampaignMigration],
    *,
    registry_path: Path,
    archive_dir: Path | None,
) -> None:
    """ck3_chronicler-27ov.28 (audit M-P3): after upgrading an archived
    campaign's live DB to head, re-export its snapshot through the sync
    layer so secondary machines pick up a head-schema snapshot instead of
    500ing on a stale one. Only refreshes snapshots that already exist —
    creation belongs to the txuo seal/backfill flow. Best-effort: export
    failures log and never fail the migration."""
    if archive_dir is None:
        return
    from chronicler.db.registry import get_campaign_by_id
    from chronicler.sync import archive_export

    for pending in migrated:
        if not pending.archived:
            continue
        if not (archive_dir / f"{pending.campaign_id}.db").exists():
            continue
        campaign = get_campaign_by_id(pending.campaign_id, registry=registry_path)
        if campaign is None:
            continue
        result = archive_export.export_sealed_campaign(campaign, archive_dir=archive_dir)
        if result is not None and not result.snapshot_written:
            log.warning(
                "migrate: snapshot refresh failed for %s: %s",
                pending.campaign_id,
                result.message,
            )
        else:
            log.info(
                "migrate: refreshed in-repo snapshot for archived campaign %s",
                pending.campaign_id,
            )


def _ensure_registry_schema(registry_path: Path) -> None:
    """Trigger registry's lazy ALTER TABLE migration by opening it via
    the canonical _connect path. Idempotent.

    Forces a fresh schema-init pass: clears the memo so the connect
    actually runs CREATE/ALTER even if another path opened this file
    earlier in the process."""
    from chronicler.db.registry._core import _INIT_LOCK, _INITIALIZED_PATHS

    with _INIT_LOCK:
        _INITIALIZED_PATHS.discard(str(registry_path.resolve()))
    with _registry_connect(registry_path):
        pass


def run_migration(
    *,
    registry_path: Path,
    data_dir: Path,
    scheduler_running: bool,
    archive_dir: Path | None = None,
) -> MigrationResult:
    """Detect pending migrations, take a backup, run upgrades, auto-
    restore on per-row failure.

    ``scheduler_running`` is the caller's check (route reads
    ``bool(app.state.narrative_schedulers)`` — ck3_chronicler-m4cn — and
    the CLI uses ``False`` since the CLI tool does not run alongside
    chronicler dev). Raises
    :class:`SaveTailRunningError` when ``scheduler_running`` is True
    and there is anything to migrate.

    ``archive_dir`` is where sealed-campaign snapshots live (resolved from
    settings/env/default when omitted; tests inject a tmp dir). Bootstrapped
    snapshots are never migrated in place, and successfully migrated
    archived campaigns get their snapshot refreshed — ck3_chronicler-27ov.28.
    """
    if archive_dir is None:
        from chronicler.sync.archive_export import (
            archive_sync_enabled,
            archived_campaigns_dir,
        )

        archive_dir = archived_campaigns_dir() if archive_sync_enabled() else None

    plan = scan(registry_path=registry_path)
    _drop_bootstrapped_snapshots(plan, archive_dir=archive_dir)
    if not plan.needs_migration and not plan.registry_needs_migration:
        return MigrationResult(success=True, backup_dir=None, results=[])

    if scheduler_running:
        raise SaveTailRunningError("save-tail is running; halt it before running schema migration")

    backup_dir = snapshot(plan, registry_path=registry_path, data_dir=data_dir)
    results: list[MigrationRowResult] = []
    migrated: list[PendingCampaignMigration] = []

    for pending in plan.needs_migration:
        try:
            _upgrade_one(pending)
        except Exception as e:
            log.exception("alembic upgrade failed for %s", pending.campaign_id)
            try:
                restore_one(
                    backup_dir,
                    campaign_id=pending.campaign_id,
                    target_path=Path(pending.db_path),
                )
            except Exception as restore_err:
                log.exception("restore_one also failed for %s", pending.campaign_id)
                results.append(
                    MigrationRowResult(
                        id=pending.campaign_id,
                        ok=False,
                        error=(f"upgrade failed: {e!r}; restore also failed: {restore_err!r}"),
                    )
                )
                continue
            results.append(
                MigrationRowResult(
                    id=pending.campaign_id,
                    ok=False,
                    error=f"{type(e).__name__}: {e}",
                )
            )
        else:
            results.append(MigrationRowResult(id=pending.campaign_id, ok=True))
            migrated.append(pending)

    _refresh_archived_snapshots(migrated, registry_path=registry_path, archive_dir=archive_dir)

    if plan.registry_needs_migration:
        try:
            _ensure_registry_schema(registry_path)
        except Exception as e:
            log.exception("registry _ensure_schema failed")
            try:
                restore_registry(backup_dir, target_path=registry_path)
            except Exception as restore_err:
                results.append(
                    MigrationRowResult(
                        id="registry",
                        ok=False,
                        error=(f"upgrade failed: {e!r}; restore also failed: {restore_err!r}"),
                    )
                )
            else:
                results.append(
                    MigrationRowResult(id="registry", ok=False, error=f"{type(e).__name__}: {e}")
                )
        else:
            results.append(MigrationRowResult(id="registry", ok=True))

    return MigrationResult(
        success=all(r.ok for r in results),
        backup_dir=backup_dir,
        results=results,
    )


def restore_from_backup(
    backup_dir: Path,
    *,
    registry_path: Path,
    data_dir: Path,
    scheduler_running: bool,
) -> int:
    """User-initiated restore: copy the registry + every per-campaign
    DB in this backup back over the live locations. Returns the count
    of files restored.

    Per-campaign target paths are resolved by looking up each id in
    the registry so campaigns registered with a non-default db_path
    (custom location, test fixtures, etc.) restore to the right
    location. The registry must be restored first so the lookup uses
    the pre-migration layout."""
    if scheduler_running:
        raise SaveTailRunningError("save-tail is running; halt it before restoring from backup")
    if not backup_dir.is_dir():
        raise FileNotFoundError(f"backup not found: {backup_dir}")

    # Restore the registry first so the per-campaign path lookups
    # below see the pre-migration db_path values.
    restored = 0
    registry_backup = backup_dir / "registry.db"
    if registry_backup.is_file():
        restore_registry(backup_dir, target_path=registry_path)
        restored += 1

    # Look up each campaign in the freshly-restored registry to find
    # its real db_path, then copy the backup over that location. Falls
    # back to the default <data-dir>/campaigns/<id>.db layout when the
    # campaign isn't in the registry (e.g. registry without a registry.db
    # in the backup, or campaign deleted after backup).
    from chronicler.db.registry import get_campaign_by_id

    campaigns_dir = backup_dir / "campaigns"
    if campaigns_dir.is_dir():
        for child in campaigns_dir.iterdir():
            if child.suffix != ".db" or not child.is_file():
                continue
            campaign_id = child.stem
            target: Path
            camp = get_campaign_by_id(campaign_id, registry=registry_path)
            if camp is not None:
                target = Path(camp.db_path)
            else:
                target = data_dir / "campaigns" / f"{campaign_id}.db"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)
            restored += 1
    return restored


__all__ = [
    "MigrationResult",
    "MigrationRowResult",
    "SaveTailRunningError",
    "list_backups",
    "restore_from_backup",
    "run_migration",
]
