"""Tests for the yearly-diff smoke's event-type tally."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from chronicler.db import make_engine_for_path, make_session_factory
from chronicler.db.engine import session_scope
from chronicler.db.migrate_runner import upgrade_to_head
from chronicler.smoke.smoke_yearly import tally_event_types


def test_tally_event_types_survives_more_ids_than_sqlite_binds(tmp_path: Path) -> None:
    """A world-wide year of 1.20 diffs inserted 34,482 events and the single
    ``IN (...)`` tally died with 'too many SQL variables'. Chunked, it counts."""
    db = tmp_path / "c.db"
    upgrade_to_head(db)
    factory = make_session_factory(make_engine_for_path(db))
    with session_scope(factory) as session:
        session.execute(text("INSERT INTO characters (ck3_id) VALUES (1)"))
        for i, kind in enumerate(["death", "travel", "travel"], start=1):
            session.execute(
                text(
                    "INSERT INTO events (id, schema_version, event_type, event_date, "
                    "wall_clock_at, primary_character_id, payload_json, raw_line) "
                    "VALUES (:id, 1, :kind, '1067.1.1', 'now', 1, :p, '')"
                ),
                {"id": i, "kind": kind, "p": f'{{"n":{i}}}'},
            )

    ids = [1, 2, 3, *range(100_000, 140_000)]
    with session_scope(factory) as session:
        kinds = tally_event_types(session, ids)

    assert dict(kinds) == {"death": 1, "travel": 2}
