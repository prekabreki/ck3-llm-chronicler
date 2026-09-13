"""ck3_chronicler-bges: registry persistence for the last save-pair tick.

The five new columns on the campaigns row store the most recent
ingest-tick info so the Library card + IngestActivityStrip have
something to show on cold load (before any live SSE frame arrives).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronicler.db.registry import (
    create_campaign,
    get_campaign_by_id,
    set_campaign_last_tick,
)


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    """Isolated registry dir per test (fresh sqlite file)."""
    return tmp_path / "registry"


def test_fresh_campaign_has_no_last_tick(registry: Path) -> None:
    c = create_campaign("Test Campaign", registry=registry)
    fetched = get_campaign_by_id(c.id, registry=registry)
    assert fetched is not None
    assert fetched.last_save_filename is None
    assert fetched.last_save_ingested_at is None
    assert fetched.last_save_in_game_date is None
    assert fetched.last_tick_event_count is None
    assert fetched.last_tick_event_type_tally is None


def test_set_campaign_last_tick_persists_all_fields(registry: Path) -> None:
    c = create_campaign("Test Campaign", registry=registry)
    set_campaign_last_tick(
        c.id,
        save_filename="autosave.ck3",
        ingested_at="2026-05-10T12:34:56+00:00",
        in_game_date="1066.4.11",
        event_count=3,
        event_type_tally={"marriage": 1, "birth": 2},
        registry=registry,
    )
    fetched = get_campaign_by_id(c.id, registry=registry)
    assert fetched is not None
    assert fetched.last_save_filename == "autosave.ck3"
    assert fetched.last_save_ingested_at == "2026-05-10T12:34:56+00:00"
    assert fetched.last_save_in_game_date == "1066.4.11"
    assert fetched.last_tick_event_count == 3
    assert json.loads(fetched.last_tick_event_type_tally) == {
        "marriage": 1,
        "birth": 2,
    }


def test_set_campaign_last_tick_overwrites_prior_tick(registry: Path) -> None:
    c = create_campaign("Test Campaign", registry=registry)
    set_campaign_last_tick(
        c.id,
        save_filename="autosave.ck3",
        ingested_at="2026-05-10T12:00:00+00:00",
        in_game_date="1066.4.11",
        event_count=3,
        event_type_tally={"marriage": 1, "birth": 2},
        registry=registry,
    )
    set_campaign_last_tick(
        c.id,
        save_filename="autosave.ck3",
        ingested_at="2026-05-10T12:01:00+00:00",
        in_game_date="1066.4.12",
        event_count=0,
        event_type_tally={},
        registry=registry,
    )
    fetched = get_campaign_by_id(c.id, registry=registry)
    assert fetched is not None
    assert fetched.last_save_in_game_date == "1066.4.12"
    assert fetched.last_tick_event_count == 0
    assert json.loads(fetched.last_tick_event_type_tally) == {}
