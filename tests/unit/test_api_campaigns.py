"""HTTP-level tests for the chronicler campaigns API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from chronicler.db import (
    make_engine_for_path,
)
from chronicler.db.repository import (
    insert_biography,
    upsert_character,
)
from tests.helpers.api import CampaignHarness


def test_list_campaigns_returns_one(client: TestClient) -> None:
    resp = client.get("/api/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "test-campaign"


def test_get_campaign_by_name(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign")
    assert resp.status_code == 200
    assert resp.json()["name"] == "test-campaign"


def test_get_unknown_campaign_404(client: TestClient) -> None:
    resp = client.get("/api/campaigns/nope")
    assert resp.status_code == 404


# --- ck3_chronicler-75c: campaign counts aggregation ---


def test_campaign_response_has_no_counts_by_default(client: TestClient) -> None:
    """Default list/get omits counts (avoids the per-campaign DB open)."""
    resp = client.get("/api/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["counts"] is None


def test_campaign_list_with_include_counts(client: TestClient) -> None:
    """The fixture has 1 character, 1 biography."""
    resp = client.get("/api/campaigns?include_counts=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["counts"] == {"characters": 1, "biographies": 1}


def test_campaign_get_with_include_counts(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign?include_counts=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["counts"] == {"characters": 1, "biographies": 1}


def test_campaign_response_has_closing_chronicle_blurb_field(
    client: TestClient,
) -> None:
    """6e1: CampaignResponse always carries closing_chronicle_blurb,
    None when no closing chronicle has been generated."""
    resp = client.get("/api/campaigns/test-campaign")
    assert resp.status_code == 200
    data = resp.json()
    assert "closing_chronicle_blurb" in data
    assert data["closing_chronicle_blurb"] is None


def test_campaign_list_includes_archived_when_requested(
    make_campaign: Callable[..., TestClient],
) -> None:
    """6e1: include_archived=true is required to surface sealed
    campaigns; the default omits them so the list endpoint stays cheap.
    Verifies the Library's Completed shelf can populate via this flag."""
    c = make_campaign("sealed", archived=True)
    # Default omits archived
    resp_default = c.get("/api/campaigns")
    assert resp_default.status_code == 200
    assert resp_default.json() == []

    # include_archived=true surfaces them
    resp_all = c.get("/api/campaigns?include_archived=true")
    assert resp_all.status_code == 200
    data = resp_all.json()
    assert len(data) == 1
    assert data[0]["name"] == "sealed"
    assert data[0]["archived"] is True


def test_archived_campaign_read_endpoints_reachable_after_seal(
    make_campaign: Callable[..., TestClient],
) -> None:
    """audit F-03 / ck3_chronicler-1p4t: GET endpoints that the Library's
    Completed shelf links into must remain reachable after a campaign is
    sealed. Before the fix, every read 404'd because the dependency
    chain hard-coded include_archived=False.

    Covers GET /campaigns/{name}, /characters, /tracked, /cost-summary,
    /dynasty, /family-tree."""
    c = make_campaign("sealed", archived=True)
    # Single-campaign GET returns the sealed row (was: 404).
    r = c.get("/api/campaigns/sealed")
    assert r.status_code == 200
    assert r.json()["archived"] is True

    # All listing-style children reach the empty body rather than 404.
    for path in (
        "/api/campaigns/sealed/characters",
        "/api/campaigns/sealed/tracked",
        "/api/campaigns/sealed/cost-summary",
    ):
        assert c.get(path).status_code == 200, path

    # Detail-style endpoints whose 404 should mean "no such row in
    # this DB" still 404 — but specifically with character-not-found
    # rather than campaign-not-found, proving the campaign dep
    # resolved.
    r = c.get("/api/campaigns/sealed/characters/999/family-tree")
    assert r.status_code == 404
    assert "character 999" in r.json()["detail"]

    # Dynasty endpoint 404s on empty-DB ("no characters with a
    # dynasty_name set"), not on missing campaign.
    r = c.get("/api/campaigns/sealed/dynasty")
    assert r.status_code == 404
    assert "campaign not found" not in r.json()["detail"]


# --- ck3_chronicler-a3f: real heraldry on Library cards ---


def test_campaign_response_surfaces_player_coa_when_include_counts(
    api: CampaignHarness,
) -> None:
    """ck3_chronicler-a3f: when include_counts=true and the campaign's
    current_player_character_id resolves to a Character row with
    coa_json populated, the response carries the parsed CoA dict."""
    coa_dict = {
        "pattern": "pattern_solid.dds",
        "color1": "black",
        "color2": "green",
        "color3": "yellow",
    }
    api.campaign(
        "Erik 1066-9-15",
        seed=lambda s: upsert_character(
            s, ck3_id=32943, first_name="Erik", coa_json=json.dumps(coa_dict)
        ),
        overview={"current_player_character_id": 32943},
    )
    c = api.client()
    # Default GET (no include_counts) does not surface coa.
    bare = c.get("/api/campaigns/Erik%201066-9-15").json()
    assert bare["current_player_coa_json"] is None
    # include_counts=true triggers the per-campaign session open
    # which is when we piggyback the coa fetch.
    full = c.get("/api/campaigns/Erik%201066-9-15?include_counts=true").json()
    assert full["current_player_coa_json"] == coa_dict


def test_campaign_response_coa_is_null_when_player_id_unset(
    make_campaign: Callable[..., TestClient],
) -> None:
    """ck3_chronicler-a3f: pre-cqo / pristine campaigns lack a
    current_player_character_id — coa stays null even with
    include_counts=true."""
    c = make_campaign("Pristine")
    resp = c.get("/api/campaigns/Pristine?include_counts=true").json()
    assert resp["current_player_coa_json"] is None
    # Counts still populated — the no-coa path doesn't hide them.
    assert resp["counts"] is not None


def test_campaign_response_coa_is_null_when_character_row_missing(
    make_campaign: Callable[..., TestClient],
) -> None:
    """ck3_chronicler-a3f: the campaign has a player id but the
    per-campaign DB doesn't have that character yet (early state) —
    fall through to null, no error."""
    c = make_campaign("Early", overview={"current_player_character_id": 99999})
    resp = c.get("/api/campaigns/Early?include_counts=true").json()
    assert resp["current_player_coa_json"] is None


def test_campaign_response_coa_is_null_on_malformed_json(
    make_campaign: Callable[..., TestClient],
) -> None:
    """ck3_chronicler-a3f: defence in depth — a malformed coa_json
    string degrades to null rather than 500ing the list endpoint."""
    c = make_campaign(
        "Broken",
        seed=lambda s: upsert_character(s, ck3_id=42, first_name="Bork", coa_json="{not json"),
        overview={"current_player_character_id": 42},
    )
    resp = c.get("/api/campaigns/Broken?include_counts=true").json()
    assert resp["current_player_coa_json"] is None


# --- ck3_chronicler-bges: last-tick fields on CampaignResponse ---


def test_campaigns_list_returns_last_tick_fields(make_campaign: Callable[..., TestClient]) -> None:
    """ck3_chronicler-bges: /api/campaigns surfaces the persisted
    last-tick fields so the Library card can render the per-card
    activity line on cold load."""
    c = make_campaign(
        "LastTick",
        last_tick={
            "save_filename": "autosave.ck3",
            "ingested_at": "2026-05-10T12:00:00+00:00",
            "in_game_date": "1066.4.11",
            "event_count": 3,
            "event_type_tally": {"marriage": 1, "birth": 2},
        },
    )
    resp = c.get("/api/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    target = next(d for d in data if d["name"] == "LastTick")
    assert target["last_save_filename"] == "autosave.ck3"
    assert target["last_save_ingested_at"] == "2026-05-10T12:00:00+00:00"
    assert target["last_save_in_game_date"] == "1066.4.11"
    assert target["last_tick_event_count"] == 3
    assert target["last_tick_event_type_tally"] == {"marriage": 1, "birth": 2}


def test_campaigns_list_null_last_tick_when_never_ingested(
    make_campaign: Callable[..., TestClient],
) -> None:
    """Fresh campaign that has never been ingested: all 5 fields null."""
    c = make_campaign("FreshCampaign")
    resp = c.get("/api/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    target = next(d for d in data if d["name"] == "FreshCampaign")
    assert target["last_save_filename"] is None
    assert target["last_save_ingested_at"] is None
    assert target["last_save_in_game_date"] is None
    assert target["last_tick_event_count"] is None
    assert target["last_tick_event_type_tally"] is None


def test_campaigns_list_handles_corrupt_tally_json_defensively(
    api: CampaignHarness,
) -> None:
    """ck3_chronicler-bges: a corrupt last_tick_event_type_tally
    blob doesn't 500 the list endpoint — the tally surfaces as null."""
    import sqlite3

    camp = api.campaign(
        "CorruptTally",
        last_tick={
            "save_filename": "autosave.ck3",
            "ingested_at": "2026-05-10T12:00:00+00:00",
            "in_game_date": "1066.4.11",
            "event_count": 1,
            "event_type_tally": {"ok": 1},
        },
    )
    # Directly corrupt the tally column in the registry DB.
    with sqlite3.connect(str(api.registry)) as conn:
        conn.execute(
            "UPDATE campaigns SET last_tick_event_type_tally = ? WHERE id = ?",
            ("{not valid json", camp.id),
        )

    c = api.client()
    resp = c.get("/api/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    target = next(d for d in data if d["name"] == "CorruptTally")
    assert target["last_tick_event_type_tally"] is None
    # Other fields that were valid should still surface.
    assert target["last_save_filename"] == "autosave.ck3"
    assert target["last_tick_event_count"] == 1


def test_rename_campaign_endpoint_updates_name(make_campaign: Callable[..., TestClient]) -> None:
    """ck3_chronicler-bly: POST /api/campaigns/{name}/rename returns the
    renamed campaign with no warning; the new name is then addressable."""
    c = make_campaign("Erik 1066-9-15")
    resp = c.post(
        "/api/campaigns/Erik%201066-9-15/rename",
        json={"name": "Norse Smoke"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign"]["name"] == "Norse Smoke"
    assert body["warning"] is None

    # New name resolves; old name is 404.
    assert c.get("/api/campaigns/Norse%20Smoke").status_code == 200
    assert c.get("/api/campaigns/Erik%201066-9-15").status_code == 404


def test_rename_campaign_endpoint_warns_on_collision(api: CampaignHarness) -> None:
    """ck3_chronicler-bly: renaming to a name already used by another
    active campaign succeeds (registry allows it) but the response
    carries a warning so the UI can surface the clash."""
    api.campaign("Wessex")
    api.campaign("Erik")
    c = api.client()
    resp = c.post(
        "/api/campaigns/Erik/rename",
        json={"name": "Wessex"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign"]["name"] == "Wessex"
    assert body["warning"] is not None
    assert "Wessex" in body["warning"]


def test_rename_campaign_endpoint_rejects_empty_name(
    make_campaign: Callable[..., TestClient],
) -> None:
    """ck3_chronicler-bly: empty / whitespace-only name is a 400."""
    c = make_campaign("Erik")
    resp = c.post("/api/campaigns/Erik/rename", json={"name": "   "})
    assert resp.status_code == 400


def test_rename_campaign_endpoint_404_on_unknown(api: CampaignHarness) -> None:
    """ck3_chronicler-bly: unknown source campaign is 404 (via the
    get_campaign dependency)."""
    c = api.client()
    resp = c.post(
        "/api/campaigns/never-existed/rename",
        json={"name": "anything"},
    )
    assert resp.status_code == 404


def test_unarchive_campaign_endpoint_reactivates_sealed(
    make_campaign: Callable[..., TestClient],
) -> None:
    """ck3_chronicler-w2s: POST /api/campaigns/{name}/unarchive returns
    the freshly-active CampaignResponse and the campaign appears in the
    default (non-archived) list afterwards."""
    c = make_campaign("Norse Smoke", archived=True)
    # Sealed campaign is hidden from the default list
    assert c.get("/api/campaigns").json() == []
    # GET-by-name returns the sealed row (audit F-03 / ck3_chronicler-1p4t):
    # the Library "Completed" shelf renders archived cards via
    # ?include_archived=true and clicking through must work.
    sealed_resp = c.get("/api/campaigns/Norse%20Smoke")
    assert sealed_resp.status_code == 200
    assert sealed_resp.json()["archived"] is True

    # POST .../unarchive flips archived=0 and returns the row
    resp = c.post("/api/campaigns/Norse%20Smoke/unarchive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Norse Smoke"
    assert body["archived"] is False

    # Now visible in the default list and addressable by GET
    listing = c.get("/api/campaigns").json()
    assert [x["name"] for x in listing] == ["Norse Smoke"]
    assert c.get("/api/campaigns/Norse%20Smoke").status_code == 200


def test_unarchive_campaign_endpoint_404_on_unknown(api: CampaignHarness) -> None:
    """ck3_chronicler-w2s: 404 (not 200, not 500) when the name doesn't
    exist at all."""
    c = api.client()
    resp = c.post("/api/campaigns/never-existed/unarchive")
    assert resp.status_code == 404


def test_unarchive_campaign_endpoint_idempotent_on_active(
    make_campaign: Callable[..., TestClient],
) -> None:
    """ck3_chronicler-w2s: hitting unarchive on an already-active
    campaign returns 200 with the same row (no flip)."""
    c = make_campaign("active")
    resp = c.post("/api/campaigns/active/unarchive")
    assert resp.status_code == 200
    assert resp.json()["archived"] is False


# --- Issue #2: GET /campaigns/{name}/ingest-state ---


def test_ingest_state_404_unknown_campaign(client: TestClient) -> None:
    resp = client.get("/api/campaigns/never-existed/ingest-state")
    assert resp.status_code == 404


def test_ingest_state_zero_when_cache_dir_missing(
    make_campaign: Callable[..., TestClient],
) -> None:
    """Issue #2: a campaign that has never ingested has no cache dir. The
    honest answer is pending=0, not a 404 the strip would have to
    special-case — it is the exact case the resync exists to converge on."""
    c = make_campaign("never-ingested")
    resp = c.get("/api/campaigns/never-ingested/ingest-state")
    assert resp.status_code == 200
    assert resp.json() == {"pending": 0, "bytes": 0}


def test_ingest_state_counts_a_populated_cache(api: CampaignHarness) -> None:
    """Issue #2: the count and byte total come off the same disk scan the
    SSE cache_state frame uses, so the resync can never disagree with the
    stream about what 'pending' means."""
    camp = api.campaign("busy-cache")
    cache_dir = api.data_dir / "save-cache" / camp.id
    cache_dir.mkdir(parents=True)
    (cache_dir / "000000000001.ck3").write_bytes(b"aaaa")
    (cache_dir / "000000000002.ck3").write_bytes(b"bb")
    # A .tmp from an interrupted run and a non-conforming name are not
    # pending saves and must not be counted.
    (cache_dir / "000000000003.ck3.tmp").write_bytes(b"cccccc")
    (cache_dir / "notes.txt").write_bytes(b"x")

    resp = api.client().get("/api/campaigns/busy-cache/ingest-state")
    assert resp.status_code == 200
    assert resp.json() == {"pending": 2, "bytes": 6}

    # Side-effect free: the GET neither pruned the .tmp nor created anything.
    assert (cache_dir / "000000000003.ck3.tmp").is_file()


def test_ingest_state_matches_the_live_cache_snapshot(api: CampaignHarness) -> None:
    """Issue #2 anti-drift: the REST number must equal what SaveCache
    publishes over SSE for the same directory. If these ever diverge the
    resync would 'fix' the strip to a different wrong number."""
    from chronicler.save.cache import SaveCache

    camp = api.campaign("agreement")
    cache_dir = api.data_dir / "save-cache" / camp.id
    cache_dir.mkdir(parents=True)
    (cache_dir / "000000000007.ck3").write_bytes(b"seven!!")

    live = SaveCache(cache_dir).snapshot()
    rest = api.client().get("/api/campaigns/agreement/ingest-state").json()
    assert rest["pending"] == live["pending"]
    assert rest["bytes"] == live["bytes"]


def test_ingest_state_works_against_archived_campaign(
    make_campaign: Callable[..., TestClient],
) -> None:
    """Issue #2: an archived campaign's strip should read 0 rather than the
    client having to branch on archived-ness."""
    c = make_campaign("sealed-and-quiet")
    c.post("/api/campaigns/sealed-and-quiet/archive")
    resp = c.get("/api/campaigns/sealed-and-quiet/ingest-state")
    assert resp.status_code == 200
    assert resp.json()["pending"] == 0


# --- ck3_chronicler-0l06: POST /campaigns/{name}/refresh-snapshots ---


def test_refresh_snapshots_404_unknown_campaign(client: TestClient) -> None:
    """0l06: unknown campaign 404s, not 500."""
    resp = client.post("/api/campaigns/never-existed/refresh-snapshots")
    assert resp.status_code == 404


def test_refresh_snapshots_returns_zero_when_cache_dir_missing(
    make_campaign: Callable[..., TestClient],
) -> None:
    """0l06: campaigns whose save-cache GC'd at seal-time (the common
    'museum piece' case — Thrugot 1066-9-15 was the live example)
    return 200/refreshed=0 with a hint string, not 500. The route is
    the manual escape hatch; absence of cache is a documented dead-end."""
    # Deliberately do not create save-cache/<uuid>/.
    c = make_campaign("sealed-museum")
    resp = c.post("/api/campaigns/sealed-museum/refresh-snapshots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed"] == 0
    assert body["save_seqno"] is None
    assert body["save_date"] is None
    assert "no save-cache directory" in body["detail"].lower()


def test_refresh_snapshots_returns_zero_when_cache_empty(api: CampaignHarness) -> None:
    """0l06: cache directory exists but holds no .ck3 — same museum-
    piece outcome, but with a different hint pointing at the
    adopt-save path. Distinguished from missing-dir so the user can
    tell whether the cache GC'd vs was never populated."""
    camp = api.campaign("empty-cache")
    # Create the cache dir but leave it empty.
    (api.data_dir / "save-cache" / camp.id).mkdir(parents=True)

    c = api.client()
    resp = c.post("/api/campaigns/empty-cache/refresh-snapshots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed"] == 0
    assert body["save_seqno"] is None
    assert "save-cache directory empty" in body["detail"].lower()


def test_refresh_snapshots_works_against_archived_campaign(
    make_campaign: Callable[..., TestClient],
) -> None:
    """0l06 acceptance: archived campaigns are reachable. The whole
    motivation for this route is fixing snapshots on sealed campaigns
    after a backend update changed snapshot logic. Pre-fix
    (get_campaign), this 404'd. Post-fix (include_archived=True path),
    it executes — though with no cache it returns 0."""
    c = make_campaign("museum", archived=True)
    resp = c.post("/api/campaigns/museum/refresh-snapshots")
    assert resp.status_code == 200, resp.text
    # Archived but no cache → 0 refreshed (the museum case).
    assert resp.json()["refreshed"] == 0


def test_get_campaigns_returns_overview_fields_when_populated(
    api: CampaignHarness,
) -> None:
    """ck3_chronicler-cqo: GET /api/campaigns surfaces the seven new
    overview fields. Populated campaigns return concrete strings/ints;
    pristine campaigns return null for all seven."""
    api.campaign(
        "Erik 1066-9-15",
        create_kwargs={
            "ck3_playthrough_id": "uuid-cqo",
            "founding_dynasty_name": "Munso",
        },
        overview={
            "bookmark_date": "1066.9.15",
            "current_in_game_date": "1075.5.9",
            "current_player_character_id": 32943,
            "current_player_name": "Erik",
            "current_player_nickname": "the Heathen",
            "current_house_name": "house_munso",
        },
    )
    api.campaign("Pristine")

    client = api.client()
    resp = client.get("/api/campaigns")
    assert resp.status_code == 200
    data = {c["name"]: c for c in resp.json()}

    erik = data["Erik 1066-9-15"]
    assert erik["bookmark_date"] == "1066.9.15"
    assert erik["current_in_game_date"] == "1075.5.9"
    assert erik["current_player_character_id"] == 32943
    assert erik["current_player_name"] == "Erik"
    assert erik["current_player_nickname"] == "the Heathen"
    assert erik["current_house_name"] == "house_munso"
    assert erik["founding_dynasty_name"] == "Munso"

    fresh = data["Pristine"]
    assert fresh["bookmark_date"] is None
    assert fresh["current_in_game_date"] is None
    assert fresh["current_player_name"] is None
    assert fresh["current_house_name"] is None
    assert fresh["founding_dynasty_name"] is None


def test_campaign_counts_reflect_added_rows(client: TestClient) -> None:
    """Insert another character + biography and verify counts move."""
    from sqlalchemy.orm import sessionmaker

    db_path = client.get("/api/campaigns/test-campaign").json()["db_path"]
    engine = make_engine_for_path(Path(db_path))
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        upsert_character(s, ck3_id=42, first_name="Hild")
        insert_biography(
            s,
            character_id=42,
            body="Hild's vita.",
            prompt_template_version="biography_v1",
            provider="ollama:test",
            generated_at="2026-05-03T10:00:00+00:00",
        )
        s.commit()
    engine.dispose()

    resp = client.get("/api/campaigns/test-campaign?include_counts=true")
    assert resp.json()["counts"] == {"characters": 2, "biographies": 2}


# --- ck3_chronicler-ezpc: hard-delete a campaign ---


def test_delete_campaign_removes_registry_row_and_file(
    client: TestClient,
) -> None:
    """DELETE /api/campaigns/{name} returns 204; the campaign disappears
    from list_campaigns and the per-campaign DB file is unlinked."""
    db_path_str = client.get("/api/campaigns/test-campaign").json()["db_path"]
    db_path = Path(db_path_str)
    assert db_path.exists()

    resp = client.delete("/api/campaigns/test-campaign")
    assert resp.status_code == 204

    # No longer listed; per-campaign DB file gone.
    follow = client.get("/api/campaigns")
    assert follow.json() == []
    assert not db_path.exists()


def test_delete_unknown_campaign_returns_404(client: TestClient) -> None:
    resp = client.delete("/api/campaigns/no-such-thing")
    assert resp.status_code == 404


# --- ck3_chronicler-yv8q: reset save-tail baseline ---


def test_reset_baseline_deletes_baseline_file(client: TestClient) -> None:
    """yv8q: when a baseline file exists, DELETE removes it; deleted=True.

    The baseline path is derived from the campaign's db_path via
    baseline_path_for() — for the fixture campaign at
    ``<tmp>/campaigns/test.db``, the baseline lives at
    ``<tmp>/campaigns/test.baseline.json``.
    """
    from chronicler.save.baseline import baseline_path_for, save_baseline
    from chronicler.save.parse import SaveSnapshot

    db_path_str = client.get("/api/campaigns/test-campaign").json()["db_path"]
    baseline_path = baseline_path_for(Path(db_path_str))

    snap = SaveSnapshot(
        playthrough_id="yv8q-test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.2.1",
        player_character_id=None,
        characters={},
    )
    save_baseline(baseline_path, snap)
    assert baseline_path.exists()

    resp = client.delete("/api/campaigns/test-campaign/baseline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert body["path"] == str(baseline_path)
    assert body["message"] == "cleared"
    assert not baseline_path.exists()


def test_reset_baseline_idempotent_when_missing(client: TestClient) -> None:
    """yv8q: deleting an already-missing baseline returns deleted=False, 200.

    The route is the manual escape hatch for a wedged baseline — the
    user clicking the button against a campaign with no baseline file
    on disk should get a defined no-op outcome, not an error. Mirrors
    the rotate-debug-log pattern's "200 with descriptive message" branch.
    """
    from chronicler.save.baseline import baseline_path_for

    db_path_str = client.get("/api/campaigns/test-campaign").json()["db_path"]
    baseline_path = baseline_path_for(Path(db_path_str))
    assert not baseline_path.exists()

    resp = client.delete("/api/campaigns/test-campaign/baseline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is False
    assert body["path"] == str(baseline_path)
    assert body["message"] == "no baseline to clear"


def test_reset_baseline_unknown_campaign_returns_404(client: TestClient) -> None:
    """yv8q: 404 when the campaign name doesn't resolve."""
    resp = client.delete("/api/campaigns/never-existed/baseline")
    assert resp.status_code == 404
