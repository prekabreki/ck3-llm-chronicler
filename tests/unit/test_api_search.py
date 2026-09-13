"""HTTP-level tests for the chronicler character-search API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.db.repository import (
    upsert_character,
)

# --- ck3_chronicler-4i33: name-search filter on /characters ---


def _seed_characters_for_search(s: Session) -> None:
    """Seed several characters whose names exercise the search ranking
    (exact / prefix / substring / dynasty / nickname). Campaign name is
    ``search-camp``."""
    upsert_character(s, ck3_id=1, first_name="Alfred", relevance=80)
    upsert_character(s, ck3_id=2, first_name="Alfredo", relevance=20)
    upsert_character(s, ck3_id=3, first_name="Walfred", relevance=50)
    upsert_character(s, ck3_id=4, first_name="Egbert", relevance=10)
    upsert_character(s, ck3_id=5, first_name="Cnut")
    from chronicler.db.models import Character

    # Stamp dynasty/nickname directly so we can also test those paths.
    s.query(Character).filter_by(ck3_id=5).one().dynasty_name = "Alfredsson"
    s.query(Character).filter_by(ck3_id=4).one().nickname = "Alfred-the-Great"


@pytest.fixture
def search_client(make_campaign: Callable[..., TestClient]) -> TestClient:
    """A ``search-camp`` campaign seeded for the /characters?q= ranking tests."""
    return make_campaign("search-camp", seed=_seed_characters_for_search)


def test_search_characters_ranks_exact_then_prefix_then_substring(
    search_client: TestClient,
) -> None:
    resp = search_client.get("/api/campaigns/search-camp/characters?q=Alfred")
    assert resp.status_code == 200
    rows = resp.json()
    # All five match somehow (Alfred exact, Alfredo prefix, Walfred
    # substring, Egbert via nickname "Alfred-the-Great", Cnut via
    # dynasty "Alfredsson"). Order: exact, prefix, substring, dynasty,
    # nickname.
    names = [r["first_name"] for r in rows]
    assert names == ["Alfred", "Alfredo", "Walfred", "Cnut", "Egbert"]


def test_search_characters_case_insensitive(search_client: TestClient) -> None:
    resp = search_client.get("/api/campaigns/search-camp/characters?q=alfred")
    body = resp.json()
    assert body[0]["first_name"] == "Alfred"


def test_search_characters_whitespace_only_q_returns_full_list(
    search_client: TestClient,
) -> None:
    """A query that's only whitespace shouldn't filter — same as no q."""
    resp = search_client.get("/api/campaigns/search-camp/characters?q=%20%20")
    assert len(resp.json()) == 5


def test_search_characters_ties_break_on_relevance(
    search_client: TestClient,
) -> None:
    """Two prefix matches: the higher-relevance character wins the tie."""
    resp = search_client.get("/api/campaigns/search-camp/characters?q=Alf")
    rows = resp.json()
    # Two prefix matches (Alfred + Alfredo) tie on rank=1; relevance
    # breaks the tie so Alfred (80) lands above Alfredo (20). Walfred
    # has "Alf" only as a substring — rank=2 — so it sorts after the
    # prefix bucket regardless of its higher relevance vs Alfredo.
    names = [r["first_name"] for r in rows[:3]]
    assert names == ["Alfred", "Alfredo", "Walfred"]


def test_search_characters_no_match_returns_empty(
    search_client: TestClient,
) -> None:
    resp = search_client.get("/api/campaigns/search-camp/characters?q=ZZZZZZ")
    assert resp.json() == []


# --- ck3_chronicler-5oyz: ?ids= batched lookup ---


def test_list_characters_ids_returns_matching_summaries_in_order(
    search_client: TestClient,
) -> None:
    """ck3_chronicler-5oyz: ?ids= bypasses the relevance window so the
    Codex tracked rail can render tracked characters even when they're
    not in the top-N relevance ranking. Response order matches the
    request order so the FE can preserve a tracked-bumped_at-style
    ordering applied client-side."""
    resp = search_client.get("/api/campaigns/search-camp/characters?ids=4,1,3")
    assert resp.status_code == 200
    rows = resp.json()
    # Same order as requested (4, 1, 3).
    assert [r["ck3_id"] for r in rows] == [4, 1, 3]
    assert [r["first_name"] for r in rows] == ["Egbert", "Alfred", "Walfred"]


def test_list_characters_ids_drops_unknown_silently(
    search_client: TestClient,
) -> None:
    """Unknown ids are dropped without 404 — the FE may have a stale
    tracked-set cache or a character may have been pruned."""
    resp = search_client.get("/api/campaigns/search-camp/characters?ids=1,9999,2")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["ck3_id"] for r in rows] == [1, 2]


def test_list_characters_ids_empty_returns_empty(
    search_client: TestClient,
) -> None:
    """Empty ids string returns [] without a DB hit. Guards against the
    FE accidentally serialising 0 tracked-characters into ?ids="""
    resp = search_client.get("/api/campaigns/search-camp/characters?ids=")
    assert resp.json() == []


def test_list_characters_ids_validates_integer_format(
    search_client: TestClient,
) -> None:
    """Non-integer ids → 400 (instead of letting the int() exception
    bubble through as a 500)."""
    resp = search_client.get("/api/campaigns/search-camp/characters?ids=1,banana")
    assert resp.status_code == 400


def test_list_characters_ids_overrides_q_and_limit(
    search_client: TestClient,
) -> None:
    """When ids is set, q / limit / offset are ignored — the route
    returns the requested ids regardless of what q would otherwise
    surface."""
    resp = search_client.get("/api/campaigns/search-camp/characters?ids=4&q=Alfred&limit=1")
    rows = resp.json()
    # Egbert (4) doesn't match q=Alfred but ids takes precedence.
    assert [r["ck3_id"] for r in rows] == [4]
