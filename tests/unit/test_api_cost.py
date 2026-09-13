"""HTTP-level tests for the chronicler cost API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
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

# --- ck3_chronicler-j7z: cost-summary endpoint ---


def _seed_cost_campaign(s: Session) -> None:
    """Seed a ``cost`` campaign with a mix of anthropic + ollama
    biographies, some this month and some prior."""
    this_month = datetime.now(UTC).strftime("%Y-%m")
    last_month = "2020-01"  # arbitrary prior month
    upsert_character(s, ck3_id=1, first_name="Alfred")
    # this month — anthropic opus
    insert_biography(
        s,
        character_id=1,
        body="x",
        prompt_template_version="v1",
        provider="anthropic:claude-opus-4-7",
        generated_at=f"{this_month}-01T00:00:00+00:00",
        prompt_tokens=10_000,
        completion_tokens=5_000,
    )
    # this month — ollama (free)
    insert_biography(
        s,
        character_id=1,
        body="y",
        prompt_template_version="v1",
        provider="ollama:qwen3:14b",
        generated_at=f"{this_month}-02T00:00:00+00:00",
        prompt_tokens=8_000,
        completion_tokens=4_000,
    )
    # this month — anthropic haiku (a third biography preserves the
    # cross-provider mix the cost-aggregation test asserts on)
    insert_biography(
        s,
        character_id=1,
        body="z",
        prompt_template_version="v1",
        provider="anthropic:claude-haiku-4-5",
        generated_at=f"{this_month}-03T00:00:00+00:00",
        prompt_tokens=2_000,
        completion_tokens=1_000,
    )
    # last month — anthropic sonnet
    insert_biography(
        s,
        character_id=1,
        body="old",
        prompt_template_version="v1",
        provider="anthropic:claude-sonnet-4-6",
        generated_at=f"{last_month}-15T00:00:00+00:00",
        prompt_tokens=20_000,
        completion_tokens=10_000,
    )


@pytest.fixture
def cost_client(make_campaign: Callable[..., TestClient]) -> TestClient:
    """A ``cost`` campaign seeded for the cost-summary tests. Tests that
    reopen the DB by hand rely on the ``campaigns/cost.db`` path."""
    return make_campaign("cost", seed=_seed_cost_campaign)


def test_cost_summary_aggregates_across_providers(cost_client: TestClient) -> None:
    """this_campaign sums all 4 rows; this_month omits the last_month bio.
    tbrm.6: USD column dropped — only token throughput is asserted."""
    resp = cost_client.get("/api/campaigns/cost/cost-summary")
    assert resp.status_code == 200
    data = resp.json()
    # All-time tokens: 10k+5k + 8k+4k + 2k+1k + 20k+10k = 60k total
    assert data["this_campaign"]["input_tokens"] == 10_000 + 8_000 + 2_000 + 20_000
    assert data["this_campaign"]["output_tokens"] == 5_000 + 4_000 + 1_000 + 10_000
    assert data["this_month"]["input_tokens"] == 10_000 + 8_000 + 2_000
    assert data["this_month"]["output_tokens"] == 5_000 + 4_000 + 1_000


def test_cost_summary_empty_campaign(client: TestClient) -> None:
    """The base test-campaign has 1 bio with NULL tokens — should
    produce 0/0 not crash."""
    resp = client.get("/api/campaigns/test-campaign/cost-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["this_campaign"]["input_tokens"] == 0
    assert data["this_campaign"]["output_tokens"] == 0


def test_cost_summary_404_unknown_campaign(client: TestClient) -> None:
    resp = client.get("/api/campaigns/nope/cost-summary")
    assert resp.status_code == 404


def test_cost_summary_includes_closing_chronicle_tokens(
    cost_client: TestClient, tmp_path: Path
) -> None:
    """ck3_chronicler-li0z: the closing chronicle persists to the registry,
    not a Biography row, so its tokens must be folded into the cost totals
    explicitly. Both the this_campaign/this_month surface and the lifetime
    session-summary surface must include it."""
    from chronicler.db.registry import (
        get_campaign_by_name,
        set_campaign_closing_chronicle,
    )

    registry = tmp_path / "registry.db"
    cid = get_campaign_by_name("cost", registry=registry).id
    this_month = datetime.now(UTC).strftime("%Y-%m")
    set_campaign_closing_chronicle(
        cid,
        "the realm endured",
        generated_at=f"{this_month}-04T00:00:00+00:00",
        input_tokens=500_000,
        output_tokens=14_000,
        registry=registry,
    )

    data = cost_client.get("/api/campaigns/cost/cost-summary").json()
    # bios all-time input = 40k; this-month bios input = 20k; + closing 500k.
    assert data["this_campaign"]["input_tokens"] == 40_000 + 500_000
    assert data["this_campaign"]["output_tokens"] == 20_000 + 14_000
    assert data["this_month"]["input_tokens"] == 20_000 + 500_000
    assert data["this_month"]["output_tokens"] == 10_000 + 14_000

    # session-summary lifetime (no reset → lifetime) also includes it.
    sess = cost_client.get("/api/campaigns/cost/cost/session-summary").json()
    assert sess["lifetime"]["input"] == 40_000 + 500_000
    assert sess["lifetime"]["output"] == 20_000 + 14_000


def test_cost_summary_reports_usd(cost_client: TestClient) -> None:
    """ck3_chronicler-cs1o: USD revived. Seeded rows carry no persisted
    cost_usd and no cache breakdown, so each bills the summed-input
    approximation at its provider's base rates."""
    data = cost_client.get("/api/campaigns/cost/cost-summary").json()
    month_usd = (
        (10_000 / 1000) * 0.005
        + (5_000 / 1000) * 0.025  # opus
        + 0.0  # ollama
        + (2_000 / 1000) * 0.001
        + (1_000 / 1000) * 0.005  # haiku
    )
    lifetime_usd = month_usd + (20_000 / 1000) * 0.003 + (10_000 / 1000) * 0.015
    assert data["this_month"]["usd"] == pytest.approx(month_usd)
    assert data["this_campaign"]["usd"] == pytest.approx(lifetime_usd)


def test_session_summary_reports_usd_and_monthly_target(
    cost_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-cs1o: the dual meter gains USD + a monthly USD
    soft target (default $100 — the Team Premium programmatic credit)."""
    # Isolate the settings store: a real dev-box settings.json may carry
    # a session_started_at, which would shrink the session bucket.
    monkeypatch.setattr(
        "chronicler.settings_store.DEFAULT_SETTINGS_PATH",
        tmp_path / "settings.json",
    )
    sess = cost_client.get("/api/campaigns/cost/cost/session-summary").json()
    month_usd = (
        (10_000 / 1000) * 0.005
        + (5_000 / 1000) * 0.025
        + (2_000 / 1000) * 0.001
        + (1_000 / 1000) * 0.005
    )
    lifetime_usd = month_usd + (20_000 / 1000) * 0.003 + (10_000 / 1000) * 0.015
    assert sess["lifetime_usd"] == pytest.approx(lifetime_usd)
    # No reset yet → session mirrors lifetime.
    assert sess["session_usd"] == pytest.approx(lifetime_usd)
    assert sess["month_usd"] == pytest.approx(month_usd)
    assert sess["target_monthly_usd"] == 100.0


def test_cost_summary_prefers_persisted_cost_usd(cost_client: TestClient, tmp_path: Path) -> None:
    """Rows carrying the transport's own cost figure bill at that figure,
    not the token approximation (1M input tokens would approximate to
    $5 on opus — the persisted $0.42 must win)."""

    engine = make_engine_for_path(tmp_path / "campaigns" / "cost.db")
    factory = make_session_factory(engine)
    this_month = datetime.now(UTC).strftime("%Y-%m")
    with factory() as s:
        insert_biography(
            s,
            character_id=1,
            body="costed",
            prompt_template_version="v5",
            provider="claude-code:claude-opus-4-7",
            generated_at=f"{this_month}-05T00:00:00+00:00",
            prompt_tokens=1_000_000,
            completion_tokens=1_000,
            cache_read_tokens=900_000,
            cache_write_tokens=90_000,
            cost_usd=0.42,
        )
        s.commit()
    engine.dispose()

    data = cost_client.get("/api/campaigns/cost/cost-summary").json()
    seeded_month_usd = (
        (10_000 / 1000) * 0.005
        + (5_000 / 1000) * 0.025
        + (2_000 / 1000) * 0.001
        + (1_000 / 1000) * 0.005
    )
    assert data["this_month"]["usd"] == pytest.approx(seeded_month_usd + 0.42)
    # tokens still aggregate normally
    assert data["this_month"]["input_tokens"] == 20_000 + 1_000_000
