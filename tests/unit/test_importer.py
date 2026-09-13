"""Tests for the save-importer pipeline + API endpoint (ck3_chronicler-u3m)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from chronicler.api import create_app
from chronicler.db import (
    Base,
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.registry import create_campaign
from chronicler.save.importer import ImportProgress, import_save

# Synthetic rakaly-shaped save dict — minimal structure for the importer.
_FAKE_RAKALY_SAVE = {
    "playthrough_id": "test-playthrough-123",
    "version": "1.19.0",
    "bookmark_date": "1066.9.15",
    "currently_played_characters": [],
    "meta_data": {"version": "1.19.0"},
    "currently_played_character_history": [],
    "date": "1066.9.15",
    "player": [],
    "living": {
        "100": {
            "first_name": "Alfred",
            "birth": "1020.1.1",
            "female": False,
        },
        "200": {
            "first_name": "Eadgyth",
            "birth": "1025.5.10",
            "female": True,
        },
    },
    "dead_unprunable": {},
    "dynasties": {"dynasty_house": {}},
}


@pytest.fixture
def factory_with_schema(tmp_path: Path) -> Iterator:
    db_path = tmp_path / "import.db"
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def fake_save_path(tmp_path: Path) -> Path:
    """A fake .ck3 file (importer only checks is_file)."""
    p = tmp_path / "fake.ck3"
    p.write_bytes(b"not a real ck3 save - patched out via convert_save_to_json")
    return p


def test_import_save_populates_dynasty_and_house_name(
    factory_with_schema, fake_save_path: Path
) -> None:
    """ck3_chronicler-45i: import-save chains
    character.dynasty_house → houses_lookup (house_name) and
    character.dynasty_house → house_to_dynasty → dynasties_lookup
    (dynasty_name). Custom-named houses (localized_name only) and
    string-keyed houses (no ``name``) must populate, not stay NULL."""
    from chronicler.db.models import Character

    save = {
        "playthrough_id": "test-pt",
        "meta_data": {"version": "1.19.0", "meta_date": "1066.9.15"},
        "bookmark_date": "1066.9.15",
        "living": {
            # Engine-named house, name-only dynasty
            "1": {"first_name": "Briain", "birth": "1010.1.1", "dynasty_house": 100},
            # Custom-named (player) house and dynasty
            "2": {"first_name": "Erik", "birth": "1031.1.1", "dynasty_house": 200},
            # House with only string key (e.g. house_munso style)
            "3": {"first_name": "Sigrid", "birth": "1035.1.1", "dynasty_house": 300},
            # Character with no dynasty_house at all → both names stay NULL
            "4": {"first_name": "Bondi", "birth": "1040.1.1"},
        },
        "dead_unprunable": {},
        "dynasties": {
            "dynasty_house": {
                "100": {"name": "dynn_Briain", "dynasty": 50},
                "200": {"localized_name": "House of Erik", "dynasty": 51},
                "300": {"key": "house_munso", "dynasty": 52},
            },
            "dynasties": {
                "50": {"name": "dynn_Briain"},
                "51": {"localized_name": "Custom Dynasty"},
                "52": {"key": 490},  # int key → unresolvable → dynasty_name NULL
            },
        },
    }

    with patch("chronicler.save.importer.convert_save_to_json", return_value=save):
        result = import_save(fake_save_path, factory=factory_with_schema)
    assert result.success is True

    with factory_with_schema() as session:
        c1 = session.get(Character, 1)
        c2 = session.get(Character, 2)
        c3 = session.get(Character, 3)
        c4 = session.get(Character, 4)
        # ck3_chronicler-6d1c: decode_house_name strips ``dynn_`` /
        # ``house_`` namespace prefixes; ``House of Erik`` / ``Custom
        # Dynasty`` carry no prefix and pass through.
        assert c1.house_name == "Briain"
        assert c1.dynasty_name == "Briain"
        assert c2.house_name == "House of Erik"
        assert c2.dynasty_name == "Custom Dynasty"
        # ck3_chronicler-te8s (2026-05-09): when the dynasty's only id
        # is an integer locale key (unresolvable), the importer falls
        # back to the house name. CK3 displays the house name as the
        # dynasty in this case anyway, and prior behaviour of leaving
        # dynasty_name NULL broke the Dynasty page + briefing for
        # ~30% of characters across every campaign.
        assert c3.house_name == "munso"
        assert c3.dynasty_name == "munso"
        # Both NULL — no house, no dynasty record at all. The fallback
        # has no signal to use, so dynasty_name stays NULL. The
        # downstream surfaces (Dynasty page) handle this with a 404
        # safety net (ck3_chronicler-za6f).
        assert c4.house_name is None
        assert c4.dynasty_name is None


def test_import_save_emits_all_five_progress_stages(
    factory_with_schema, fake_save_path: Path
) -> None:
    received: list[ImportProgress] = []

    with patch("chronicler.save.importer.convert_save_to_json", return_value=_FAKE_RAKALY_SAVE):
        result = import_save(
            fake_save_path,
            factory=factory_with_schema,
            progress=received.append,
        )

    assert result.success is True
    assert result.chars_upserted == 2
    stages = [p.stage for p in received]
    assert stages == [
        "read_save",
        "parse_history",
        "backfill_events",
        "generate_biographies",
        "done",
    ]
    fractions = [p.fraction for p in received]
    assert fractions == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_import_save_idempotent_on_rerun(factory_with_schema, fake_save_path: Path) -> None:
    """Running the import twice produces 0 new memory rows and 0 errors."""
    fake_with_memory = dict(_FAKE_RAKALY_SAVE)
    fake_with_memory["character_memory_manager"] = {"database": {}}
    # Add one memory referenced from a character
    fake_with_memory["living"] = {
        "100": {
            "first_name": "Alfred",
            "birth": "1020.1.1",
            "alive_data": {"memories": [1]},
        }
    }
    fake_with_memory["character_memory_manager"]["database"] = {
        "1": {
            "type": "first_meeting",
            "creation_date": "1067.1.1",
            "participants": {"witness": 100},
        }
    }
    with patch("chronicler.save.importer.convert_save_to_json", return_value=fake_with_memory):
        first = import_save(fake_save_path, factory=factory_with_schema)
        second = import_save(fake_save_path, factory=factory_with_schema)
    assert first.memories_inserted == 1
    assert second.memories_inserted == 0
    assert second.memories_duplicate == 1


def test_import_save_returns_failure_for_missing_file(factory_with_schema, tmp_path: Path) -> None:
    """Non-existent save path → ImportResult(success=False)."""
    received: list[ImportProgress] = []
    result = import_save(
        tmp_path / "does-not-exist.ck3",
        factory=factory_with_schema,
        progress=received.append,
    )
    assert result.success is False
    assert "save file not found" in (result.error or "")
    assert received[-1].stage == "error"


def test_import_save_progress_callback_failure_does_not_break_import(
    factory_with_schema, fake_save_path: Path
) -> None:
    """Buggy callbacks don't kill the import — observability is best-effort."""

    def _bad_cb(progress: ImportProgress) -> None:
        raise RuntimeError("oops")

    with patch("chronicler.save.importer.convert_save_to_json", return_value=_FAKE_RAKALY_SAVE):
        result = import_save(fake_save_path, factory=factory_with_schema, progress=_bad_cb)
    assert result.success is True
    assert result.chars_upserted == 2


# --- API endpoint tests ---


@pytest.fixture
def import_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    registry = tmp_path / "registry.db"
    campaign_db = tmp_path / "campaigns" / "imp.db"
    campaign_db.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine_for_path(campaign_db)
    Base.metadata.create_all(engine)
    engine.dispose()
    create_campaign("imp", db_path=str(campaign_db), registry=registry)
    app = create_app(registry_path=registry)
    return app, registry


@pytest.mark.asyncio
async def test_import_endpoint_returns_202_with_import_id(import_app, fake_save_path: Path) -> None:
    app, _registry = import_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("chronicler.save.importer.convert_save_to_json", return_value=_FAKE_RAKALY_SAVE):
            resp = await client.post(
                "/api/campaigns/imp/import-save",
                json={"save_path": str(fake_save_path)},
            )
    assert resp.status_code == 202
    data = resp.json()
    assert "import_id" in data
    assert data["campaign_name"] == "imp"
    assert data["sse_url"] == f"/api/sse/import/{data['import_id']}"


@pytest.mark.asyncio
async def test_import_endpoint_400_for_missing_save_path(import_app) -> None:
    app, _registry = import_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/campaigns/imp/import-save",
            json={"save_path": "/nope/does/not/exist.ck3"},
        )
    assert resp.status_code == 400
    assert "save file not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_import_endpoint_404_for_unknown_campaign(import_app, fake_save_path: Path) -> None:
    app, _registry = import_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/campaigns/ghost/import-save",
            json={"save_path": str(fake_save_path)},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_endpoint_publishes_progress_to_bus(import_app, fake_save_path: Path) -> None:
    """End-to-end: POST /import-save publishes progress events on the
    bus channel matching the returned import_id."""
    app, _registry = import_app
    bus = app.state.event_bus
    transport = httpx.ASGITransport(app=app)
    received: list[dict] = []
    stop = asyncio.Event()
    sub_started = asyncio.Event()

    async def consume(import_id: str):
        sub_started.set()
        async for event in bus.subscribe(f"import:{import_id}", stop_event=stop):
            received.append(event)
            if event.get("stage") in ("done", "error"):
                stop.set()

    # Start the import via HTTP, then immediately subscribe.
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("chronicler.save.importer.convert_save_to_json", return_value=_FAKE_RAKALY_SAVE):
            resp = await client.post(
                "/api/campaigns/imp/import-save",
                json={"save_path": str(fake_save_path)},
            )
            import_id = resp.json()["import_id"]

            consumer = asyncio.create_task(consume(import_id))
            await sub_started.wait()
            # Wait for done or timeout
            try:
                await asyncio.wait_for(consumer, timeout=10.0)
            except TimeoutError:
                stop.set()
                consumer.cancel()
                raise

    stages = [e["stage"] for e in received]
    assert "done" in stages or "error" in stages
    # The first stage we should see (assuming the subscriber registered
    # before any publish) is read_save. But there's a race — if the
    # background task ran ahead of subscription, we may have missed
    # earlier stages. Just check that we got at least the terminal
    # event.
    assert all(e["import_id"] == import_id for e in received)
