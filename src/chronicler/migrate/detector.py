"""ck3_chronicler-72a: schema mismatch detection.

Pure read-only scan. For each per-campaign DB compares the stamped
alembic head against the alembic ScriptDirectory's resolved head.
For the registry, introspects PRAGMA table_info(<table>) against the
``_*_REQUIRED_COLUMNS`` tuples in :mod:`chronicler.db.registry` —
those tuples encode 'what columns this chronicler version expects'.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from chronicler.config import find_repo_root
from chronicler.db.registry import list_campaigns
from chronicler.db.registry.campaigns import _CAMPAIGNS_REQUIRED_COLUMNS
from chronicler.db.registry.tracked import _TRACKED_REQUIRED_COLUMNS


@dataclass(frozen=True)
class PendingCampaignMigration:
    """One per-campaign DB whose alembic head is behind the chronicler's
    expected head. ``current_head`` is None for pre-alembic DBs."""

    campaign_id: str
    name: str
    db_path: str
    current_head: str | None
    target_head: str
    # ck3_chronicler-4zdg: archived (= "Completed"/sealed) campaigns are
    # still scanned + migrated (sj31 — their read-only pages 500 on a
    # stale schema), but the migration UI hides them. This flag lets the
    # /api/migrate/status route filter them out of the banner + panel
    # without affecting run_migration, which sweeps the full plan.
    archived: bool = False


@dataclass
class MigrationPlan:
    needs_migration: list[PendingCampaignMigration] = field(default_factory=list)
    registry_needs_migration: bool = False
    registry_missing_columns: list[str] = field(default_factory=list)


def _alembic_target_head() -> str:
    """Resolve the head revision the chronicler expects all per-campaign
    DBs to reach. Reads alembic.ini at the repo root."""
    cfg = Config(str(find_repo_root() / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("alembic ScriptDirectory has no head revision")
    return head


def _read_alembic_head(db_path: Path) -> str | None:
    """Return the stamped alembic head for a per-campaign DB, or None
    if the DB has no ``alembic_version`` table."""
    if not db_path.is_file():
        return None
    # Issue #54: `closing`, not a bare `with` — the connection form that
    # commits but never closes leaves a handle that Windows refuses to let
    # anyone rename or replace. These are read-only probes, but they run in
    # the migrate/patch-playbook flow, which backs a campaign DB up and
    # swaps it out; a leaked reader would block exactly that.
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        )
        if cur.fetchone() is None:
            return None
        cur = conn.execute("SELECT version_num FROM alembic_version LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None


def _scan_registry(registry_path: Path) -> tuple[bool, list[str]]:
    """Check the registry's campaigns + tracked_characters tables for
    missing columns. Returns ``(needs_migration, missing_column_names)``.
    The list is unqualified column names; tests assert membership."""
    if not registry_path.is_file():
        return False, []
    missing: list[str] = []
    with closing(sqlite3.connect(registry_path)) as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='campaigns'")
        if cur.fetchone() is None:
            # Registry is empty/never-initialised — _ensure_schema will
            # create it on next connect; not a "needs migration" case.
            return False, []
        for table, expected in (
            ("campaigns", _CAMPAIGNS_REQUIRED_COLUMNS),
            ("tracked_characters", _TRACKED_REQUIRED_COLUMNS),
        ):
            cur = conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            )
            if cur.fetchone() is None:
                continue
            cur = conn.execute(f"PRAGMA table_info({table})")
            present = {row[1] for row in cur.fetchall()}
            for col_name, _col_type in expected:
                if col_name not in present:
                    missing.append(col_name)
    return (len(missing) > 0, missing)


def scan(*, registry_path: Path) -> MigrationPlan:
    """Build a migration plan from the current registry + per-campaign DBs.

    Scans the registry FIRST via raw sqlite3 (no _connect), because
    _connect() runs the lazy ``ALTER TABLE ADD COLUMN`` migration as a
    side effect — calling list_campaigns first would auto-fix the
    registry schema before we get to inspect it. Per-campaign DB walk
    follows; SQLAlchemy doesn't have the same auto-mutation behaviour.

    ck3_chronicler-sj31: archived campaigns are included in the scan.
    The API still serves their read-only routes (Library card, vita
    roll, character pages) so an out-of-date archived schema 500s on
    any post-migration query. doctor migrate now keeps them at head
    too; un-archive (ck3_chronicler-w2s) likewise stays safe.
    """
    plan = MigrationPlan()
    needs, missing = _scan_registry(registry_path)
    plan.registry_needs_migration = needs
    plan.registry_missing_columns = missing

    target = _alembic_target_head()
    if registry_path.is_file():
        for camp in list_campaigns(include_archived=True, registry=registry_path):
            db_path = Path(camp.db_path)
            current = _read_alembic_head(db_path)
            if current != target:
                plan.needs_migration.append(
                    PendingCampaignMigration(
                        campaign_id=camp.id,
                        name=camp.name,
                        db_path=str(db_path),
                        current_head=current,
                        target_head=target,
                        archived=bool(camp.archived),
                    )
                )
    return plan
