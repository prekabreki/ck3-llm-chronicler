"""Tests for chronicler.db.backfills (ck3_chronicler-te8s).

Verifies the dynasty-from-house retroactive UPDATE is correct,
idempotent, and only touches rows that actually need fixing.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from chronicler.db.backfills import (
    _backfill_one_campaign,
    backfill_dynasty_name_from_house_name,
    backfill_last_event_in_game_date,
)
from chronicler.db.registry import (
    create_campaign,
    get_campaign_by_id,
    touch_last_event_at,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data_dir"))


def _seed_campaign_with_chars(
    tmp_path: Path,
    *,
    name: str,
    chars: list[tuple[int, str | None, str | None]],
) -> Path:
    """Create a registry + campaign DB with `chars` rows. Each tuple is
    (ck3_id, dynasty_name, house_name). Returns the registry path."""
    os.environ["CHRONICLER_DATA_DIR"] = str(tmp_path / "data_dir")
    registry_path = tmp_path / "data_dir" / "registry.db"
    (tmp_path / "data_dir" / "campaigns").mkdir(parents=True, exist_ok=True)
    campaign = create_campaign(name, registry=registry_path)
    with sqlite3.connect(campaign.db_path) as conn:
        # Schema-light fixture — only the columns the backfill touches.
        # The backfill's UPDATE targets characters.dynasty_name and
        # characters.house_name; nothing else needs to be present.
        conn.execute(
            "CREATE TABLE characters ("
            "ck3_id INTEGER PRIMARY KEY, "
            "first_name TEXT, "
            "dynasty_name TEXT, "
            "house_name TEXT)"
        )
        conn.executemany(
            "INSERT INTO characters (ck3_id, first_name, dynasty_name, house_name) "
            "VALUES (?, ?, ?, ?)",
            [(cid, f"Char{cid}", dyn, house) for cid, dyn, house in chars],
        )
    return registry_path


def test_backfill_one_campaign_fills_null_dynasty_with_house(
    tmp_path: Path,
) -> None:
    """The core invariant: a row with NULL dynasty_name but a non-NULL
    house_name gets dynasty_name = house_name. Returns the count."""
    registry = _seed_campaign_with_chars(
        tmp_path,
        name="Wessex",
        chars=[
            (1, None, "Barcelona"),  # NULL dyn + house → fills
            (2, "Plantagenet", "York"),  # already set → unchanged
            (3, None, None),  # both NULL → unchanged
            (4, "Tudor", None),  # dyn set, house NULL → unchanged
        ],
    )
    from chronicler.db.registry import get_campaign_by_name

    campaign = get_campaign_by_name("Wessex", registry=registry)
    assert campaign is not None
    updated = _backfill_one_campaign(Path(campaign.db_path))
    assert updated == 1

    with sqlite3.connect(campaign.db_path) as conn:
        rows = {
            r[0]: (r[1], r[2])
            for r in conn.execute("SELECT ck3_id, dynasty_name, house_name FROM characters")
        }
        assert rows[1] == ("Barcelona", "Barcelona")
        assert rows[2] == ("Plantagenet", "York")  # untouched
        assert rows[3] == (None, None)  # untouched
        assert rows[4] == ("Tudor", None)  # untouched


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    """Running the backfill twice on the same DB only writes on the
    first call — the second call's WHERE clause matches no rows."""
    registry = _seed_campaign_with_chars(
        tmp_path,
        name="Idempotent",
        chars=[(1, None, "Barcelona"), (2, None, "York")],
    )
    from chronicler.db.registry import get_campaign_by_name

    campaign = get_campaign_by_name("Idempotent", registry=registry)
    assert campaign is not None

    first = _backfill_one_campaign(Path(campaign.db_path))
    second = _backfill_one_campaign(Path(campaign.db_path))
    assert first == 2
    assert second == 0


def test_backfill_dynasty_name_from_house_name_walks_all_campaigns(
    tmp_path: Path,
) -> None:
    """The top-level helper iterates every campaign in the registry
    (active + archived) and returns the summed update count."""
    registry = _seed_campaign_with_chars(
        tmp_path,
        name="Wessex",
        chars=[(1, None, "Barcelona")],
    )
    # A second campaign in the same registry.
    campaign2 = create_campaign("Mercia", registry=registry)
    with sqlite3.connect(campaign2.db_path) as conn:
        conn.execute(
            "CREATE TABLE characters ("
            "ck3_id INTEGER PRIMARY KEY, "
            "first_name TEXT, "
            "dynasty_name TEXT, "
            "house_name TEXT)"
        )
        conn.executemany(
            "INSERT INTO characters (ck3_id, first_name, dynasty_name, house_name) "
            "VALUES (?, ?, ?, ?)",
            [(2, "Penda", None, "Iclingas"), (3, "Æthelflæd", "Wessex", "Wessex")],
        )

    total = backfill_dynasty_name_from_house_name(registry_path=registry)
    # 1 from Wessex + 1 from Mercia = 2 rows touched
    assert total == 2


def test_backfill_skips_campaigns_with_missing_db(tmp_path: Path) -> None:
    """If the per-campaign db_path doesn't exist on disk (half-cloned
    repo, broken symlink, etc.), the backfill logs and skips rather
    than raising."""
    registry = _seed_campaign_with_chars(
        tmp_path,
        name="Real",
        chars=[(1, None, "Barcelona")],
    )
    # Manually register a campaign whose db_path doesn't exist.
    create_campaign(
        "Phantom",
        db_path=str(tmp_path / "ghost.db"),
        registry=registry,
    )
    total = backfill_dynasty_name_from_house_name(registry_path=registry)
    assert total == 1  # Real fixed; Phantom skipped


# --- ck3_chronicler-9xa6: last_event_in_game_date backfill ---


def _seed_campaign_with_events(
    tmp_path: Path,
    *,
    name: str,
    events: list[tuple[int, str | None]],
) -> Path:
    """Create a registry + campaign DB with events rows. Each tuple is
    (id, event_date_iso). Returns the registry path."""
    os.environ["CHRONICLER_DATA_DIR"] = str(tmp_path / "data_dir")
    registry_path = tmp_path / "data_dir" / "registry.db"
    (tmp_path / "data_dir" / "campaigns").mkdir(parents=True, exist_ok=True)
    campaign = create_campaign(name, registry=registry_path)
    with sqlite3.connect(campaign.db_path) as conn:
        # Schema-light fixture — only the column the backfill reads.
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, event_date_iso TEXT)")
        conn.executemany(
            "INSERT INTO events (id, event_date_iso) VALUES (?, ?)",
            events,
        )
    return registry_path


def test_9xa6_backfill_writes_max_event_date_iso_to_registry(
    tmp_path: Path,
) -> None:
    """The core invariant: a campaign with non-NULL events.event_date_iso
    rows gets last_event_in_game_date = MAX(event_date_iso) on the
    registry row. Returns the count of campaigns touched."""
    from chronicler.db.registry import get_campaign_by_name

    registry = _seed_campaign_with_events(
        tmp_path,
        name="Wessex",
        events=[(1, "1066-09-15"), (2, "1071-04-12"), (3, "1069-12-25")],
    )
    pre = get_campaign_by_name("Wessex", registry=registry)
    assert pre is not None and pre.last_event_in_game_date is None

    updated = backfill_last_event_in_game_date(registry_path=registry)
    assert updated == 1
    post = get_campaign_by_name("Wessex", registry=registry)
    assert post is not None
    assert post.last_event_in_game_date == "1071-04-12"


def test_9xa6_backfill_is_idempotent(tmp_path: Path) -> None:
    """Once last_event_in_game_date is set, a second run is a no-op."""
    from chronicler.db.registry import get_campaign_by_name

    registry = _seed_campaign_with_events(
        tmp_path,
        name="Idempotent",
        events=[(1, "1066-09-15")],
    )
    first = backfill_last_event_in_game_date(registry_path=registry)
    second = backfill_last_event_in_game_date(registry_path=registry)
    assert first == 1
    assert second == 0
    # Value preserved exactly.
    campaign = get_campaign_by_name("Idempotent", registry=registry)
    assert campaign is not None
    assert campaign.last_event_in_game_date == "1066-09-15"


def test_9xa6_backfill_does_not_overwrite_existing_in_game_date(
    tmp_path: Path,
) -> None:
    """A campaign whose registry row already has the column set (because
    a post-9xa6 save-tail tick wrote to it via touch_last_event_at) is
    skipped — the backfill is for legacy NULL rows only."""
    from chronicler.db.registry import get_campaign_by_name

    registry = _seed_campaign_with_events(
        tmp_path,
        name="Live",
        events=[(1, "1066-09-15")],
    )
    campaign = get_campaign_by_name("Live", registry=registry)
    assert campaign is not None
    # Simulate save-tail having written a (probably-newer) value.
    touch_last_event_at(campaign.id, in_game_date="1126.5.18", registry=registry)
    updated = backfill_last_event_in_game_date(registry_path=registry)
    assert updated == 0
    refetched = get_campaign_by_id(campaign.id, registry=registry)
    assert refetched is not None
    assert refetched.last_event_in_game_date == "1126.5.18"  # unchanged


def test_9xa6_backfill_skips_campaign_with_no_events(tmp_path: Path) -> None:
    """A campaign DB with an empty events table (or only NULL event_date_iso
    rows) leaves the registry column NULL — no zombie writes."""
    from chronicler.db.registry import get_campaign_by_name

    registry = _seed_campaign_with_events(
        tmp_path,
        name="Empty",
        events=[(1, None), (2, None)],
    )
    updated = backfill_last_event_in_game_date(registry_path=registry)
    assert updated == 0
    campaign = get_campaign_by_name("Empty", registry=registry)
    assert campaign is not None
    assert campaign.last_event_in_game_date is None


def test_9xa6_backfill_skips_missing_db(tmp_path: Path) -> None:
    """As with the te8s backfill, a missing per-campaign DB is logged
    and skipped rather than raising."""
    registry = _seed_campaign_with_events(
        tmp_path,
        name="Real",
        events=[(1, "1066-09-15")],
    )
    create_campaign(
        "Phantom",
        db_path=str(tmp_path / "ghost.db"),
        registry=registry,
    )
    total = backfill_last_event_in_game_date(registry_path=registry)
    assert total == 1  # Real backfilled; Phantom skipped
