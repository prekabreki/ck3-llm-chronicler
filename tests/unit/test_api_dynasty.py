"""HTTP-level tests for the chronicler dynasty API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.db import (
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.repository import (
    insert_biography,
    upsert_character,
)
from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-thpz.1: GET /campaigns/{name}/dynasty ---


def _register_dynasty_campaign(
    api: CampaignHarness,
    *,
    player_id: int | None = 100,
    closing: str | None = None,
) -> None:
    """Register a ``wessex`` campaign with three Wessex characters across
    two generations, one biography, an optional pinned player, and an
    optional closing-chronicle blurb."""

    def _seed(s: Session) -> None:
        upsert_character(
            s,
            ck3_id=99,
            first_name="Egbert",
            birth_date="978.1.1",
            death_date="1010.5.4",
            relevance="player",
        )
        from chronicler.db.models import Character as _C

        s.query(_C).filter_by(ck3_id=99).one().dynasty_name = "Wessex"
        s.query(_C).filter_by(ck3_id=99).one().coa_json = json.dumps(
            {"pattern": "egbert.dds", "color1": "blue"}
        )
        upsert_character(
            s,
            ck3_id=100,
            first_name="Alfred",
            birth_date="1010.1.1",
            death_date=None,
            relevance="player",
        )
        s.query(_C).filter_by(ck3_id=100).one().dynasty_name = "Wessex"
        s.query(_C).filter_by(ck3_id=100).one().nickname = "the Great"
        s.query(_C).filter_by(ck3_id=100).one().coa_json = json.dumps(
            {"pattern": "alfred.dds", "color1": "red"}
        )
        upsert_character(
            s,
            ck3_id=101,
            first_name="Edward",
            birth_date="1040.6.6",
            death_date="1085.2.3",
            relevance="heir",
        )
        s.query(_C).filter_by(ck3_id=101).one().dynasty_name = "Wessex"
        s.query(_C).filter_by(ck3_id=101).one().coa_json = json.dumps({"pattern": "edward.dds"})
        # A non-Wessex character to confirm filtering by dynasty_name.
        upsert_character(s, ck3_id=200, first_name="Cnut", relevance="rival")
        s.query(_C).filter_by(ck3_id=200).one().dynasty_name = "Knytlinga"

        insert_biography(
            s,
            character_id=99,
            body=(
                "Egbert was crowned in 962 and held the throne for "
                "nearly five decades. "
                * 2
                + "His reign laid the foundations of the Wessex dynasty."
            ),
            prompt_template_version="biography_v1",
            provider="ollama:qwen3:14b",
            generated_at="2026-04-15T10:00:00+00:00",
            events_through_event_id=None,
        )

    overview = {"current_player_character_id": player_id} if player_id is not None else None
    campaign = api.campaign("wessex", seed=_seed, overview=overview)
    if closing is not None:
        from chronicler.db.registry import set_campaign_closing_chronicle

        set_campaign_closing_chronicle(
            campaign.id,
            closing,
            generated_at="2026-05-06T12:00:00+00:00",
            registry=api.registry,
        )


def test_dynasty_returns_player_dynasty_with_members_ordered_by_birth(
    api: CampaignHarness,
) -> None:
    _register_dynasty_campaign(api)
    c = api.client()
    resp = c.get("/api/campaigns/wessex/dynasty")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dynasty_name"] == "Wessex"
    assert body["member_count"] == 3
    # Ordered by birth date ascending: Egbert (978) → Alfred (1010) → Edward (1040).
    names = [m["first_name"] for m in body["members"]]
    assert names == ["Egbert", "Alfred", "Edward"]
    # Cnut (different dynasty) must not leak in.
    assert all(m["dynasty_name"] == "Wessex" for m in body["members"])


def test_dynasty_marks_player_as_current_head(api: CampaignHarness) -> None:
    _register_dynasty_campaign(api, player_id=100)
    c = api.client()
    resp = c.get("/api/campaigns/wessex/dynasty")
    body = resp.json()
    assert body["current_head"] is not None
    assert body["current_head"]["first_name"] == "Alfred"
    assert body["current_head"]["is_player"] is True
    # Members list also reflects the player flag.
    flags = {m["first_name"]: m["is_player"] for m in body["members"]}
    assert flags == {"Egbert": False, "Alfred": True, "Edward": False}


def test_dynasty_falls_back_when_no_player_set(api: CampaignHarness) -> None:
    """current_player_character_id can be null on adopted-but-not-yet-
    resolved campaigns. Pick the highest-relevance character with a
    dynasty rather than 404."""
    _register_dynasty_campaign(api, player_id=None)
    c = api.client()
    resp = c.get("/api/campaigns/wessex/dynasty")
    body = resp.json()
    assert body["dynasty_name"] == "Wessex"
    assert body["current_head"] is None  # no player resolved


def test_dynasty_404_when_player_pinned_but_dynasty_name_null(
    api: CampaignHarness,
) -> None:
    """ck3_chronicler-za6f: if the campaign has a pinned player but
    save-tail hasn't yet populated player.dynasty_name through the
    dynasty_house_id chain, the route used to fall back to the most-
    populated dynasty in the DB — which on the v09-smoke-class case
    rendered ``japanese_fujiwara`` for a House of Barcelona player.
    Now we 404 so the UI can render an 'unresolved' empty state
    instead of a confidently-wrong header.
    """
    from chronicler.db.models import Character as _C

    _register_dynasty_campaign(api, player_id=100)
    # Wipe Alfred's dynasty_name to recreate the smoke-found state.
    campaign_db = api.data_dir / "campaigns" / "wessex.db"
    engine = make_engine_for_path(campaign_db)
    factory = make_session_factory(engine)
    with factory() as s:
        s.query(_C).filter_by(ck3_id=100).one().dynasty_name = None
        s.commit()
    engine.dispose()

    c = api.client()
    resp = c.get("/api/campaigns/wessex/dynasty")
    assert resp.status_code == 404, resp.text
    assert "no dynasty" in resp.json()["detail"].lower()


def test_dynasty_parses_coa_json_per_member(api: CampaignHarness) -> None:
    """Each member's coa_json string is parsed inline so the lineage
    strip can render per-character arms without a follow-up fetch."""
    _register_dynasty_campaign(api)
    c = api.client()
    body = c.get("/api/campaigns/wessex/dynasty").json()
    by_name = {m["first_name"]: m for m in body["members"]}
    assert by_name["Egbert"]["coa_json"] == {
        "pattern": "egbert.dds",
        "color1": "blue",
    }
    assert by_name["Alfred"]["coa_json"]["pattern"] == "alfred.dds"


def test_dynasty_vita_roll_orders_by_death_desc(api: CampaignHarness) -> None:
    """Vita roll surfaces members with biographies, ordered by death
    date descending so the most-recent passing leads."""
    from chronicler.db.repository import insert_biography

    _register_dynasty_campaign(api)
    # Add a second biography so the roll has two entries to order.
    campaign_db = api.data_dir / "campaigns" / "wessex.db"
    engine = make_engine_for_path(campaign_db)
    factory = make_session_factory(engine)
    with factory() as s:
        insert_biography(
            s,
            character_id=101,
            body="Edward inherited the realm in 1010 and held it for 75 years.",
            prompt_template_version="biography_v1",
            provider="ollama:qwen3:14b",
            generated_at="2026-04-20T10:00:00+00:00",
            events_through_event_id=None,
        )
        s.commit()
    engine.dispose()

    c = api.client()
    body = c.get("/api/campaigns/wessex/dynasty").json()
    roll = body["vita_roll"]
    # Edward died 1085, Egbert died 1010 — Edward first.
    assert [v["first_name"] for v in roll] == ["Edward", "Egbert"]
    assert roll[0]["biography_excerpt"].startswith("Edward inherited")


def test_latest_biography_by_character_picks_highest_version_regardless_of_order() -> None:
    """ck3_chronicler-27ov.25 (audit M-A3): biographies_for_characters is
    unsorted, so the vita roll must pick the highest VERSION, not the last row.
    The old `version > 0` was always true (versions start at 1) → last-row-wins,
    correct only by accident of SQLite's rowid ordering. Feed rows
    highest-version-first to prove version comparison, not row order."""
    from chronicler.api.routes.dynasty import _latest_biography_by_character

    rows = [
        (101, "v3 body", "2026-04-22T00:00:00+00:00", 3),
        (101, "v1 body", "2026-04-20T00:00:00+00:00", 1),
        (101, "v2 body", "2026-04-21T00:00:00+00:00", 2),
        (202, "only body", "2026-04-20T00:00:00+00:00", 1),
    ]
    result = _latest_biography_by_character(rows)
    assert result[101] == ("v3 body", "2026-04-22T00:00:00+00:00")
    assert result[202] == ("only body", "2026-04-20T00:00:00+00:00")


def test_dynasty_founding_paragraph_is_first_paragraph_only(
    api: CampaignHarness,
) -> None:
    """ck3_chronicler-27ov.46 (audit M-A4): DynastyResponse.founding_paragraph
    is rendered as a single hero <p>, but the route shipped the entire 1-2 page
    closing chronicle. It must be just the first paragraph."""
    chronicle = (
        "The house of Wessex began with Egbert the Steadfast.\n\n"
        "Then came Alfred, who held the realm for fifty years.\n\n"
        "And the line endured beyond living memory."
    )
    _register_dynasty_campaign(api, closing=chronicle)
    c = api.client()
    body = c.get("/api/campaigns/wessex/dynasty").json()
    assert body["founding_paragraph"] == ("The house of Wessex began with Egbert the Steadfast.")
    assert "Alfred" not in body["founding_paragraph"]


def test_dynasty_returns_404_when_no_dynasty_resolves(
    make_campaign: Callable[..., TestClient],
) -> None:
    """Empty campaign (no characters with dynasty_name) ⇒ 404 so the UI
    can render its "no save adopted yet" branch."""
    c = make_campaign("empty")
    resp = c.get("/api/campaigns/empty/dynasty")
    assert resp.status_code == 404


def test_dynasty_carries_closing_chronicle_blurb_when_present(
    api: CampaignHarness,
) -> None:
    blurb = "And so the Wessex line endured, by sword and by faith."
    _register_dynasty_campaign(api, closing=blurb)
    c = api.client()
    body = c.get("/api/campaigns/wessex/dynasty").json()
    assert body["founding_paragraph"] == blurb
