"""Tests for the registry DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chronicler.db.registry import (
    add_suppressed_kind,
    add_tracked_character,
    archive_campaign,
    create_campaign,
    get_campaign_by_id,
    get_campaign_by_name,
    get_tail_offset,
    is_character_tracked,
    is_kind_suppressed,
    list_campaigns,
    list_suppressed_kinds,
    list_tracked_characters,
    remove_suppressed_kind,
    remove_tracked_character,
    rename_campaign,
    resolve_campaign_for_save,
    set_tail_offset,
    touch_last_event_at,
    unarchive_campaign,
    update_campaign_overview,
    update_tracked_character,
)


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    return tmp_path / "registry.db"


def test_create_and_fetch_campaign(registry: Path) -> None:
    c = create_campaign("Aquitaine", ck3_version="1.17.0", registry=registry)
    assert c.name == "Aquitaine"
    assert c.id
    assert c.archived is False
    assert c.tail_offset == 0
    fetched = get_campaign_by_id(c.id, registry=registry)
    assert fetched is not None
    assert fetched.id == c.id


def test_fetch_unknown_returns_none(registry: Path) -> None:
    assert get_campaign_by_id("does-not-exist", registry=registry) is None
    assert get_campaign_by_name("does-not-exist", registry=registry) is None


# --- ck3_chronicler-27ov.49 (J4): _row_to_campaign derived from the dataclass.
# Every other test migrates the DB before fetching, so the "column absent ->
# default" branch is otherwise unguarded. These pin the mapper contract the
# dataclass-driven version must preserve. ---

_BASE_DDL_COLUMNS = (
    "id TEXT, name TEXT, ck3_playthrough_id TEXT, ck3_version TEXT, "
    "created_at TEXT, last_event_at TEXT, archived INTEGER, db_path TEXT, "
    "founding_dynasty_name TEXT, tail_offset INTEGER"
)


def _row(conn: sqlite3.Connection, ddl_columns: str, values: tuple[object, ...]):
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE campaigns ({ddl_columns})")
    placeholders = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO campaigns VALUES ({placeholders})", values)
    return conn.execute("SELECT * FROM campaigns").fetchone()


def test_row_to_campaign_defaults_absent_columns_to_none() -> None:
    """A legacy row missing every lazily-migrated column maps cleanly: the
    absent fields fall back to their dataclass defaults (None) and archived
    coerces to bool."""
    from chronicler.db.registry.campaigns import _row_to_campaign

    with sqlite3.connect(":memory:") as conn:
        row = _row(
            conn,
            _BASE_DDL_COLUMNS,
            ("id1", "Wessex", None, "1.17", "2026-01-01", None, 0, "/p.db", None, 7),
        )
        c = _row_to_campaign(row)

    assert c.id == "id1"
    assert c.name == "Wessex"
    assert c.archived is False
    assert c.tail_offset == 7
    # Lazily-migrated columns absent from this row -> dataclass defaults.
    assert c.closing_chronicle is None
    assert c.closing_chronicle_cost_usd is None
    assert c.current_player_gold is None
    assert c.last_event_in_game_date is None


def test_row_to_campaign_reads_present_columns() -> None:
    """When the columns exist, their values are read through (and archived
    coerces a stored 1 to True)."""
    from chronicler.db.registry.campaigns import _row_to_campaign

    ddl = _BASE_DDL_COLUMNS + ", closing_chronicle TEXT, current_player_gold REAL"
    with sqlite3.connect(":memory:") as conn:
        row = _row(
            conn,
            ddl,
            (
                "id2",
                "Mercia",
                "pt-1",
                "1.17",
                "2026-01-01",
                "2026-02-02",
                1,
                "/q.db",
                "Godwin",
                42,
                "The End",
                1234.5,
            ),
        )
        c = _row_to_campaign(row)

    assert c.archived is True
    assert c.founding_dynasty_name == "Godwin"
    assert c.closing_chronicle == "The End"
    assert c.current_player_gold == 1234.5


def test_list_campaigns_excludes_archived_by_default(registry: Path) -> None:
    a = create_campaign("A", registry=registry)
    b = create_campaign("B", registry=registry)
    archive_campaign(a.id, registry=registry)

    active = list_campaigns(registry=registry)
    assert {c.id for c in active} == {b.id}

    everything = list_campaigns(include_archived=True, registry=registry)
    assert {c.id for c in everything} == {a.id, b.id}


def test_get_campaign_by_name_filters_archived(registry: Path) -> None:
    c = create_campaign("Castile", registry=registry)
    archive_campaign(c.id, registry=registry)
    assert get_campaign_by_name("Castile", registry=registry) is None


def test_tail_offset_round_trip(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    assert get_tail_offset(c.id, registry=registry) == 0
    set_tail_offset(c.id, 4096, registry=registry)
    assert get_tail_offset(c.id, registry=registry) == 4096


def test_tail_offset_unknown_campaign_is_zero(registry: Path) -> None:
    assert get_tail_offset("nope", registry=registry) == 0


def test_touch_last_event_at_persists(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    assert c.last_event_at is None
    touch_last_event_at(c.id, registry=registry)
    refetched = get_campaign_by_id(c.id, registry=registry)
    assert refetched is not None
    assert refetched.last_event_at is not None


def test_touch_last_event_at_without_in_game_date_leaves_in_game_column_null(
    registry: Path,
) -> None:
    """ck3_chronicler-9xa6: a touch with no in_game_date still bumps the
    wall-clock column (legacy callers, e.g. the bare debug-log tailer)
    but must NOT clobber a previously-recorded in-game date and must NOT
    write a wall-clock value into the in-game column."""
    c = create_campaign("Wessex", registry=registry)
    assert c.last_event_in_game_date is None
    touch_last_event_at(c.id, registry=registry)
    refetched = get_campaign_by_id(c.id, registry=registry)
    assert refetched is not None
    assert refetched.last_event_at is not None  # wall-clock still bumped
    assert refetched.last_event_in_game_date is None  # untouched


def test_touch_last_event_at_with_in_game_date_persists_to_in_game_column(
    registry: Path,
) -> None:
    """ck3_chronicler-9xa6: callers that have an in-game date in hand
    (save-tail's _apply_campaign_overview_from_snap) write to both columns
    in one call."""
    c = create_campaign("Mercia", registry=registry)
    touch_last_event_at(c.id, in_game_date="1071.4.12", registry=registry)
    refetched = get_campaign_by_id(c.id, registry=registry)
    assert refetched is not None
    assert refetched.last_event_at is not None
    assert refetched.last_event_in_game_date == "1071.4.12"


def test_touch_last_event_at_in_game_date_does_not_regress_to_null(
    registry: Path,
) -> None:
    """A subsequent touch without an in_game_date must not blank a
    previously-set value. The wall-clock column is unconditionally
    refreshed; the in-game column is set-only-when-provided."""
    c = create_campaign("Northumbria", registry=registry)
    touch_last_event_at(c.id, in_game_date="1100.1.1", registry=registry)
    touch_last_event_at(c.id, registry=registry)  # no in_game_date
    refetched = get_campaign_by_id(c.id, registry=registry)
    assert refetched is not None
    assert refetched.last_event_in_game_date == "1100.1.1"


def test_create_campaign_assigns_unique_ids(registry: Path) -> None:
    a = create_campaign("A", registry=registry)
    b = create_campaign("B", registry=registry)
    assert a.id != b.id


# --- ck3_chronicler-4cl: tracked_characters note / role round-trip ---
# (ck3_chronicler-nx2x retired the dead preferred_provider/preferred_model
#  fields; these tests now exercise the same add/update/preserve semantics
#  via the surviving note + role columns.)


def test_tracked_character_defaults(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(c.id, 12345, note="player", registry=registry)
    rows = list_tracked_characters(c.id, registry=registry)
    assert len(rows) == 1
    assert rows[0].character_id == 12345
    assert rows[0].note == "player"
    assert rows[0].role is None


def test_tracked_character_with_role(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(
        c.id,
        12345,
        note="player",
        role="protagonist",
        registry=registry,
    )
    [row] = list_tracked_characters(c.id, registry=registry)
    assert row.role == "protagonist"


def test_add_tracked_character_partial_update_preserves_other_fields(
    registry: Path,
) -> None:
    """Re-adding with only note should leave role intact (COALESCE-on-None)."""
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(
        c.id,
        12345,
        role="rival",
        registry=registry,
    )
    add_tracked_character(c.id, 12345, note="updated note", registry=registry)
    [row] = list_tracked_characters(c.id, registry=registry)
    assert row.note == "updated note"
    assert row.role == "rival"


def test_update_tracked_character_explicit_clear(registry: Path) -> None:
    """update_tracked_character explicitly setting None must clear the column
    (unlike add_tracked_character which preserves on None)."""
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(c.id, 1, role="rival", registry=registry)
    assert update_tracked_character(c.id, 1, role=None, registry=registry)
    [row] = list_tracked_characters(c.id, registry=registry)
    assert row.role is None


def test_update_tracked_character_unknown_field_raises(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(c.id, 1, registry=registry)
    with pytest.raises(ValueError, match="unknown tracked-character fields"):
        update_tracked_character(c.id, 1, bogus="x", registry=registry)


def test_update_tracked_character_no_fields_returns_false(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(c.id, 1, registry=registry)
    assert update_tracked_character(c.id, 1, registry=registry) is False


def test_update_tracked_character_unknown_target_returns_false(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    assert update_tracked_character(c.id, 99999, note="x", registry=registry) is False


def test_remove_tracked_character_returns_bool(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(c.id, 1, registry=registry)
    assert is_character_tracked(c.id, 1, registry=registry)
    assert remove_tracked_character(c.id, 1, registry=registry) is True
    assert remove_tracked_character(c.id, 1, registry=registry) is False
    assert not is_character_tracked(c.id, 1, registry=registry)


def test_lazy_migration_adds_columns_to_legacy_db(tmp_path: Path) -> None:
    """A registry DB created before lvv (with the old 4-column schema)
    must auto-gain the new columns on next connect — verified by reading
    PRAGMA table_info before + after."""
    legacy = tmp_path / "legacy.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(legacy)
    try:
        # Pre-lvv schema
        raw.execute(
            """
            CREATE TABLE tracked_characters (
                campaign_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                note TEXT,
                added_at TEXT NOT NULL,
                PRIMARY KEY (campaign_id, character_id)
            )
            """
        )
        raw.execute(
            "INSERT INTO tracked_characters VALUES (?, ?, ?, ?)",
            ("camp-1", 7, "important char", "2026-01-01T00:00:00+00:00"),
        )
        raw.commit()
        cols_before = {
            r[1] for r in raw.execute("PRAGMA table_info(tracked_characters)").fetchall()
        }
        assert cols_before == {"campaign_id", "character_id", "note", "added_at"}
    finally:
        raw.close()

    # Touching the registry through any operation triggers _connect →
    # _ensure_tracked_columns. list_tracked_characters is the cheapest.
    rows = list_tracked_characters("camp-1", registry=legacy)

    raw = sqlite3.connect(legacy)
    try:
        cols_after = {r[1] for r in raw.execute("PRAGMA table_info(tracked_characters)").fetchall()}
    finally:
        raw.close()
    # The lazy migration backfills the columns added after the original
    # 4-column schema (ck3_chronicler-nx2x retired preferred_provider/model;
    # role + paused_at + bumped_at are the surviving required columns).
    assert "role" in cols_after
    assert "paused_at" in cols_after
    assert "bumped_at" in cols_after
    # Pre-existing row survives the migration with NULL for the new fields
    assert len(rows) == 1
    assert rows[0].character_id == 7
    assert rows[0].role is None


# --- ck3_chronicler-fkw: suppressed_event_kinds ---


def test_suppress_kind_round_trip(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    assert is_kind_suppressed(c.id, "death", registry=registry) is False
    add_suppressed_kind(c.id, "death", registry=registry)
    assert is_kind_suppressed(c.id, "death", registry=registry) is True
    rows = list_suppressed_kinds(c.id, registry=registry)
    assert len(rows) == 1
    assert rows[0].event_kind == "death"


def test_suppress_kind_is_per_campaign(registry: Path) -> None:
    a = create_campaign("A", registry=registry)
    b = create_campaign("B", registry=registry)
    add_suppressed_kind(a.id, "birth", registry=registry)
    assert is_kind_suppressed(a.id, "birth", registry=registry) is True
    assert is_kind_suppressed(b.id, "birth", registry=registry) is False


def test_suppress_kind_idempotent(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_suppressed_kind(c.id, "death", registry=registry)
    add_suppressed_kind(c.id, "death", registry=registry)
    assert len(list_suppressed_kinds(c.id, registry=registry)) == 1


def test_remove_suppressed_kind_returns_bool(registry: Path) -> None:
    c = create_campaign("Aquitaine", registry=registry)
    add_suppressed_kind(c.id, "death", registry=registry)
    assert remove_suppressed_kind(c.id, "death", registry=registry) is True
    assert remove_suppressed_kind(c.id, "death", registry=registry) is False
    assert is_kind_suppressed(c.id, "death", registry=registry) is False


def test_is_kind_suppressed_treats_none_as_unsuppressed(registry: Path) -> None:
    """A None event_kind (parser couldn't recover the type) is never
    suppressed — those failures always reach quarantine for inspection."""
    c = create_campaign("Aquitaine", registry=registry)
    add_suppressed_kind(c.id, "death", registry=registry)
    assert is_kind_suppressed(c.id, None, registry=registry) is False


def test_lazy_migration_is_idempotent(tmp_path: Path) -> None:
    """Running through _connect twice must not raise (re-ALTER would fail)."""
    registry = tmp_path / "registry.db"
    c = create_campaign("Aquitaine", registry=registry)
    add_tracked_character(c.id, 1, role="rival", registry=registry)
    # Second connect — would crash if _ensure_columns retried ALTER
    [row] = list_tracked_characters(c.id, registry=registry)
    assert row.role == "rival"


def test_ensure_columns_swallows_duplicate_column_race(tmp_path: Path) -> None:
    """F-49: when a cross-process race lands the same ALTER from another
    writer between our PRAGMA check and our ALTER, SQLite raises
    ``OperationalError: duplicate column name``. _ensure_columns must
    treat that as success (the schema is now in the desired state).

    Drive the dupe branch deterministically with a thin connection
    proxy that forwards everything to a real sqlite3 connection but
    lies about ``PRAGMA table_info`` (returns zero rows). That makes
    _ensure_columns think the column is missing, attempt the ALTER,
    and trip the duplicate-column-name OperationalError that F-49
    catches.
    """
    from chronicler.db.registry import _core as core

    registry = tmp_path / "registry.db"
    create_campaign("seed", registry=registry)  # writes live schema with column

    real_conn = sqlite3.connect(registry)
    try:
        # Sanity: the column is already there.
        cur = real_conn.execute("PRAGMA table_info(campaigns)")
        assert "closing_chronicle" in {row[1] for row in cur.fetchall()}

        class _EmptyPragmaConn:
            """Forwards .execute to the real connection except PRAGMA
            table_info, which returns zero rows."""

            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def execute(self, sql: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
                if sql.lstrip().upper().startswith("PRAGMA TABLE_INFO"):
                    return self._real.execute("SELECT 1 WHERE 0 = 1")
                return self._real.execute(sql, *args, **kwargs)

        fake = _EmptyPragmaConn(real_conn)
        # Should not raise — F-49 catches the dupe-column ALTER.
        core._ensure_columns(
            fake,  # type: ignore[arg-type]
            "campaigns",
            (("closing_chronicle", "TEXT"),),
        )
    finally:
        real_conn.close()


def test_lazy_migration_adds_overview_columns_to_existing_registry(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-cqo: a registry created with the pre-cqo schema
    must lazy-migrate the six overview columns on next connect."""
    import sqlite3

    registry = tmp_path / "registry.db"
    # Simulate a pre-cqo registry: minimal schema with only the columns
    # that existed before cqo (mirrors the older _CREATE_CAMPAIGNS).
    conn = sqlite3.connect(registry)
    conn.execute(
        """
        CREATE TABLE campaigns (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            ck3_playthrough_id TEXT,
            ck3_version TEXT,
            created_at TEXT NOT NULL,
            last_event_at TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            db_path TEXT NOT NULL,
            founding_dynasty_name TEXT,
            tail_offset INTEGER NOT NULL DEFAULT 0,
            closing_chronicle TEXT,
            closing_chronicle_generated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO campaigns (id, name, created_at, db_path) VALUES (?, ?, ?, ?)",
        ("old-id", "Pre-cqo", "2026-05-01T00:00:00+00:00", str(tmp_path / "x.db")),
    )
    conn.commit()
    conn.close()

    # Reset the per-process init cache so _ensure_campaigns_columns runs.
    from chronicler.db.registry import _core as registry_mod

    with registry_mod._INIT_LOCK:
        registry_mod._INITIALIZED_PATHS.discard(str(registry.resolve()))

    # Now connecting via the helper should add the new columns.
    camp = get_campaign_by_id("old-id", registry=registry)
    assert camp is not None
    assert camp.bookmark_date is None
    assert camp.current_in_game_date is None
    assert camp.current_player_character_id is None
    assert camp.current_player_name is None
    assert camp.current_player_nickname is None
    assert camp.current_house_name is None


def test_create_campaign_accepts_ck3_playthrough_id(tmp_path: Path) -> None:
    """ck3_chronicler-cqo: auto-detect needs to write playthrough_id at
    create time so the next lookup-by-playthrough_id finds the campaign.

    The existing create_campaign signature accepts founding_dynasty_name
    but not ck3_playthrough_id — that gap is closed here."""
    registry = tmp_path / "registry.db"
    camp = create_campaign(
        "Erik 1066-9-15",
        db_path=str(tmp_path / "erik.db"),
        ck3_playthrough_id="uuid-cqo-test",
        registry=registry,
    )
    assert camp.ck3_playthrough_id == "uuid-cqo-test"
    # Round-trip through get_campaign_by_id confirms the column is written,
    # not just on the dataclass.
    fresh = get_campaign_by_id(camp.id, registry=registry)
    assert fresh is not None
    assert fresh.ck3_playthrough_id == "uuid-cqo-test"


def test_update_campaign_overview_writes_all_fields(tmp_path: Path) -> None:
    """ck3_chronicler-cqo: update_campaign_overview is called from
    save-tail's _advance_baseline after every tick and writes the six
    overview columns plus ck3_playthrough_id so the Library card is
    one query."""
    registry = tmp_path / "registry.db"
    camp = create_campaign("Erik 1066-9-15", db_path=str(tmp_path / "erik.db"), registry=registry)
    update_campaign_overview(
        camp.id,
        ck3_playthrough_id="uuid-cqo-test",
        bookmark_date="1066.9.15",
        current_in_game_date="1075.5.9",
        current_player_character_id=32943,
        current_player_name="Erik",
        current_player_nickname="the Heathen",
        current_house_name="house_munso",
        registry=registry,
    )
    fresh = get_campaign_by_id(camp.id, registry=registry)
    assert fresh is not None
    assert fresh.ck3_playthrough_id == "uuid-cqo-test"
    assert fresh.bookmark_date == "1066.9.15"
    assert fresh.current_in_game_date == "1075.5.9"
    assert fresh.current_player_character_id == 32943
    assert fresh.current_player_name == "Erik"
    assert fresh.current_player_nickname == "the Heathen"
    assert fresh.current_house_name == "house_munso"


def test_update_campaign_overview_omitted_fields_preserve_existing_values(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-cqo: only the kwargs the caller actually passes
    are written. Omitted kwargs leave the column untouched — that's
    how heir-succession ticks can refresh just first_name/nickname/
    house without re-asserting bookmark_date."""
    registry = tmp_path / "registry.db"
    camp = create_campaign("Erik 1066-9-15", db_path=str(tmp_path / "erik.db"), registry=registry)
    # Tick 1: full population.
    update_campaign_overview(
        camp.id,
        bookmark_date="1066.9.15",
        current_in_game_date="1066.9.15",
        current_player_character_id=32943,
        current_player_name="Erik",
        current_player_nickname="the Heathen",
        current_house_name="house_munso",
        registry=registry,
    )
    # Tick 2: only the moving fields. bookmark_date and house_name
    # not passed — must remain set from tick 1.
    update_campaign_overview(
        camp.id,
        current_in_game_date="1067.3.1",
        current_player_character_id=32943,
        current_player_name="Erik",
        current_player_nickname="the Wise",
        registry=registry,
    )
    fresh = get_campaign_by_id(camp.id, registry=registry)
    assert fresh is not None
    assert fresh.bookmark_date == "1066.9.15"  # preserved (omitted on tick 2)
    assert fresh.current_in_game_date == "1067.3.1"  # updated
    assert fresh.current_player_nickname == "the Wise"  # updated
    assert fresh.current_house_name == "house_munso"  # preserved (omitted)


def test_update_campaign_overview_explicit_none_writes_sql_null(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-cqo: passing ``None`` (vs omitting the kwarg)
    explicitly writes SQL NULL. This is what save-tail uses on heir
    succession to clear a stale nickname or house when the new player
    doesn't have one."""
    registry = tmp_path / "registry.db"
    camp = create_campaign("Erik 1066-9-15", db_path=str(tmp_path / "erik.db"), registry=registry)
    # Tick 1: full population including nickname.
    update_campaign_overview(
        camp.id,
        current_player_character_id=32943,
        current_player_name="Erik",
        current_player_nickname="the Heathen",
        current_house_name="house_munso",
        registry=registry,
    )
    # Tick 2: heir succession; new player has no nickname.
    # Explicitly pass None → must clear, not preserve.
    update_campaign_overview(
        camp.id,
        current_player_character_id=5678,
        current_player_name="Asbjorn",
        current_player_nickname=None,  # explicit clear
        current_house_name="house_munso",
        registry=registry,
    )
    fresh = get_campaign_by_id(camp.id, registry=registry)
    assert fresh is not None
    assert fresh.current_player_name == "Asbjorn"
    assert fresh.current_player_nickname is None  # cleared, not stale
    assert fresh.current_house_name == "house_munso"


# --- ck3_chronicler-cqo: resolve_campaign_for_save ---


def _make_snap_for_resolve(
    *,
    playthrough_id: str,
    player_first_name: str = "Erik",
    bookmark_date: str = "1066.9.15",
    founding_dynasty_name: str | None = None,
):
    """Stub a SaveSnapshot-like object with the four fields
    resolve_campaign_for_save reads. We don't need the full SaveSnapshot
    machinery for these unit tests — duck typing makes that fine."""
    from dataclasses import dataclass

    @dataclass
    class _SnapStub:
        playthrough_id: str
        player_character_id: int = 32943
        bookmark_date: str | None = None
        founding_player_first_name: str | None = None
        founding_dynasty_name: str | None = None

    return _SnapStub(
        playthrough_id=playthrough_id,
        bookmark_date=bookmark_date,
        founding_player_first_name=player_first_name,
        founding_dynasty_name=founding_dynasty_name,
    )


def test_resolve_campaign_for_save_matches_existing_by_playthrough(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-cqo: an existing campaign with a matching
    ck3_playthrough_id is returned as-is, not duplicated."""
    registry = tmp_path / "registry.db"
    existing = create_campaign(
        "Erik 1066-9-15",
        db_path=str(tmp_path / "erik.db"),
        ck3_playthrough_id="uuid-existing",
        registry=registry,
    )
    snap = _make_snap_for_resolve(playthrough_id="uuid-existing", player_first_name="DoesNotMatter")
    matched = resolve_campaign_for_save(snap, ck3_version="1.19.0.4", registry=registry)
    assert matched.id == existing.id
    # No new campaign was created.
    all_campaigns = list_campaigns(include_archived=True, registry=registry)
    assert len(all_campaigns) == 1


def test_resolve_campaign_for_save_creates_with_auto_name_on_no_match(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-cqo / 2026-05-09: with no matching campaign,
    create one with auto-name. Format is now '<dynasty> <bookmark>'
    when the snap's founding_dynasty_name is set (the chronicled
    spine stays stable across succession), falling back to the
    player's first name when no dynasty is known. The decoded form
    'Munso' is what production passes — _build_resolve_snap (CLI)
    runs decode_house_name on the raw key before reaching here."""
    registry = tmp_path / "registry.db"
    snap = _make_snap_for_resolve(
        playthrough_id="uuid-fresh",
        player_first_name="Erik",
        bookmark_date="1066.9.15",
        founding_dynasty_name="Munso",
    )
    created = resolve_campaign_for_save(snap, ck3_version="1.19.0.4", registry=registry)
    assert created.name == "Munso 1066-9-15"
    assert created.ck3_playthrough_id == "uuid-fresh"
    assert created.ck3_version == "1.19.0.4"
    assert created.founding_dynasty_name == "Munso"


def test_resolve_campaign_for_save_falls_back_to_player_when_no_dynasty(
    tmp_path: Path,
) -> None:
    """ck3_chronicler 2026-05-09: when the dynasty isn't known yet
    (adventurer mode, very-early playthrough where the parser hasn't
    resolved the chain), fall back to the player's first name —
    mirrors the dynasty-preferred behaviour for the common case."""
    registry = tmp_path / "registry.db"
    snap = _make_snap_for_resolve(
        playthrough_id="uuid-no-dyn",
        player_first_name="Erik",
        bookmark_date="1066.9.15",
        founding_dynasty_name=None,
    )
    created = resolve_campaign_for_save(snap, ck3_version="1.19.0.4", registry=registry)
    assert created.name == "Erik 1066-9-15"


def test_resolve_campaign_for_save_picks_most_recent_on_dup_playthrough(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-cqo: defensive fallback for the 'two campaigns
    share a playthrough_id' corner — shouldn't happen but if it does,
    pick the one with the highest last_event_at."""
    registry = tmp_path / "registry.db"
    older = create_campaign(
        "Older",
        db_path=str(tmp_path / "older.db"),
        ck3_playthrough_id="uuid-dup",
        registry=registry,
    )
    newer = create_campaign(
        "Newer",
        db_path=str(tmp_path / "newer.db"),
        ck3_playthrough_id="uuid-dup",
        registry=registry,
    )
    # Bump only `newer`'s last_event_at so the tiebreak is deterministic.
    touch_last_event_at(newer.id, registry=registry)

    snap = _make_snap_for_resolve(playthrough_id="uuid-dup")
    matched = resolve_campaign_for_save(snap, ck3_version="1.19.0.4", registry=registry)
    assert matched.id == newer.id
    assert matched.id != older.id


# --- ck3_chronicler-bly: rename ---


def test_rename_campaign_round_trip(registry: Path) -> None:
    """ck3_chronicler-bly: a renamed campaign is findable under the new
    name and not under the old one."""
    c = create_campaign("Erik 1066-9-15", registry=registry)
    rename_campaign(c.id, "Norse Smoke", registry=registry)
    fresh = get_campaign_by_id(c.id, registry=registry)
    assert fresh is not None
    assert fresh.name == "Norse Smoke"
    assert get_campaign_by_name("Norse Smoke", registry=registry) is not None
    assert get_campaign_by_name("Erik 1066-9-15", registry=registry) is None


def test_rename_does_not_enforce_uniqueness(registry: Path) -> None:
    """ck3_chronicler-bly: registry intentionally allows duplicate names —
    cqo's auto-detect keys on ``ck3_playthrough_id`` so name collisions
    are cosmetic. The API layer surfaces a warning; the registry does
    not reject."""
    a = create_campaign("Wessex Run", registry=registry)
    b = create_campaign("Erik 1066-9-15", registry=registry)
    rename_campaign(b.id, "Wessex Run", registry=registry)
    rows = list_campaigns(registry=registry)
    assert {x.id for x in rows} == {a.id, b.id}
    assert sorted(x.name for x in rows) == ["Wessex Run", "Wessex Run"]


# --- ck3_chronicler-w2s: un-archive ---


def test_unarchive_campaign_round_trip(registry: Path) -> None:
    """ck3_chronicler-w2s: archive then unarchive returns the campaign
    to the active list."""
    c = create_campaign("Norse Smoke", registry=registry)
    archive_campaign(c.id, registry=registry)
    assert {x.id for x in list_campaigns(registry=registry)} == set()
    unarchive_campaign(c.id, registry=registry)
    assert {x.id for x in list_campaigns(registry=registry)} == {c.id}
    fresh = get_campaign_by_id(c.id, registry=registry)
    assert fresh is not None
    assert fresh.archived is False


def test_unarchive_idempotent_on_active(registry: Path) -> None:
    """ck3_chronicler-w2s: unarchiving an already-active campaign is a
    no-op (UPDATE on archived=0 → archived=0 changes nothing)."""
    c = create_campaign("Active", registry=registry)
    unarchive_campaign(c.id, registry=registry)  # already active
    fresh = get_campaign_by_id(c.id, registry=registry)
    assert fresh is not None
    assert fresh.archived is False


def test_get_campaign_by_name_include_archived(registry: Path) -> None:
    """ck3_chronicler-w2s: include_archived=True must surface a sealed
    row that the default lookup hides."""
    c = create_campaign("Sealed", registry=registry)
    archive_campaign(c.id, registry=registry)
    assert get_campaign_by_name("Sealed", registry=registry) is None
    found = get_campaign_by_name("Sealed", include_archived=True, registry=registry)
    assert found is not None
    assert found.id == c.id
    assert found.archived is True


# --- ck3_chronicler-v4z: _connect TOCTOU regression guard ---


def test_connect_concurrent_first_open_does_not_race_on_schema(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-v4z: multiple threads opening the same fresh
    registry path concurrently must not crash on ``duplicate column
    name``. Pre-fix _connect released _INIT_LOCK after the membership
    check, then ran ALTER TABLE outside the lock — two threads could
    both observe already_init=False and both run _ensure_*_columns,
    with the second hitting ``OperationalError: duplicate column
    name`` once the first completed.

    The fix holds the lock across the DDL too, so only one thread runs
    schema setup. The test stresses that path with a barrier-coordinated
    fan-out and passes deterministically under the lock-held
    implementation."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from chronicler.db.registry import _core as registry_mod

    registry = tmp_path / "race.db"
    # Make sure no prior test has memoised this exact path.
    with registry_mod._INIT_LOCK:
        registry_mod._INITIALIZED_PATHS.discard(str(registry.resolve()))

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def worker() -> None:
        # Coordinate so all threads hit _connect at roughly the same
        # instant — maximises the chance of catching a regression
        # back to the unlocked-DDL shape.
        barrier.wait()
        try:
            create_campaign("racer", db_path=str(tmp_path / "x.db"), registry=registry)
        except BaseException as exc:  # noqa: BLE001 — capture for assertion
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(lambda _: worker(), range(n_threads)))

    assert errors == [], f"concurrent _connect raised: {errors!r}"
    # All N threads inserted; schema is exactly once-created.
    rows = list_campaigns(registry=registry)
    assert len(rows) == n_threads


# --- ck3_chronicler-27ov.79 (audit L18): delete_campaign owns child cleanup ---


def test_delete_campaign_removes_dependent_rows(tmp_path: Path) -> None:
    """audit L18: with no FK cascade (enforcement is off; the clauses were
    dropped), delete_campaign is the sole mechanism that clears a campaign's
    tracked_characters / suppressed_event_kinds. A bare DELETE of the parent
    row would orphan them — so the explicit deletes must do the work."""
    from chronicler.db.registry import connect, delete_campaign

    registry = tmp_path / "registry.db"
    camp = create_campaign("Cascade", registry=registry)
    add_tracked_character(camp.id, 12345, note="player", registry=registry)
    add_suppressed_kind(camp.id, "birth", registry=registry)

    assert delete_campaign(camp.id, registry=registry) is True

    with connect(registry) as conn:
        tracked = conn.execute(
            "SELECT COUNT(*) FROM tracked_characters WHERE campaign_id = ?",
            (camp.id,),
        ).fetchone()[0]
        suppressed = conn.execute(
            "SELECT COUNT(*) FROM suppressed_event_kinds WHERE campaign_id = ?",
            (camp.id,),
        ).fetchone()[0]
    assert tracked == 0
    assert suppressed == 0


def test_resolve_campaign_for_save_raises_when_only_match_archived(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-obds: an archived campaign sharing a playthrough_id
    used to silently fork into a duplicate. The 2026-05-07 smoke session
    surfaced the actual harm (lost data visibility). Now it raises
    :class:`ArchivedCampaignConflict` so the caller can either un-archive
    or adopt under a fresh explicit name — never silently fork."""
    from chronicler.db.registry import ArchivedCampaignConflict

    registry = tmp_path / "registry.db"
    sealed = create_campaign(
        "Erik 1066-9-15",
        db_path=str(tmp_path / "erik.db"),
        ck3_playthrough_id="uuid-sealed",
        registry=registry,
    )
    archive_campaign(sealed.id, registry=registry)

    snap = _make_snap_for_resolve(
        playthrough_id="uuid-sealed",
        player_first_name="Erik",
        bookmark_date="1066.9.15",
    )
    with pytest.raises(ArchivedCampaignConflict) as excinfo:
        resolve_campaign_for_save(snap, ck3_version="1.19.0.4", registry=registry)
    assert excinfo.value.archived_campaign.id == sealed.id
    # Registry still has only the one (archived) row — no silent fork.
    all_campaigns = list_campaigns(include_archived=True, registry=registry)
    assert len(all_campaigns) == 1
    assert all_campaigns[0].id == sealed.id


def test_resolve_campaign_for_save_returns_active_match_even_when_archived_exists(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-obds: an active match takes priority. The archived
    sibling is only consulted when no active match exists, so unarchive
    + resume produces an active row that resolves directly."""
    registry = tmp_path / "registry.db"
    # Two rows sharing a playthrough_id: one archived, one active. The
    # active one should win (matches the unarchive-then-resume flow).
    archived = create_campaign(
        "Erik 1066-9-15",
        db_path=str(tmp_path / "erik.db"),
        ck3_playthrough_id="uuid-shared",
        registry=registry,
    )
    archive_campaign(archived.id, registry=registry)
    active = create_campaign(
        "Erik 1066-9-15 (resumed)",
        db_path=str(tmp_path / "erik2.db"),
        ck3_playthrough_id="uuid-shared",
        registry=registry,
    )

    snap = _make_snap_for_resolve(
        playthrough_id="uuid-shared",
        player_first_name="Erik",
        bookmark_date="1066.9.15",
    )
    matched = resolve_campaign_for_save(snap, ck3_version="1.19.0.4", registry=registry)
    assert matched.id == active.id
    assert matched.archived is False


def test_find_archived_match_for_playthrough(tmp_path: Path) -> None:
    """ck3_chronicler-obds: helper returns the archived row, ignores
    active rows, and returns None when no row matches."""
    from chronicler.db.registry import find_archived_match_for_playthrough

    registry = tmp_path / "registry.db"
    archived = create_campaign(
        "Erik 1066-9-15",
        db_path=str(tmp_path / "erik.db"),
        ck3_playthrough_id="uuid-A",
        registry=registry,
    )
    archive_campaign(archived.id, registry=registry)
    create_campaign(
        "Bjorn 1066-9-15",
        db_path=str(tmp_path / "bjorn.db"),
        ck3_playthrough_id="uuid-B",
        registry=registry,
    )

    found = find_archived_match_for_playthrough("uuid-A", registry=registry)
    assert found is not None
    assert found.id == archived.id
    # Active-only playthrough returns None (resolve_campaign_for_save
    # handles the active path itself).
    assert find_archived_match_for_playthrough("uuid-B", registry=registry) is None
    assert find_archived_match_for_playthrough("uuid-X", registry=registry) is None
    # Empty/None playthrough returns None without querying.
    assert find_archived_match_for_playthrough("", registry=registry) is None
    assert find_archived_match_for_playthrough(None, registry=registry) is None
