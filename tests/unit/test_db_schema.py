"""Verify the Alembic migrations produce the current schema."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

EXPECTED_TABLES = {
    "alembic_version",
    "biographies",
    "characters",
    "event_participants",
    "events",
    "quarantine",
    "schema_meta",
}

# ck3_chronicler-b2y: FTS5 virtual tables created by migration
# ebc825c7d3ed. They install several internal shadow tables
# (biographies_fts_data, biographies_fts_idx, etc.) — those are
# implementation detail, not load-bearing on the model layer, so we
# just check the user-facing virtual table is present.
#
# Plan cozy-coalescing-shannon (Task 16) removed memories_fts along
# with the rest of the LLM-memory pipeline; only biographies_fts
# remains.
EXPECTED_FTS5_VIRTUAL_TABLES = {"biographies_fts"}


def test_alembic_upgrade_head_produces_v02_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    env = {**os.environ, "CHRONICLER_DB_URL": f"sqlite:///{db_path.as_posix()}"}
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    actual = {name for (name,) in rows}
    # Must contain every expected base table + every FTS5 virtual table.
    # The FTS5 module materialises a handful of internal shadow tables
    # (e.g. biographies_fts_data, biographies_fts_idx) which we don't
    # enumerate exhaustively — they're FTS5 implementation detail.
    missing = (EXPECTED_TABLES | EXPECTED_FTS5_VIRTUAL_TABLES) - actual
    assert not missing, f"missing tables after migration: {missing}"


def _alembic_upgrade(repo_root: Path, env: dict[str, str], rev: str) -> None:
    """Run ``alembic upgrade <rev>`` the same way the head-schema test does."""
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", rev],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade {rev} failed: {result.stderr}"


def test_v0_12_drop_memory_pipeline_preserves_data(tmp_path: Path) -> None:
    """ck3_chronicler-xgyx: positive round-trip for the destructive v0_12
    migration (b762b5ba1a38).

    The head-schema test only exercises a fresh-DB upgrade. This test
    upgrades a *populated* v0_11 schema (memories rows + FTS triggers +
    ``memories_rebuilt_at`` set) through the drop and asserts the LLM
    memory pipeline is gone while every other table's rows survive intact.

    The migration is forward-only (``downgrade`` raises), so this is an
    upgrade-to-pre → seed → upgrade-through-head assertion, not a true
    round-trip.
    """
    db_path = tmp_path / "test.db"
    env = {**os.environ, "CHRONICLER_DB_URL": f"sqlite:///{db_path.as_posix()}"}
    repo_root = Path(__file__).resolve().parents[2]

    # 1. Bring the DB up to v0_11 (the revision just before the drop): has
    #    memories, memories_fts (+ memories_ai/au/ad triggers), and the
    #    characters.memories_rebuilt_at sentinel column.
    _alembic_upgrade(repo_root, env, "ma96slice1abc")

    # 2. Seed a populated v0_11 DB: survivors (characters/events/biographies
    #    with memories_rebuilt_at set) + the doomed memories rows.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO characters (ck3_id, first_name, memories_rebuilt_at) "
            "VALUES (1, 'Bjorn', '2026-05-27T00:00:00Z'), (2, 'Asta', NULL)"
        )
        conn.execute(
            "INSERT INTO events (id, schema_version, event_type, event_date, "
            "wall_clock_at, primary_character_id, payload_json, raw_line) "
            "VALUES (10, 1, 'death', '1075.8.27', '2026-05-27T00:00:00Z', 1, '{}', '')"
        )
        conn.executemany(
            "INSERT INTO biographies (character_id, version, body, "
            "prompt_template_version, provider, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 1, "The saga of Bjorn.", "bio-v1", "test", "2026-05-27T00:00:00Z"),
                (2, 1, "The saga of Asta.", "bio-v1", "test", "2026-05-27T00:00:00Z"),
            ],
        )
        # memories rows — fire the FTS sync triggers (memories_ai) so the
        # drop has to tear down a genuinely populated external-content FTS.
        conn.executemany(
            "INSERT INTO memories (character_id, body, prompt_template_version, "
            "provider, generated_at) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "Bjorn remembers the raid.", "mem-v1", "test", "2026-05-27T00:00:00Z"),
                (1, "Bjorn recalls the feast.", "mem-v1", "test", "2026-05-27T00:00:00Z"),
            ],
        )
        conn.commit()
        chars_before = conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # 3. Upgrade through the drop to head.
    _alembic_upgrade(repo_root, env, "head")

    # 4. Assertions: memory pipeline gone, everything else intact.
    with sqlite3.connect(db_path) as conn:
        tables = {
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        # memories table + all its FTS5 shadow tables are gone.
        assert "memories" not in tables, "memories table must be dropped"
        assert not any(t.startswith("memories_fts") for t in tables), (
            f"memories_fts (+ shadow tables) must be dropped; found "
            f"{[t for t in tables if t.startswith('memories_fts')]}"
        )
        # memories sync triggers gone.
        triggers = {
            name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert not any(t.startswith("memories_") for t in triggers), (
            f"memories_* triggers must be dropped; found "
            f"{[t for t in triggers if t.startswith('memories_')]}"
        )
        # memories_rebuilt_at column gone from characters.
        char_cols = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
        assert "memories_rebuilt_at" not in char_cols, (
            "characters.memories_rebuilt_at column must be dropped"
        )
        # Survivors intact — biographies content preserved, no rows lost.
        bios = {
            (cid, body)
            for cid, body in conn.execute(
                "SELECT character_id, body FROM biographies ORDER BY character_id"
            )
        }
        assert bios == {
            (1, "The saga of Bjorn."),
            (2, "The saga of Asta."),
        }, f"biographies must survive intact; got {bios}"
        assert conn.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == chars_before
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before


def test_models_module_imports() -> None:
    from chronicler.db import (
        Base,
        Biography,
        Character,
        Event,
        EventParticipant,
        Quarantine,
        SchemaMeta,
    )
    from chronicler.db.models import CharacterCoaHistory

    expected_tables = {
        Biography.__tablename__,
        Character.__tablename__,
        CharacterCoaHistory.__tablename__,
        Event.__tablename__,
        EventParticipant.__tablename__,
        Quarantine.__tablename__,
        SchemaMeta.__tablename__,
    }
    assert set(Base.metadata.tables.keys()) == expected_tables
