"""Tests for `chronicler doctor migrate` subcommand."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chronicler.cli.main import app
from chronicler.db.registry import create_campaign


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_doctor_migrate_no_op_when_clean(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["doctor", "migrate"])
    assert result.exit_code == 0
    assert "All schemas current" in result.stdout


def test_doctor_migrate_runs_upgrade_on_pre_alembic_db(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    db = tmp_path / "campaigns" / "test.db"
    db.parent.mkdir(parents=True)
    db.touch()  # empty file — alembic upgrade head will run from baseline
    create_campaign("test", db_path=str(db), registry=tmp_path / "registry.db")
    result = runner.invoke(app, ["doctor", "migrate"])
    assert result.exit_code == 0
    # After migration, alembic_version table should be populated.
    with sqlite3.connect(db) as conn:
        cur = conn.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone() is not None


def test_doctor_runs_probes_with_no_subcommand(runner: CliRunner) -> None:
    """`chronicler doctor` (no subcommand) still runs the existing probes."""
    result = runner.invoke(app, ["doctor"])
    # Probe output mentions CK3 install (one of the probe names).
    assert "CK3 install" in result.stdout
