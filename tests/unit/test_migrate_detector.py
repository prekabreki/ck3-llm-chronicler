"""Tests for chronicler.migrate.detector — schema mismatch scan."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chronicler.db import Base, make_engine_for_path
from chronicler.db.registry import create_campaign
from chronicler.migrate.detector import scan


def _make_per_campaign_db(path: Path, alembic_head: str | None) -> None:
    """Create a per-campaign DB, then optionally stamp alembic_version."""
    engine = make_engine_for_path(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    if alembic_head is not None:
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
            )
            conn.execute(
                "INSERT INTO alembic_version (version_num) VALUES (?)",
                (alembic_head,),
            )
            conn.commit()


def test_scan_empty_when_no_campaigns(tmp_path: Path) -> None:
    registry = tmp_path / "registry.db"
    plan = scan(registry_path=registry)
    assert plan.needs_migration == []
    assert plan.registry_needs_migration is False


def test_scan_flags_pre_alembic_db(tmp_path: Path) -> None:
    """A per-campaign DB without an alembic_version table is flagged
    with current_head=None — needs alembic upgrade head from baseline."""
    registry = tmp_path / "registry.db"
    db = tmp_path / "campaigns" / "test.db"
    db.parent.mkdir(parents=True)
    _make_per_campaign_db(db, alembic_head=None)
    create_campaign("test", db_path=str(db), registry=registry)

    plan = scan(registry_path=registry)
    assert len(plan.needs_migration) == 1
    pending = plan.needs_migration[0]
    assert pending.current_head is None
    assert pending.target_head  # non-empty


def test_scan_flags_behind_head_db(tmp_path: Path) -> None:
    """A DB stamped at a fake earlier revision is flagged."""
    registry = tmp_path / "registry.db"
    db = tmp_path / "campaigns" / "test.db"
    db.parent.mkdir(parents=True)
    _make_per_campaign_db(db, alembic_head="not_the_real_head")
    create_campaign("test", db_path=str(db), registry=registry)

    plan = scan(registry_path=registry)
    assert len(plan.needs_migration) == 1
    assert plan.needs_migration[0].current_head == "not_the_real_head"


def test_scan_clean_when_at_head(tmp_path: Path) -> None:
    """A DB stamped at the actual head reports no pending migration."""
    from alembic.command import stamp
    from alembic.config import Config

    from chronicler.config import find_repo_root

    registry = tmp_path / "registry.db"
    db = tmp_path / "campaigns" / "test.db"
    db.parent.mkdir(parents=True)
    _make_per_campaign_db(db, alembic_head=None)
    cfg = Config(str(find_repo_root() / "alembic.ini"))
    cfg.attributes["db_url"] = f"sqlite:///{db.as_posix()}"
    stamp(cfg, "head")
    create_campaign("test", db_path=str(db), registry=registry)

    plan = scan(registry_path=registry)
    assert plan.needs_migration == []


def test_scan_registry_clean_when_columns_present(tmp_path: Path) -> None:
    """A registry connected via the normal _connect path lazily adds the
    expected columns; a fresh registry should report clean."""
    registry = tmp_path / "registry.db"
    create_campaign("just-to-init-the-registry", db_path=str(tmp_path / "x.db"), registry=registry)
    plan = scan(registry_path=registry)
    assert plan.registry_needs_migration is False
    assert plan.registry_missing_columns == []


def test_scan_includes_archived_campaigns(tmp_path: Path) -> None:
    """ck3_chronicler-sj31: archived per-campaign DBs are still served
    by the API (Library card / vita roll / character pages), so an
    out-of-date archived schema 500s on any post-migration query.
    The detector must include archived campaigns so doctor migrate
    keeps them at head.
    """
    from chronicler.db.registry import archive_campaign

    registry = tmp_path / "registry.db"
    db = tmp_path / "campaigns" / "sealed.db"
    db.parent.mkdir(parents=True)
    _make_per_campaign_db(db, alembic_head=None)
    sealed = create_campaign("sealed", db_path=str(db), registry=registry)
    archive_campaign(sealed.id, registry=registry)

    plan = scan(registry_path=registry)
    assert len(plan.needs_migration) == 1, (
        "archived campaign with pre-alembic DB must surface in the plan; "
        "otherwise doctor migrate skips it and the API 500s on the "
        "stale schema"
    )
    assert plan.needs_migration[0].campaign_id == sealed.id
    assert plan.needs_migration[0].current_head is None


def test_scan_marks_archived_flag(tmp_path: Path) -> None:
    """ck3_chronicler-4zdg: each pending entry carries an ``archived``
    flag so the migration UI can hide completed (archived) campaigns
    while the migrator still sweeps them. Active campaigns are
    ``archived=False``; sealed ones ``archived=True``."""
    from chronicler.db.registry import archive_campaign

    registry = tmp_path / "registry.db"
    active_db = tmp_path / "campaigns" / "active.db"
    sealed_db = tmp_path / "campaigns" / "sealed.db"
    active_db.parent.mkdir(parents=True)
    _make_per_campaign_db(active_db, alembic_head=None)
    _make_per_campaign_db(sealed_db, alembic_head=None)
    create_campaign("active", db_path=str(active_db), registry=registry)
    sealed = create_campaign("sealed", db_path=str(sealed_db), registry=registry)
    archive_campaign(sealed.id, registry=registry)

    plan = scan(registry_path=registry)
    by_name = {p.name: p for p in plan.needs_migration}
    assert by_name["active"].archived is False
    assert by_name["sealed"].archived is True


def test_scan_registry_flags_missing_columns(tmp_path: Path) -> None:
    """Hand-craft a registry with the campaigns table but missing the
    closing_chronicle column. scan() must surface that gap."""
    registry = tmp_path / "registry.db"
    with sqlite3.connect(registry) as conn:
        conn.execute(
            "CREATE TABLE campaigns (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "ck3_playthrough_id TEXT, ck3_version TEXT, created_at TEXT NOT NULL, "
            "last_event_at TEXT, archived INTEGER NOT NULL DEFAULT 0, "
            "db_path TEXT NOT NULL, founding_dynasty_name TEXT, "
            "tail_offset INTEGER NOT NULL DEFAULT 0)"
        )
        conn.commit()

    plan = scan(registry_path=registry)
    assert plan.registry_needs_migration is True
    assert "closing_chronicle" in plan.registry_missing_columns
