"""Tests for search helpers + endpoint (ck3_chronicler-b2y).

Two passes per scope: one against a base schema (no FTS5 tables) which
exercises the LIKE fallback, and one against a schema with FTS5
shadows manually created (mirrors what migration ebc825c7d3ed sets
up in production)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from chronicler.api import create_app
from chronicler.db import (
    Base,
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.registry import create_campaign
from chronicler.db.repository import (
    insert_biography,
    insert_event_idempotent,
    upsert_character,
)
from chronicler.db.search import (
    search_biographies,
    search_characters,
    search_events,
)


def _create_fts5_shadows(engine: Engine) -> None:
    """Manually run the same DDL the alembic migration emits.

    Skip silently when SQLite was built without FTS5 (the migration
    does the same — the search helpers fall back to LIKE)."""
    with engine.begin() as conn:
        try:
            conn.exec_driver_sql("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
            conn.exec_driver_sql("DROP TABLE _fts5_probe")
        except Exception:
            return
        for table, fts in (("biographies", "biographies_fts"),):
            conn.exec_driver_sql(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
                f"body, content='{table}', content_rowid='id', tokenize='porter unicode61')"
            )
            conn.exec_driver_sql(f"INSERT INTO {fts}(rowid, body) SELECT id, body FROM {table}")
            conn.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {table}_ai AFTER INSERT ON {table} BEGIN "
                f"INSERT INTO {fts}(rowid, body) VALUES (new.id, new.body); END"
            )
            conn.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {table}_ad AFTER DELETE ON {table} BEGIN "
                f"INSERT INTO {fts}({fts}, rowid, body) VALUES ('delete', old.id, old.body); END"
            )
            conn.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {table}_au AFTER UPDATE ON {table} BEGIN "
                f"INSERT INTO {fts}({fts}, rowid, body) VALUES ('delete', old.id, old.body); "
                f"INSERT INTO {fts}(rowid, body) VALUES (new.id, new.body); END"
            )


def _seed_search_db(db_path: Path) -> None:
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        for cid, name, nick in (
            (1, "Alfred", None),
            (2, "Eadgyth", "the Wise"),
            (3, "Cuthbert", None),
        ):
            upsert_character(s, ck3_id=cid, first_name=name, nickname=nick, dynasty_name="Wessex")
        insert_biography(
            s,
            character_id=1,
            body="Alfred fought the Danes at the battle of Edington and won.",
            prompt_template_version="v1",
            provider="anthropic",
            generated_at="2026-05-01T10:00:00+00:00",
        )
        insert_biography(
            s,
            character_id=2,
            body="Eadgyth ruled in council, holding the realm together.",
            prompt_template_version="v1",
            provider="anthropic",
            generated_at="2026-05-01T11:00:00+00:00",
        )
        # Insert one event with a payload mention for the events scope
        insert_event_idempotent(
            s,
            schema_version=1,
            event_type="vanilla_memory",
            event_date="867.1.1",
            event_date_iso="0867-01-01",
            wall_clock_at="2026-05-01T00:00:00+00:00",
            primary_character_id=1,
            payload_json='{"v":1,"t":"vanilla_memory","p":{"memory_type":"battle_of_edington"}}',
            raw_line="seed",
        )
        s.commit()
    return engine, factory


@pytest.fixture
def session_with_fts5(tmp_path: Path) -> Iterator[Session]:
    engine, factory = _seed_search_db(tmp_path / "fts.db")
    _create_fts5_shadows(engine)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture
def session_without_fts5(tmp_path: Path) -> Iterator[Session]:
    """Same seeds, no FTS5 shadows — exercises the LIKE fallback path."""
    engine, factory = _seed_search_db(tmp_path / "like.db")
    with factory() as s:
        yield s
    engine.dispose()


# --- helper-level tests ---


def test_search_biographies_finds_match_via_fts5(session_with_fts5: Session) -> None:
    hits = search_biographies(session_with_fts5, "Edington")
    assert len(hits) == 1
    assert hits[0].character_id == 1
    assert "<mark>" in hits[0].snippet  # FTS5 snippet markup
    assert hits[0].rank is not None  # FTS5 path populates rank


def test_search_biographies_finds_match_via_like_fallback(
    session_without_fts5: Session,
) -> None:
    hits = search_biographies(session_without_fts5, "Edington")
    assert len(hits) == 1
    assert hits[0].character_id == 1
    assert hits[0].rank is None  # LIKE path doesn't compute rank
    assert "Edington" in hits[0].snippet


def test_search_characters_matches_first_name_and_nickname(
    session_without_fts5: Session,
) -> None:
    hits = search_characters(session_without_fts5, "Wise")
    assert len(hits) == 1
    assert hits[0].character_id == 2
    assert "Eadgyth" in hits[0].snippet
    assert "the Wise" in hits[0].snippet


def test_search_events_finds_payload_token(session_without_fts5: Session) -> None:
    hits = search_events(session_without_fts5, "edington")
    assert len(hits) == 1
    assert hits[0].character_id == 1
    assert "vanilla_memory" in hits[0].snippet


def test_fts5_query_with_hyphens_treated_as_phrase(session_with_fts5: Session) -> None:
    """User inputs with hyphens (which FTS5 reads as NEAR/exclusion
    operators) are quoted so the entire string becomes a phrase."""
    from chronicler.db.repository import insert_biography

    insert_biography(
        session_with_fts5,
        character_id=1,
        body="Mentioned the rare-search-token in passing.",
        prompt_template_version="v1",
        provider="anthropic",
        generated_at="2026-05-01T15:00:00+00:00",
    )
    session_with_fts5.commit()
    # Should not raise; should find the row via phrase tokenisation.
    hits = search_biographies(session_with_fts5, "rare-search-token")
    assert len(hits) == 1


def test_fts5_safe_query_quotes_bare_boolean_keywords() -> None:
    """ck3_chronicler-27ov.6 (audit M-B2): bare uppercase AND/OR/NOT/NEAR are
    FTS5 operators. The sanitizer only quoted on operator *characters*, so a
    query ending in (or consisting of) such a keyword passed through raw and
    raised. We don't expose operators, so these must be quoted as a phrase."""
    from chronicler.db.search import _fts5_safe_query

    assert _fts5_safe_query("AND") == '"AND"'
    assert _fts5_safe_query("Edington AND") == '"Edington AND"'
    assert _fts5_safe_query("cat NOT dog") == '"cat NOT dog"'
    assert _fts5_safe_query("cat OR dog") == '"cat OR dog"'
    assert _fts5_safe_query("foo NEAR bar") == '"foo NEAR bar"'
    # Clean queries keep implicit-AND (no quoting). Lowercase and/or/not are
    # barewords to FTS5, not operators, so they stay unquoted too.
    assert _fts5_safe_query("edington battle") == "edington battle"
    assert _fts5_safe_query("cat and dog") == "cat and dog"


def test_search_biographies_query_ending_in_boolean_keyword_does_not_raise(
    session_with_fts5: Session,
) -> None:
    """ck3_chronicler-27ov.6 (audit M-B2): MATCH 'Edington AND' raised
    sqlite3.OperationalError, 500ing the whole cross-campaign search. A bare
    keyword (and any operator syntax the sanitizer might miss) must degrade to
    a phrase search, never raise."""
    assert isinstance(search_biographies(session_with_fts5, "Edington AND"), list)
    assert isinstance(search_biographies(session_with_fts5, "AND"), list)
    assert isinstance(search_biographies(session_with_fts5, "cat NOT dog"), list)
    # A clean single-token query embedded with a trailing keyword is treated
    # as a phrase; the point is that it returns rather than 500s.
    assert isinstance(search_biographies(session_with_fts5, "the AND"), list)


def test_search_helpers_return_empty_for_blank_query(session_without_fts5: Session) -> None:
    assert search_biographies(session_without_fts5, "") == []
    assert search_biographies(session_without_fts5, "   ") == []
    assert search_characters(session_without_fts5, "") == []


def test_fts5_insert_trigger_indexes_new_biographies(
    session_with_fts5: Session,
) -> None:
    """A biography added after the index is built must still be findable
    via FTS5 — the AFTER INSERT trigger keeps the shadow in sync."""
    insert_biography(
        session_with_fts5,
        character_id=3,
        body="Cuthbert sailed to the western isles in his old age.",
        prompt_template_version="v1",
        provider="anthropic",
        generated_at="2026-05-01T13:00:00+00:00",
    )
    session_with_fts5.commit()
    hits = search_biographies(session_with_fts5, "western")
    assert len(hits) == 1
    assert hits[0].character_id == 3


# --- endpoint tests ---


@pytest.fixture
def search_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    registry = tmp_path / "registry.db"
    # Two campaigns with disjoint content
    for name in ("alpha", "beta"):
        campaign_db = tmp_path / "campaigns" / f"{name}.db"
        campaign_db.parent.mkdir(parents=True, exist_ok=True)
        engine, factory = _seed_search_db(campaign_db)
        if name == "beta":
            # Add a unique token only in beta
            with factory() as s:
                insert_biography(
                    s,
                    character_id=1,
                    body="Unique-token-betaonly appears here.",
                    prompt_template_version="v1",
                    provider="anthropic",
                    generated_at="2026-05-01T14:00:00+00:00",
                )
                s.commit()
        engine.dispose()
        create_campaign(name, db_path=str(campaign_db), registry=registry)
    app = create_app(registry_path=registry)
    with TestClient(app) as c:
        yield c


def test_search_endpoint_aggregates_across_campaigns(search_client: TestClient) -> None:
    """The 'Edington' token appears in both biography body and event
    payload across both campaigns: 2 biographies + 2 events = 4 hits."""
    resp = search_client.get("/api/search?q=Edington")
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "Edington"
    kinds = sorted(h["kind"] for h in data["hits"])
    assert kinds == ["biography", "biography", "event", "event"]
    assert set(data["by_campaign"].keys()) == {"alpha", "beta"}


def test_search_endpoint_filters_by_single_campaign(search_client: TestClient) -> None:
    resp = search_client.get("/api/search?q=Edington&campaign=alpha")
    data = resp.json()
    assert all(h["campaign_name"] == "alpha" for h in data["hits"])
    assert "alpha" in data["by_campaign"]


def test_search_endpoint_unknown_campaign_404(search_client: TestClient) -> None:
    resp = search_client.get("/api/search?q=x&campaign=ghost")
    assert resp.status_code == 404


def test_search_endpoint_scope_filter(search_client: TestClient) -> None:
    """scope=characters limits results to character matches only."""
    resp = search_client.get("/api/search?q=Eadgyth&scope=characters")
    data = resp.json()
    assert all(h["kind"] == "character" for h in data["hits"])


def test_search_endpoint_unknown_scope_returns_400(search_client: TestClient) -> None:
    resp = search_client.get("/api/search?q=x&scope=nonsense")
    assert resp.status_code == 400


def test_search_endpoint_finds_token_only_in_one_campaign(search_client: TestClient) -> None:
    resp = search_client.get("/api/search?q=betaonly")
    data = resp.json()
    assert len(data["hits"]) == 1
    assert data["hits"][0]["campaign_name"] == "beta"


def test_search_endpoint_empty_query_returns_422(search_client: TestClient) -> None:
    """Pydantic min_length=1 enforced by FastAPI's Query."""
    resp = search_client.get("/api/search?q=")
    assert resp.status_code == 422


def test_search_endpoint_attaches_coa_json_for_known_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-3yd9: hits whose character has a persisted coa_json
    must surface it on the response so the SearchPage card renders the
    real CoA via HeraldryWithFallback. Hits without a persisted CoA must
    surface coa_json=None so the FE falls through to the procedural shield.
    """
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    registry = tmp_path / "registry.db"
    campaign_db = tmp_path / "campaigns" / "gamma.db"
    campaign_db.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = _seed_search_db(campaign_db)
    # Persist a CoA dict on Alfred (#1) only; Eadgyth (#2) stays bare.
    with factory() as s:
        upsert_character(
            s,
            ck3_id=1,
            coa_json='{"pattern":"pattern_solid.dds","color1":"red","color2":"yellow_light"}',
        )
        s.commit()
    engine.dispose()
    create_campaign("gamma", db_path=str(campaign_db), registry=registry)
    app = create_app(registry_path=registry)
    with TestClient(app) as c:
        resp = c.get("/api/search?q=Eadgyth&campaign=gamma")
        assert resp.status_code == 200
        data = resp.json()
        # Search returns at least the character hit (id=2, no CoA persisted).
        eadgyth_hit = next(h for h in data["hits"] if h["character_id"] == 2)
        assert eadgyth_hit["coa_json"] is None

        resp = c.get("/api/search?q=Edington&campaign=gamma")
        assert resp.status_code == 200
        data = resp.json()
        # Biography + event hits both ride character_id=1 → real CoA attached.
        alfred_hits = [h for h in data["hits"] if h["character_id"] == 1]
        assert alfred_hits, "expected at least one hit with character_id=1"
        for h in alfred_hits:
            assert isinstance(h["coa_json"], dict)
            assert h["coa_json"]["pattern"] == "pattern_solid.dds"
