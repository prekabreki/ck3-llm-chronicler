"""HTTP-level tests for the chronicler tracked-characters API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.db.repository import (
    insert_biography,
    insert_event_idempotent,
    upsert_character,
)
from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-4cl: GET /tracked endpoint ---


def _seed_tracked_characters(s: Session) -> None:
    """Seed the ``tracked`` campaign's 3 characters with varying activity
    (player with several biographies, heir with one, plus a pending char
    registered in the fixture). Tracked-char notes/roles are applied in
    the fixture after registration."""
    this_month = datetime.now(UTC).strftime("%Y-%m")
    last_month = datetime(2020, 1, 1, tzinfo=UTC).strftime("%Y-%m")
    # Player char (lots of bios, this month)
    upsert_character(s, ck3_id=100, first_name="Toirrdelbach", nickname="the Cunning")
    eid_player = insert_event_idempotent(
        s,
        schema_version=1,
        event_type="vanilla_memory",
        event_date="1066.5.1",
        event_date_iso="1066-05-01",
        wall_clock_at=f"{this_month}-01T00:00:00+00:00",
        primary_character_id=100,
        payload_json="{}",
        raw_line="seed",
    )
    insert_biography(
        s,
        character_id=100,
        body="Bio 1.",
        prompt_template_version="biography_v1",
        provider="anthropic",
        generated_at=f"{this_month}-15T10:00:00+00:00",
        events_through_event_id=eid_player,
        prompt_tokens=100,
        completion_tokens=200,
    )
    insert_biography(
        s,
        character_id=100,
        body="Bio 2.",
        prompt_template_version="biography_v1",
        provider="anthropic",
        generated_at=f"{this_month}-20T10:00:00+00:00",
        events_through_event_id=eid_player,
        prompt_tokens=150,
        completion_tokens=250,
    )
    # Old biography (not in current month) — should not contribute to spend
    insert_biography(
        s,
        character_id=100,
        body="Bio old.",
        prompt_template_version="biography_v1",
        provider="anthropic",
        generated_at=f"{last_month}-15T10:00:00+00:00",
        events_through_event_id=eid_player,
        prompt_tokens=999,
        completion_tokens=999,
    )
    # An extra this-month biography so the monthly_token_spend
    # assertion below still has three contributing rows on this
    # character (the dropped Memory was the third). Keeps the
    # tracked endpoint's spend arithmetic identical pre/post the
    # memory-pipeline demolition.
    insert_biography(
        s,
        character_id=100,
        body="Bio 3.",
        prompt_template_version="biography_v1",
        provider="anthropic",
        generated_at=f"{this_month}-16T10:00:00+00:00",
        events_through_event_id=eid_player,
        prompt_tokens=50,
        completion_tokens=75,
    )

    # Heir (one bio, no monthly spend tokens recorded)
    upsert_character(s, ck3_id=200, first_name="Cuthbert")
    eid_heir = insert_event_idempotent(
        s,
        schema_version=1,
        event_type="vanilla_memory",
        event_date="1066.6.1",
        event_date_iso="1066-06-01",
        wall_clock_at=f"{this_month}-02T00:00:00+00:00",
        primary_character_id=200,
        payload_json="{}",
        raw_line="seed-heir",
    )
    insert_biography(
        s,
        character_id=200,
        body="Heir bio.",
        prompt_template_version="biography_v1",
        provider="ollama:test",
        generated_at=f"{this_month}-10T10:00:00+00:00",
        events_through_event_id=eid_heir,
        # NULL token columns — should count as 0 in monthly_token_spend
    )


@pytest.fixture
def tracked_client(api: CampaignHarness) -> TestClient:
    """A ``tracked`` campaign with 3 tracked characters of varying activity."""
    from chronicler.db.registry import add_tracked_character

    camp = api.campaign("tracked", seed=_seed_tracked_characters)
    add_tracked_character(camp.id, 100, note="player char", role="player", registry=api.registry)
    add_tracked_character(camp.id, 200, role="heir", registry=api.registry)
    # Pending: tracked but not yet in characters table
    add_tracked_character(camp.id, 300, role="rival", registry=api.registry)
    return api.client()


def test_tracked_endpoint_lists_all_tracked_chars(tracked_client: TestClient) -> None:
    resp = tracked_client.get("/api/campaigns/tracked/tracked")
    assert resp.status_code == 200
    rows = resp.json()
    ids = [r["character_id"] for r in rows]
    assert ids == [100, 200, 300]  # added_at ordering


def test_tracked_endpoint_pulls_character_metadata(tracked_client: TestClient) -> None:
    resp = tracked_client.get("/api/campaigns/tracked/tracked")
    by_id = {r["character_id"]: r for r in resp.json()}
    assert by_id[100]["first_name"] == "Toirrdelbach"
    assert by_id[100]["nickname"] == "the Cunning"
    assert by_id[100]["role"] == "player"


def test_tracked_endpoint_aggregates_counts_and_monthly_spend(
    tracked_client: TestClient,
) -> None:
    resp = tracked_client.get("/api/campaigns/tracked/tracked")
    by_id = {r["character_id"]: r for r in resp.json()}
    # Toirrdelbach: 4 bios (one is last-month so it doesn't contribute
    # to monthly_token_spend). monthly_token_spend = (100+200) +
    # (150+250) + (50+75) = 825. (Plan cozy-coalescing-shannon: the
    # third this-month contribution used to come from a Memory row;
    # it's now a fourth biography with the same token shape.)
    assert by_id[100]["biography_count"] == 4
    assert by_id[100]["monthly_token_spend"] == 825
    # Cuthbert: 1 bio with NULL tokens
    assert by_id[200]["biography_count"] == 1
    assert by_id[200]["monthly_token_spend"] == 0


def test_tracked_endpoint_handles_pending_character(tracked_client: TestClient) -> None:
    """Char 300 is tracked but not yet in characters table — still listed
    with first_name=None, all stats zero. Frontend renders as 'pending'."""
    resp = tracked_client.get("/api/campaigns/tracked/tracked")
    by_id = {r["character_id"]: r for r in resp.json()}
    assert by_id[300]["first_name"] is None
    assert by_id[300]["nickname"] is None
    assert by_id[300]["role"] == "rival"
    assert by_id[300]["biography_count"] == 0
    assert by_id[300]["monthly_token_spend"] == 0


def test_tracked_endpoint_empty_when_nothing_tracked(client: TestClient) -> None:
    """The base 'test-campaign' fixture has no tracked rows — empty list."""
    resp = client.get("/api/campaigns/test-campaign/tracked")
    assert resp.status_code == 200
    assert resp.json() == []


def test_tracked_endpoint_404_unknown_campaign(client: TestClient) -> None:
    resp = client.get("/api/campaigns/nope/tracked")
    assert resp.status_code == 404


# --- ck3_chronicler-ogi: POST /tracked, DELETE /tracked, POST /tracked/auto-track ---


def test_post_tracked_adds_character(client: TestClient) -> None:
    """POST /tracked persists the row + returns the new TrackedResponse so
    the frontend can splice it into its query cache."""
    resp = client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892, "note": "the protagonist", "role": "player"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["character_id"] == 36892
    assert body["role"] == "player"
    assert body["first_name"] == "Eadmund"  # from the test-campaign fixture
    # The list endpoint now sees this row.
    list_resp = client.get("/api/campaigns/test-campaign/tracked")
    assert any(r["character_id"] == 36892 for r in list_resp.json())


def test_post_tracked_is_idempotent(client: TestClient) -> None:
    """Re-adding doesn't error and updates fields without resetting added_at."""
    resp1 = client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892, "role": "player"},
    )
    assert resp1.status_code == 201
    added_at = resp1.json()["added_at"]
    resp2 = client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892, "role": "rival"},
    )
    assert resp2.status_code == 201
    body = resp2.json()
    assert body["role"] == "rival"
    assert body["added_at"] == added_at  # original timestamp preserved


def test_post_tracked_unknown_campaign_404(client: TestClient) -> None:
    resp = client.post(
        "/api/campaigns/nope/tracked",
        json={"character_id": 36892},
    )
    assert resp.status_code == 404


def test_post_tracked_unknown_character_returns_pending(client: TestClient) -> None:
    """Tracking a character_id not yet in the per-campaign DB succeeds —
    the row's first_name/nickname surface as null until save-tail
    refreshes them. Mirrors the existing GET /tracked behaviour."""
    resp = client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 999_999, "role": "rival"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["character_id"] == 999_999
    assert body["first_name"] is None
    assert body["nickname"] is None


def test_delete_tracked_removes_row(client: TestClient) -> None:
    # Add then delete.
    client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892},
    )
    resp = client.delete("/api/campaigns/test-campaign/tracked/36892")
    assert resp.status_code == 204
    list_resp = client.get("/api/campaigns/test-campaign/tracked")
    assert all(r["character_id"] != 36892 for r in list_resp.json())


def test_delete_tracked_404_when_not_tracked(client: TestClient) -> None:
    resp = client.delete("/api/campaigns/test-campaign/tracked/12345")
    assert resp.status_code == 404


def test_auto_track_503_when_no_save_found(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an autosave reachable, /auto-track surfaces a 503 with a
    helpful 'launch CK3 first' hint — same shape as other infrastructure
    503s in the API."""
    # Point the CK3 save dir at a fresh empty tmp dir so _latest_save
    # finds nothing.
    empty = tmp_path / "empty-saves"
    empty.mkdir()
    monkeypatch.setattr("chronicler.config.get_ck3_save_dir", lambda: empty)
    resp = client.post("/api/campaigns/test-campaign/tracked/auto-track")
    assert resp.status_code == 503
    assert "no CK3 autosave" in resp.json()["detail"]


def test_auto_track_adds_player_and_family(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a synthesised SaveSnapshot is fed through auto-track;
    candidates land in the registry as tracked rows. Stubs the parsing
    pipeline so the test stays unit-scoped (no rakaly invocation)."""
    from dataclasses import field

    @dataclass
    class _Family:
        primary_spouse: int | None = None
        spouses: list[int] = field(default_factory=list)
        children: list[int] = field(default_factory=list)
        mother: int | None = None
        father: int | None = None

    @dataclass
    class _Char:
        first_name: str = "Eadmund"
        family: _Family = field(default_factory=_Family)

    @dataclass
    class _Snap:
        playthrough_id: str = "pt-fixture-001"
        player_character_id: int = 100
        characters: dict = field(default_factory=dict)

    # Player + spouse + 1 child, no parents.
    snap = _Snap(
        characters={
            100: _Char(first_name="Player", family=_Family(primary_spouse=200, children=[300])),
            200: _Char(first_name="Spouse"),
            300: _Char(first_name="Child"),
        }
    )

    # A real file at a fake path so save_path.is_file() passes.
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    fake_save = save_dir / "autosave.ck3"
    fake_save.write_bytes(b"fake")
    monkeypatch.setattr("chronicler.config.get_ck3_save_dir", lambda: save_dir)
    monkeypatch.setattr("chronicler.save.ingest._latest_save", lambda *_a, **_k: fake_save)
    monkeypatch.setattr("chronicler.save.convert_save_to_json", lambda _p: {})
    monkeypatch.setattr("chronicler.save.parse_save", lambda _j: snap)
    # No playthrough check noise — pin on first observation.
    monkeypatch.setattr(
        "chronicler.api.routes.tracked.assert_playthrough_or_pin",
        lambda *_a, **_k: None,
    )

    resp = client.post("/api/campaigns/test-campaign/tracked/auto-track")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    added_ids = [r["character_id"] for r in body["added"]]
    assert sorted(added_ids) == [100, 200, 300]
    assert body["already_tracked"] == []
    assert body["save_path"].endswith("autosave.ck3")

    # Re-running is idempotent — all candidates skip into already_tracked.
    resp2 = client.post("/api/campaigns/test-campaign/tracked/auto-track")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["added"] == []
    assert sorted(body2["already_tracked"]) == [100, 200, 300]


def test_delete_campaign_clears_dependent_tracked_rows(
    client: TestClient,
) -> None:
    """tracked_characters rows for the deleted campaign are removed
    too, even though the registry doesn't run with PRAGMA foreign_keys=ON."""
    from chronicler.db.registry import (
        add_tracked_character,
        get_campaign_by_name,
        list_tracked_characters,
    )

    cache = client.app.state.engine_cache
    camp = get_campaign_by_name("test-campaign", registry=cache.registry_path)
    assert camp is not None
    add_tracked_character(camp.id, 36892, registry=cache.registry_path)
    assert len(list_tracked_characters(camp.id, registry=cache.registry_path)) == 1

    resp = client.delete("/api/campaigns/test-campaign")
    assert resp.status_code == 204
    # Registry-side cleanup: tracked rows for the gone campaign are gone.
    assert list_tracked_characters(camp.id, registry=cache.registry_path) == []


# --- ck3_chronicler-gw16: auto-track rules + suggested candidates ---


def test_auto_track_rules_returns_defaults_for_unset_campaign(
    client: TestClient,
) -> None:
    """A fresh campaign has no auto_track_rules JSON → endpoint returns
    AUTO_TRACK_RULES_DEFAULT (heirs+spouses on, county-vassals off)."""
    resp = client.get("/api/campaigns/test-campaign/auto-track-rules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["include_heirs"] is True
    assert body["include_spouses"] is True
    assert body["include_county_vassals"] is False


def test_auto_track_rules_round_trip(client: TestClient) -> None:
    """PUT a partial rules body, then GET reflects it."""
    resp = client.put(
        "/api/campaigns/test-campaign/auto-track-rules",
        json={"include_heirs": False},
    )
    assert resp.status_code == 200
    assert resp.json()["include_heirs"] is False
    # Other fields untouched.
    assert resp.json()["include_spouses"] is True

    follow = client.get("/api/campaigns/test-campaign/auto-track-rules")
    assert follow.json() == {
        "include_heirs": False,
        "include_spouses": True,
        "include_grandchildren": True,
        "include_county_vassals": False,
    }


def test_auto_track_honors_persisted_rules(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: persist include_heirs=False, run /auto-track, and
    confirm the child snap_member is NOT added (only player + spouse +
    parents land)."""
    from dataclasses import field

    @dataclass
    class _Family:
        primary_spouse: int | None = None
        spouses: list[int] = field(default_factory=list)
        children: list[int] = field(default_factory=list)
        mother: int | None = None
        father: int | None = None

    @dataclass
    class _Char:
        first_name: str = "Eadmund"
        family: _Family = field(default_factory=_Family)

    @dataclass
    class _Snap:
        playthrough_id: str = "pt-fixture-gw16"
        player_character_id: int = 100
        characters: dict = field(default_factory=dict)

    snap = _Snap(
        characters={
            100: _Char(first_name="Player", family=_Family(primary_spouse=200, children=[300])),
            200: _Char(first_name="Spouse"),
            300: _Char(first_name="Child"),
        }
    )

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    fake_save = save_dir / "autosave.ck3"
    fake_save.write_bytes(b"fake")
    monkeypatch.setattr("chronicler.config.get_ck3_save_dir", lambda: save_dir)
    monkeypatch.setattr("chronicler.save.ingest._latest_save", lambda *_a, **_k: fake_save)
    monkeypatch.setattr("chronicler.save.convert_save_to_json", lambda _p: {})
    monkeypatch.setattr("chronicler.save.parse_save", lambda _j: snap)
    monkeypatch.setattr(
        "chronicler.api.routes.tracked.assert_playthrough_or_pin",
        lambda *_a, **_k: None,
    )

    # Persist rules: heirs OFF.
    client.put(
        "/api/campaigns/test-campaign/auto-track-rules",
        json={"include_heirs": False},
    )
    resp = client.post("/api/campaigns/test-campaign/tracked/auto-track")
    assert resp.status_code == 200, resp.text
    added_ids = sorted(r["character_id"] for r in resp.json()["added"])
    # No 300 (child) because heirs=off.
    assert added_ids == [100, 200]


def test_suggested_candidates_returns_living_untracked_relatives(
    client: TestClient,
) -> None:
    """Seed the test-campaign player with two living children + one
    dead child + one already-tracked child; only the two living
    untracked rows come back."""
    import json as _json

    from chronicler.db.engine import session_scope
    from chronicler.db.registry import (
        add_tracked_character,
        get_campaign_by_name,
        update_campaign_overview,
    )
    from chronicler.db.repository import upsert_character

    cache = client.app.state.engine_cache
    camp = get_campaign_by_name("test-campaign", registry=cache.registry_path)
    assert camp is not None
    update_campaign_overview(
        camp.id,
        current_player_character_id=36892,
        registry=cache.registry_path,
    )
    factory = cache.factory_for(camp)
    with session_scope(factory) as s:
        # Wire family_data into the player's snapshot json so the
        # walker can find children.
        from chronicler.db.repository import get_character

        player = get_character(s, 36892)
        assert player is not None
        player.save_snapshot_json = _json.dumps(
            {"family_data": {"child": [70001, 70002, 70003, 70004]}}
        )
        # 70001 living + untracked (should appear)
        upsert_character(s, ck3_id=70001, first_name="Living1", birth_date="1080.1.1")
        # 70002 living + already tracked (skip)
        upsert_character(s, ck3_id=70002, first_name="Tracked", birth_date="1081.1.1")
        # 70003 dead (skip)
        upsert_character(
            s,
            ck3_id=70003,
            first_name="Dead",
            birth_date="1082.1.1",
            death_date="1099.5.1",
        )
        # 70004 living + untracked (should appear)
        upsert_character(s, ck3_id=70004, first_name="Living2", birth_date="1083.1.1")
        s.commit()

    add_tracked_character(camp.id, 70002, registry=cache.registry_path)

    resp = client.get("/api/campaigns/test-campaign/tracked/suggested-candidates")
    assert resp.status_code == 200
    body = resp.json()
    ids = sorted(r["ck3_id"] for r in body)
    assert ids == [70001, 70004]
    # Relation hint preserved.
    assert all(r["relation"] == "child" for r in body)
