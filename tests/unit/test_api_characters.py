"""HTTP-level tests for the chronicler characters API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from chronicler.db import (
    make_engine_for_path,
)
from chronicler.db.repository import (
    upsert_character,
)
from tests.helpers.api import CampaignHarness


def test_list_characters(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["ck3_id"] == 36892
    assert rows[0]["first_name"] == "Eadmund"


def test_list_characters_pagination_validated(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters?limit=0")
    assert resp.status_code == 400
    resp = client.get("/api/campaigns/test-campaign/characters?offset=-1")
    assert resp.status_code == 400


def test_get_character_detail(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters/36892")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ck3_id"] == 36892
    assert data["first_name"] == "Eadmund"
    assert data["birth_date"] == "1049.12.10"
    assert len(data["events"]) == 1
    assert data["events"][0]["type"] == "vanilla_memory"
    assert data["events"][0]["payload"]["p"]["memory_type"] == "relative_died"


def test_get_unknown_character_404(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters/99999")
    assert resp.status_code == 404


def test_get_biography(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters/36892/biography")
    assert resp.status_code == 200
    data = resp.json()
    assert "Eadmund" in data["body"]
    assert data["version"] == 1
    assert data["provider"] == "test:fake"


def test_get_biography_404_when_none(client: TestClient) -> None:
    """Insert a character with no biography, expect 404."""
    # Add a second char via the API client's app — directly via repository.
    # Pull engine via a fresh open of the campaign DB
    from sqlalchemy.orm import sessionmaker

    from chronicler.db.repository import upsert_character

    # The cached engine is in the app's EngineCache; use the same DB path.
    db_path = client.get("/api/campaigns/test-campaign").json()["db_path"]
    engine = make_engine_for_path(Path(db_path))
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        upsert_character(s, ck3_id=99999, first_name="Nobio")
        s.commit()
    engine.dispose()

    resp = client.get("/api/campaigns/test-campaign/characters/99999/biography")
    assert resp.status_code == 404


def test_openapi_renders(client: TestClient) -> None:
    """The /docs page is automatically wired by FastAPI; just smoke."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "chronicler"
    # Sanity-check a couple of endpoints made it into the spec
    paths = spec["paths"]
    assert "/api/campaigns" in paths
    assert "/api/campaigns/{name}/characters/{ck3_id}" in paths


# --- HTML / HTMX page tests retired in v0.7 Phase 6 (ck3_chronicler-789).
# The Jinja+HTMX UI was replaced by the React SPA; routes/web.py and
# templates/* (except spa_not_built.html) were deleted. Equivalent
# coverage now lives in frontend/src/pages/*.test.tsx. ---


# --- ck3_chronicler-7ao: character CoA endpoint ---


def test_get_character_coa_returns_persisted_structure(
    api: CampaignHarness,
) -> None:
    """When Character.coa_json is populated, GET /coa returns the
    parsed JSON structure (the same shape CK3 emits in
    coat_of_arms_manager_database)."""
    coa_payload = {
        "pattern": "pattern_solid.dds",
        "color1": "black",
        "color2": "green",
        "color3": "yellow",
        "sub": {
            "pattern": "pattern_vertical_split_01.dds",
            "color1": "red",
            "color2": "yellow",
            "colored_emblem": {
                "texture": "ce_leopard_passant_guardant.dds",
                "color1": "white",
                "color2": "white",
                "color3": "black",
            },
        },
    }
    api.campaign(
        "coa-test",
        seed=lambda s: upsert_character(
            s,
            ck3_id=29160,
            first_name="Toirrdelbach",
            coa_json=json.dumps(coa_payload, separators=(",", ":")),
        ),
    )
    c = api.client()
    resp = c.get("/api/campaigns/coa-test/characters/29160/coa")
    assert resp.status_code == 200
    body = resp.json()
    assert body == coa_payload
    # Spot-check the deeply-nested charge survived round-trip
    assert body["sub"]["colored_emblem"]["texture"] == "ce_leopard_passant_guardant.dds"


def test_get_character_coa_404_when_not_persisted(client: TestClient) -> None:
    """The fixture character (Eadmund 36892) has no coa_json — endpoint
    returns 404 with a hint about tracking + save-tail."""
    resp = client.get("/api/campaigns/test-campaign/characters/36892/coa")
    assert resp.status_code == 404
    assert "no CoA persisted" in resp.json()["detail"]


def test_get_character_coa_404_when_unknown_character(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters/99999/coa")
    assert resp.status_code == 404


# --- ck3_chronicler-fjln: per-character stats endpoint ---


def test_narrative_character_stats_empty_by_default(client: TestClient) -> None:
    """fjln: with nothing completed yet, the rows list is empty.
    Frontend uses this to skip rendering the stats panel."""
    resp = client.get("/api/settings/narrative-queue/character-stats")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


def test_narrative_character_stats_serialises_aggregates(client: TestClient) -> None:
    """fjln: the endpoint mirrors NarrativeQueueState.character_stats
    over the wire. One row per (character_id, kind) pair the scheduler
    has touched, stable-ordered."""
    queue = client.app.state.narrative_queue
    a = queue.enqueue(1234, "biography")
    queue.mark_completed(a, duration_ms=2500)
    b = queue.enqueue(5678, "biography")
    queue.mark_failed(b, "ollama 500")

    resp = client.get("/api/settings/narrative-queue/character-stats")
    data = resp.json()
    rows = data["rows"]
    # Stable order: (1234, biography), (5678, biography)
    assert [(r["character_id"], r["kind"]) for r in rows] == [
        (1234, "biography"),
        (5678, "biography"),
    ]
    bio_1234 = rows[0]
    assert bio_1234["completed_count"] == 1
    assert bio_1234["failed_count"] == 0
    assert bio_1234["median_duration_ms"] == 2500
    assert bio_1234["last_success_at"] is not None
    assert "T" in bio_1234["last_success_at"]
    bio_5678 = rows[1]
    assert bio_5678["completed_count"] == 0
    assert bio_5678["failed_count"] == 1
    assert bio_5678["median_duration_ms"] is None
    assert bio_5678["last_success_at"] is None


def test_narrative_character_stats_serialises_character_name(
    client: TestClient,
) -> None:
    """ck3_chronicler-27ov.81 (audit L30): the stats row carries the name
    stamped from the QueueItem, so the queue page's stats table renders
    names without a FE join. None until the name is stamped / when it was
    never resolved at enqueue."""
    queue = client.app.state.narrative_queue
    a = queue.enqueue(1234, "biography", character_name="Erik")
    queue.mark_completed(a, duration_ms=2500)
    b = queue.enqueue(5678, "biography")  # no name resolved
    queue.mark_failed(b, "timeout")

    rows = client.get("/api/settings/narrative-queue/character-stats").json()["rows"]
    by_cid = {r["character_id"]: r for r in rows}
    assert by_cid[1234]["character_name"] == "Erik"
    assert by_cid[5678]["character_name"] is None


# --- ck3_chronicler-7b8d: CoA history endpoint ---


def test_coa_history_404_unknown_character(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters/99999/coa-history")
    assert resp.status_code == 404


def test_coa_history_returns_empty_when_never_recorded(client: TestClient) -> None:
    """Eadmund (test-campaign fixture) has no CoA history — empty entries."""
    resp = client.get("/api/campaigns/test-campaign/characters/36892/coa-history")
    assert resp.status_code == 200
    assert resp.json() == {"entries": []}


def test_coa_history_returns_recorded_entries_oldest_first(
    client: TestClient,
) -> None:
    """Seed two history rows directly via the repo and confirm order."""
    from chronicler.db.registry import get_campaign_by_name
    from chronicler.db.repository import append_coa_history_if_changed

    cache = client.app.state.engine_cache
    camp = get_campaign_by_name("test-campaign", registry=cache.registry_path)
    assert camp is not None
    factory = cache.factory_for(camp)
    with factory() as s:
        append_coa_history_if_changed(
            s,
            character_id=36892,
            coa_json='{"pattern":"a"}',
            observed_at="2026-05-06T10:00:00+00:00",
        )
        append_coa_history_if_changed(
            s,
            character_id=36892,
            coa_json='{"pattern":"b"}',
            observed_at="2026-05-06T11:00:00+00:00",
        )
        s.commit()

    resp = client.get("/api/campaigns/test-campaign/characters/36892/coa-history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entries"]) == 2
    assert body["entries"][0]["coa"] == {"pattern": "a"}
    assert body["entries"][1]["coa"] == {"pattern": "b"}
    assert body["entries"][0]["observed_at"] == "2026-05-06T10:00:00+00:00"
