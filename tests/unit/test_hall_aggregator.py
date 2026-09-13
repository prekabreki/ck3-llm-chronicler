"""Tests for the cross-DB Hall of Fame aggregator (ck3_chronicler-467k).

Covers the aggregator function directly + the /api/hall-of-fame route
end-to-end. Each test seeds a fresh registry + per-campaign DBs under
tmp_path so the aggregator's grouping by ``(playthrough_id,
dynasty_name)`` can be exercised across realistic shapes (multiple
campaigns under one playthrough, archived siblings, missing
playthrough_id).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicler.api import create_app
from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.hall import aggregate_hall_of_fame
from chronicler.db.registry import (
    add_tracked_character,
    archive_campaign,
    create_campaign,
    set_campaign_closing_chronicle,
    update_campaign_overview,
)
from chronicler.db.repository import insert_biography, upsert_character


def _make_campaign_db(tmp_path: Path, name: str) -> Path:
    """Create an empty per-campaign DB at ``<tmp>/campaigns/<name>.db``."""
    db = tmp_path / "campaigns" / f"{name}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine_for_path(db)
    Base.metadata.create_all(engine)
    engine.dispose()
    return db


def _seed_character_and_bio(
    db: Path,
    *,
    ck3_id: int,
    first_name: str,
    coa_json: str | None = None,
    biography_count: int = 1,
) -> None:
    engine = make_engine_for_path(db)
    factory = make_session_factory(engine)
    with factory() as s:
        upsert_character(
            s,
            ck3_id=ck3_id,
            first_name=first_name,
            coa_json=coa_json,
        )
        for i in range(biography_count):
            insert_biography(
                s,
                character_id=ck3_id,
                body=f"Vita {i}",
                prompt_template_version="biography_v1",
                provider="test:fake",
                generated_at=f"2026-05-{(i % 28) + 1:02d}T00:00:00+00:00",
                events_through_event_id=None,
            )
        s.commit()
    engine.dispose()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    return tmp_path


def test_aggregate_groups_two_campaigns_under_one_dynasty(env: Path) -> None:
    """Two campaigns sharing playthrough_id + founding_dynasty_name
    collapse to one rollup with campaigns_count=2 and summed counts."""
    registry = env / "registry.db"
    db_a = _make_campaign_db(env, "a")
    db_b = _make_campaign_db(env, "b")
    _seed_character_and_bio(db_a, ck3_id=100, first_name="Erik", biography_count=2)
    _seed_character_and_bio(db_b, ck3_id=101, first_name="Harald", biography_count=1)

    a = create_campaign(
        "Erik 1066",
        db_path=str(db_a),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    b = create_campaign(
        "Erik 1080",
        db_path=str(db_b),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    update_campaign_overview(
        a.id, bookmark_date="1066.9.15", current_in_game_date="1080.1.1", registry=registry
    )
    update_campaign_overview(
        b.id, bookmark_date="1080.1.1", current_in_game_date="1100.6.18", registry=registry
    )
    add_tracked_character(a.id, 100, registry=registry)
    add_tracked_character(b.id, 101, registry=registry)
    add_tracked_character(b.id, 102, registry=registry)  # 2 tracked in b

    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 1
    r = rollups[0]
    assert r.dynasty_name == "Munsö"
    assert r.playthrough_id == "pt-1"
    assert r.campaigns_count == 2
    assert r.tracked_count == 3  # 1 + 2
    assert r.biographies_count == 3  # 2 + 1
    assert r.span_label == "1066 — 1100"
    assert r.span_end_label == "1100.6.18"
    assert r.span_days is not None and r.span_days > 0
    assert r.is_active is True
    assert r.sealed_at_label is None
    assert r.id == "pt-1|Munsö"


def test_aggregate_keeps_distinct_playthroughs_separate(env: Path) -> None:
    """Same dynasty_name but different playthrough_id remains two rollups
    (the brief's identity-reconciliation default — merge is opt-in)."""
    registry = env / "registry.db"
    db_a = _make_campaign_db(env, "a")
    db_b = _make_campaign_db(env, "b")
    create_campaign(
        "Munsö round 1",
        db_path=str(db_a),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    create_campaign(
        "Munsö round 2",
        db_path=str(db_b),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-2",
        registry=registry,
    )
    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 2
    assert {r.playthrough_id for r in rollups} == {"pt-1", "pt-2"}
    assert all(r.dynasty_name == "Munsö" for r in rollups)


def test_aggregate_uses_current_house_name_when_no_founding_set(env: Path) -> None:
    """Dynasty fallback chain: founding_dynasty_name → current_house_name
    → 'Unknown dynasty'. A campaign with neither shouldn't crash."""
    registry = env / "registry.db"
    db = _make_campaign_db(env, "a")
    c = create_campaign(
        "Mystery",
        db_path=str(db),
        ck3_playthrough_id="pt-x",
        registry=registry,
    )
    update_campaign_overview(c.id, current_house_name="Hauteville", registry=registry)
    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 1
    assert rollups[0].dynasty_name == "Hauteville"


def test_aggregate_marks_archived_when_all_campaigns_sealed(env: Path) -> None:
    """is_active flips to False only when every campaign in the group is
    archived. Sealed campaigns also surface a blurb + sealed_at_label."""
    registry = env / "registry.db"
    db = _make_campaign_db(env, "a")
    _seed_character_and_bio(db, ck3_id=100, first_name="Erik")
    c = create_campaign(
        "Erik 1066",
        db_path=str(db),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    set_campaign_closing_chronicle(
        c.id,
        "First paragraph of the chronicle.\n\nSecond paragraph that should not appear.",
        generated_at="2026-04-30T18:00:00+00:00",
        registry=registry,
    )
    archive_campaign(c.id, registry=registry)

    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 1
    r = rollups[0]
    assert r.is_active is False
    assert r.blurb == "First paragraph of the chronicle."
    assert r.sealed_at_label == "2026-04-30"


def test_aggregate_active_rollup_has_no_seal_metadata(env: Path) -> None:
    """A non-archived campaign produces an active rollup with blurb=None
    and sealed_at_label=None — the FE renders the "(chronicle in
    progress)" placeholder for that case."""
    registry = env / "registry.db"
    db = _make_campaign_db(env, "a")
    _seed_character_and_bio(db, ck3_id=100, first_name="Erik")
    create_campaign(
        "Erik 1066",
        db_path=str(db),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 1
    assert rollups[0].is_active is True
    assert rollups[0].blurb is None
    assert rollups[0].sealed_at_label is None


def test_aggregate_pulls_player_coa_from_most_recent_campaign(env: Path) -> None:
    """The Hall card shield reads the player's coa_json from the
    most-recent campaign in the rollup. Older campaigns' coa_json is
    ignored once the most-recent has one."""
    registry = env / "registry.db"
    db_a = _make_campaign_db(env, "a")
    db_b = _make_campaign_db(env, "b")
    _seed_character_and_bio(
        db_a,
        ck3_id=100,
        first_name="Erik",
        coa_json=json.dumps({"pattern": "old"}),
    )
    _seed_character_and_bio(
        db_b,
        ck3_id=101,
        first_name="Harald",
        coa_json=json.dumps({"pattern": "current"}),
    )
    a = create_campaign(
        "old",
        db_path=str(db_a),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    b = create_campaign(
        "current",
        db_path=str(db_b),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    # Pin player IDs so the coa lookup has a target on each campaign.
    update_campaign_overview(a.id, current_player_character_id=100, registry=registry)
    update_campaign_overview(b.id, current_player_character_id=101, registry=registry)
    # Mark b as more recent via last_event_at (touch_last_event_at would
    # also work but we set it explicitly here for determinism).
    update_campaign_overview(b.id, current_in_game_date="1100.1.1", registry=registry)

    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 1
    coa_json = rollups[0].coa_json
    assert coa_json is not None
    parsed = json.loads(coa_json)
    assert parsed == {"pattern": "current"}, "expected the most-recent campaign's CoA"


def test_aggregate_returns_empty_when_no_campaigns(env: Path) -> None:
    """Bare registry → empty rollup list (the FE renders the empty-state
    UI on len(rollups) == 0)."""
    registry = env / "registry.db"
    rollups = aggregate_hall_of_fame(registry=registry)
    assert rollups == []


def test_aggregate_unknown_dynasty_when_all_metadata_missing(env: Path) -> None:
    """A campaign with neither founding_dynasty_name nor current_house_name
    falls through to the 'Unknown dynasty' bucket so the renderer never
    sees a missing string."""
    registry = env / "registry.db"
    db = _make_campaign_db(env, "a")
    create_campaign(
        "Mystery",
        db_path=str(db),
        ck3_playthrough_id="pt-?",
        registry=registry,
    )
    rollups = aggregate_hall_of_fame(registry=registry)
    assert len(rollups) == 1
    assert rollups[0].dynasty_name == "Unknown dynasty"


@pytest.fixture
def hall_client(env: Path) -> Iterator[TestClient]:
    registry = env / "registry.db"
    db = _make_campaign_db(env, "a")
    _seed_character_and_bio(db, ck3_id=100, first_name="Erik")
    c = create_campaign(
        "Erik 1066",
        db_path=str(db),
        founding_dynasty_name="Munsö",
        ck3_playthrough_id="pt-1",
        registry=registry,
    )
    update_campaign_overview(
        c.id,
        bookmark_date="1066.9.15",
        current_in_game_date="1080.1.1",
        current_player_character_id=100,
        registry=registry,
    )
    add_tracked_character(c.id, 100, registry=registry)

    app = create_app(registry_path=registry)
    with TestClient(app) as client:
        yield client


def test_hall_of_fame_endpoint_returns_rollup(hall_client: TestClient) -> None:
    """End-to-end: /api/hall-of-fame returns the aggregator output as a
    HallOfFameResponse with one rollup matching the seeded campaign."""
    resp = hall_client.get("/api/hall-of-fame")
    assert resp.status_code == 200
    body = resp.json()
    assert "rollups" in body
    assert len(body["rollups"]) == 1
    r = body["rollups"][0]
    assert r["dynasty_name"] == "Munsö"
    assert r["playthrough_id"] == "pt-1"
    assert r["campaigns_count"] == 1
    assert r["tracked_count"] == 1
    assert r["biographies_count"] == 1
    assert r["primary_campaign_name"] == "Erik 1066"
    assert r["is_active"] is True
    assert r["span_label"] == "1066 — 1080"


def test_hall_of_fame_endpoint_empty_registry(env: Path) -> None:
    """Empty registry → empty rollups array, status 200."""
    registry = env / "registry.db"
    app = create_app(registry_path=registry)
    with TestClient(app) as c:
        resp = c.get("/api/hall-of-fame")
    assert resp.status_code == 200
    assert resp.json() == {"rollups": []}
