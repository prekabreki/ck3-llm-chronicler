"""Tests for chronicler.db.backfills resilience (ck3_chronicler-wvrm).

Every sqlite3 connection here is wrapped in ``closing`` with an explicit
``commit`` rather than using the connection as a bare context manager. That
shape commits but does NOT close (issue #54), and these fixtures' DB files
are later unlinked or moved — which Windows refuses while a handle is open.
The first version of the issue-#8 race test below shipped with the bare form
and failed on the Windows CI leg for exactly that reason.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from chronicler.db import backfills
from chronicler.db.backfills import (
    backfill_dynasty_name_from_house_name,
    campaign_db_is_usable,
)


def test_campaign_db_is_usable_missing_file_returns_false_and_creates_nothing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "gone.db"
    assert campaign_db_is_usable(missing, "characters") is False
    # The probe must NOT have materialised an empty file.
    assert not missing.exists()


def test_campaign_db_is_usable_tableless_file_returns_false(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()  # a real but tableless DB
    assert campaign_db_is_usable(empty, "characters") is False


def test_campaign_db_is_usable_present_table_returns_true(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    with closing(sqlite3.connect(str(real))) as conn:
        conn.execute("CREATE TABLE characters (ck3_id INTEGER PRIMARY KEY)")
        conn.commit()
    assert campaign_db_is_usable(real, "characters") is True


def test_campaign_db_is_usable_present_but_missing_required_table(tmp_path: Path) -> None:
    db = tmp_path / "partial.db"
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        conn.commit()
    assert campaign_db_is_usable(db, "characters") is False


def test_dynasty_backfill_skips_tableless_db_without_warning(tmp_path, monkeypatch, caplog) -> None:
    # A campaign whose db_path is a real-but-tableless file (the exact
    # post-merge Saar state once an empty .db has been materialised).
    from chronicler.db.registry import create_campaign

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data_dir"))
    registry_path = tmp_path / "data_dir" / "registry.db"
    (tmp_path / "data_dir" / "campaigns").mkdir(parents=True, exist_ok=True)
    campaign = create_campaign(
        "Empty",
        ck3_version="1.19.0",
        founding_dynasty_name="X",
        ck3_playthrough_id="pt",
        registry=registry_path,
    )
    # Replace the per-campaign DB with a tableless file.
    Path(campaign.db_path).unlink(missing_ok=True)
    import sqlite3 as _sq

    _sq.connect(campaign.db_path).close()

    with caplog.at_level(logging.WARNING):
        touched = backfill_dynasty_name_from_house_name(registry_path=registry_path)

    assert touched == 0
    assert "no such table" not in caplog.text
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# --- ck3_chronicler-27ov.50 (audit M-B4): exactly-once semantics ---


def _seed_campaign_with_character(tmp_path, monkeypatch, *, name="Once"):
    """Campaign whose DB has one character row eligible for both the
    te8s (NULL dynasty_name, house_name set) and x2qj passes."""
    from chronicler.db.registry import create_campaign

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data_dir"))
    registry_path = tmp_path / "data_dir" / "registry.db"
    (tmp_path / "data_dir" / "campaigns").mkdir(parents=True, exist_ok=True)
    campaign = create_campaign(
        name,
        ck3_version="1.19.0",
        founding_dynasty_name="X",
        ck3_playthrough_id=f"pt-{name}",
        registry=registry_path,
    )
    with closing(sqlite3.connect(campaign.db_path)) as conn:
        conn.execute(
            "CREATE TABLE characters ("
            "  ck3_id INTEGER PRIMARY KEY,"
            "  culture TEXT, first_name TEXT, nickname TEXT,"
            "  dynasty_name TEXT, house_name TEXT"
            ")"
        )
        conn.execute("INSERT INTO characters VALUES (1, 'norse', 'Erik', NULL, NULL, 'Munso')")
        conn.commit()
    return campaign, registry_path


def test_dynasty_backfill_runs_exactly_once_per_campaign(tmp_path, monkeypatch) -> None:
    campaign, registry_path = _seed_campaign_with_character(tmp_path, monkeypatch)

    assert backfill_dynasty_name_from_house_name(registry_path=registry_path) == 1

    # Re-null the column to prove the second boot does NOT redo the work
    # (the marker, not row-state idempotence, is what skips it).
    with closing(sqlite3.connect(campaign.db_path)) as conn:
        conn.execute("UPDATE characters SET dynasty_name = NULL")
        conn.commit()
    assert backfill_dynasty_name_from_house_name(registry_path=registry_path) == 0


def test_redecode_backfill_runs_exactly_once_per_campaign(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from chronicler.db.backfills import backfill_redecode_character_names

    _campaign, registry_path = _seed_campaign_with_character(tmp_path, monkeypatch, name="Decode")

    with patch("chronicler.db.backfills._redecode_names_one_campaign", return_value=0) as run_one:
        backfill_redecode_character_names(registry_path=registry_path)
        assert run_one.call_count == 1
        backfill_redecode_character_names(registry_path=registry_path)
        # Second boot: the marker short-circuits before the expensive
        # per-row decode pass even runs.
        assert run_one.call_count == 1


def test_backfill_marker_not_written_when_pass_fails(tmp_path, monkeypatch) -> None:
    """A failed pass must retry on the next boot - only success marks."""
    from unittest.mock import patch

    from chronicler.db.backfills import backfill_redecode_character_names

    _campaign, registry_path = _seed_campaign_with_character(tmp_path, monkeypatch, name="Retry")

    with patch(
        "chronicler.db.backfills._redecode_names_one_campaign",
        side_effect=RuntimeError("boom"),
    ) as run_one:
        backfill_redecode_character_names(registry_path=registry_path)
        assert run_one.call_count == 1
    with patch("chronicler.db.backfills._redecode_names_one_campaign", return_value=0) as run_one:
        backfill_redecode_character_names(registry_path=registry_path)
        assert run_one.call_count == 1  # retried because no marker was written


# --- Issue #8: the guard is TOCTOU; the engine must not create ---


def test_backfill_does_not_resurrect_a_db_deleted_after_the_guard(
    tmp_path, monkeypatch, caplog
) -> None:
    """Issue #8 root cause, forced rather than waited for.

    The startup backfills run on a background thread from the API lifespan
    and are only awaited at shutdown, so they run *concurrently* with
    request handling. ``campaign_db_is_usable`` is a check, so a campaign
    deleted between it returning True and the connect leaves the connect
    facing a missing file — and a plain ``sqlite:///path`` engine CREATES
    one. The deleted campaign's DB came back as a table-less stub, the
    "no such table" error was swallowed as best-effort, and
    ``test_delete_campaign_removes_registry_row_and_file``'s
    ``assert not db_path.exists()`` failed roughly one full-suite run in
    three while passing every time in isolation.

    The race is forced here by deleting the file inside the guard itself.
    Repeated green full-suite runs would not have proven anything: at one
    failure in three, five clean runs happen by luck ~13% of the time.
    """
    campaign, registry_path = _seed_campaign_with_character(tmp_path, monkeypatch, name="Racy")
    db_path = Path(campaign.db_path)
    real_guard = backfills.campaign_db_is_usable

    def guard_then_delete(path: Path, *required: str) -> bool:
        verdict = real_guard(path, *required)
        # Stand in for DELETE /api/campaigns/{name} landing in the window
        # between the guard and the connect.
        if Path(path) == db_path:
            db_path.unlink(missing_ok=True)
        return verdict

    monkeypatch.setattr(backfills, "campaign_db_is_usable", guard_then_delete)

    with caplog.at_level(logging.WARNING):
        touched = backfill_dynasty_name_from_house_name(registry_path=registry_path)

    assert touched == 0
    # The whole point: the deleted campaign's DB stays deleted.
    assert not db_path.exists(), (
        "the backfill re-created a deleted campaign DB as an empty stub — "
        "the engine must open with mode=rw so a missing file raises"
    )
    # Still best-effort: it logs and moves on rather than breaking boot.
    assert "Racy" in caplog.text


def test_make_engine_for_existing_path_refuses_to_create(tmp_path: Path) -> None:
    """The primitive behind the fix, asserted directly."""
    from sqlalchemy import text

    from chronicler.db.engine import make_engine_for_existing_path

    missing = tmp_path / "never-existed.db"
    engine = make_engine_for_existing_path(missing)
    with pytest.raises(OperationalError), engine.begin() as conn:
        conn.execute(text("SELECT 1"))
    assert not missing.exists()

    # An existing DB is still fully writable — this is not read-only.
    real = tmp_path / "real.db"
    with closing(sqlite3.connect(str(real))) as conn:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.commit()
    engine = make_engine_for_existing_path(real)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t VALUES (1)"))
        assert conn.execute(text("SELECT count(*) FROM t")).scalar() == 1
