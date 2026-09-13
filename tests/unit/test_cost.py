"""Unit tests for chronicler.cost — token rate card + closing-chronicle accounting.

The closing-chronicle helpers moved out of db/repository.py (where they were
parked behind a duck-typed ``campaign: object``) to this dedicated cost module
with typed access to the registry :class:`Campaign` (M-B5). The per-1k USD rate
card (lookup_token_price / compute_generation_cost) moved here from config.py
when pricing was relocated out of the path-defaults module (ck3_chronicler-27ov.80,
audit L24); ck3_chronicler-tbrm.6 had removed these price tests, cs1o revived
them (generations bill real money / the programmatic credit pool again).
"""

from __future__ import annotations

from typing import Any

import pytest

from chronicler.cost import (
    CACHE_READ_INPUT_MULTIPLIER,
    CACHE_WRITE_INPUT_MULTIPLIER,
    add_closing_chronicle_tokens,
    compute_generation_cost,
    filter_closing_tokens,
    get_provider_costs,
    has_token_price,
    lookup_token_price,
    new_token_usd_bucket,
)
from chronicler.db.registry.campaigns import Campaign


def _campaign(**overrides: Any) -> Campaign:
    base: dict[str, Any] = dict(
        id="c1",
        name="Test",
        ck3_playthrough_id=None,
        ck3_version=None,
        created_at="2026-01-01",
        last_event_at=None,
        archived=False,
        db_path="/tmp/x.db",
        founding_dynasty_name=None,
        tail_offset=0,
    )
    base.update(overrides)
    return Campaign(**base)


def test_new_bucket_shape() -> None:
    assert new_token_usd_bucket() == {
        "input": 0,
        "output": 0,
        "usd_persisted": 0.0,
        "uncosted_input": 0,
        "uncosted_read": 0,
        "uncosted_write": 0,
        "uncosted_output": 0,
    }


def test_filter_closing_tokens_passthrough() -> None:
    assert filter_closing_tokens(100, 50, "2026-05-01") == (100, 50)


def test_filter_closing_tokens_zero_is_noop() -> None:
    assert filter_closing_tokens(0, 0, "2026-05-01") == (0, 0)
    assert filter_closing_tokens(None, None, None) == (0, 0)


def test_filter_closing_tokens_month_prefix_excludes() -> None:
    assert filter_closing_tokens(100, 50, "2026-04-30", month_prefix="2026-05") == (0, 0)
    assert filter_closing_tokens(100, 50, "2026-05-02", month_prefix="2026-05") == (100, 50)


def test_filter_closing_tokens_since_excludes() -> None:
    assert filter_closing_tokens(100, 50, "2026-04-30", since="2026-05-01") == (0, 0)
    assert filter_closing_tokens(100, 50, "2026-05-02", since="2026-05-01") == (100, 50)


def test_add_closing_persisted_cost_buckets_under_provider() -> None:
    agg: dict[str, dict[str, float]] = {}
    camp = _campaign(
        closing_chronicle_input_tokens=1000,
        closing_chronicle_output_tokens=2000,
        closing_chronicle_generated_at="2026-05-10",
        closing_chronicle_provider="claude-code:claude-opus-4-7",
        closing_chronicle_cost_usd=0.42,
    )
    add_closing_chronicle_tokens(agg, camp)
    bucket = agg["claude-code:claude-opus-4-7"]
    assert bucket["input"] == 1000
    assert bucket["output"] == 2000
    assert bucket["usd_persisted"] == 0.42
    assert bucket["uncosted_input"] == 0
    assert bucket["uncosted_output"] == 0


def test_add_closing_uncosted_folds_token_fields() -> None:
    agg: dict[str, dict[str, float]] = {}
    camp = _campaign(
        closing_chronicle_input_tokens=1000,
        closing_chronicle_output_tokens=2000,
        closing_chronicle_generated_at="2026-05-10",
        closing_chronicle_provider="claude-code:closing",
        closing_chronicle_cost_usd=None,
        closing_chronicle_cache_read_tokens=300,
        closing_chronicle_cache_write_tokens=100,
    )
    add_closing_chronicle_tokens(agg, camp)
    bucket = agg["claude-code:closing"]
    assert bucket["usd_persisted"] == 0.0
    assert bucket["uncosted_input"] == 1000
    assert bucket["uncosted_read"] == 300
    assert bucket["uncosted_write"] == 100
    assert bucket["uncosted_output"] == 2000


def test_add_closing_provider_fallback_when_unset() -> None:
    agg: dict[str, dict[str, float]] = {}
    camp = _campaign(
        closing_chronicle_input_tokens=10,
        closing_chronicle_output_tokens=20,
        closing_chronicle_generated_at="2026-05-10",
        closing_chronicle_provider=None,
        closing_chronicle_cost_usd=0.01,
    )
    add_closing_chronicle_tokens(agg, camp)
    assert "claude-code:closing-chronicle" in agg


def test_add_closing_noop_when_filtered_out_by_month() -> None:
    agg: dict[str, dict[str, float]] = {}
    camp = _campaign(
        closing_chronicle_input_tokens=10,
        closing_chronicle_output_tokens=20,
        closing_chronicle_generated_at="2026-04-30",
        closing_chronicle_cost_usd=0.01,
    )
    assert add_closing_chronicle_tokens(agg, camp, month_prefix="2026-05") == {}


def test_add_closing_noop_when_no_tokens() -> None:
    agg: dict[str, dict[str, float]] = {}
    assert add_closing_chronicle_tokens(agg, _campaign()) == {}


# --- ck3_chronicler-cs1o: token rate card (moved from config.py, audit L24) ---


def test_get_provider_costs_returns_known_providers() -> None:
    costs = get_provider_costs()
    assert "anthropic" in costs
    assert "claude-code" in costs
    assert "ollama" in costs  # historical rows still price at 0
    for provider in ("anthropic", "claude-code"):
        assert "claude-opus-4-7" in costs[provider]
        assert "claude-opus-4-8" in costs[provider]
        assert "claude-sonnet-4-6" in costs[provider]
        assert "claude-haiku-4-5" in costs[provider]


def test_get_provider_costs_returns_defensive_copy() -> None:
    """Mutations to the returned dict must not poison the module config."""
    costs = get_provider_costs()
    costs["anthropic"]["claude-opus-4-7"]["input_per_1k_usd"] = 999
    fresh = get_provider_costs()
    assert fresh["anthropic"]["claude-opus-4-7"]["input_per_1k_usd"] != 999


def test_lookup_token_price_anthropic_opus() -> None:
    in_price, out_price = lookup_token_price("anthropic:claude-opus-4-7")
    assert in_price == 0.005
    assert out_price == 0.025


def test_lookup_token_price_1m_variant_bills_at_standard_rate() -> None:
    """27ov.27: per the published pricing page, Opus 4.7/4.8 and Sonnet
    4.6 include the full 1M context window at STANDARD pricing — the
    [1m] variants carry no surcharge."""
    for model in ("claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-4-6"):
        base = lookup_token_price(f"claude-code:{model}")
        lm = lookup_token_price(f"claude-code:{model}[1m]")
        assert lm == base, model


def test_anthropic_pricing_table_pinned_to_published_rates() -> None:
    """Pin the whole table so price drift is visible in review (27ov.27).

    Source: https://platform.claude.com/docs/en/about-claude/pricing
    verified 2026-06-12 — Opus 4.7/4.8 $5/$25 per MTok, Sonnet 4.6
    $3/$15, Haiku 4.5 $1/$5; 1M context at standard pricing (no
    long-context premium on any current model). If this test fails
    after editing cost.py, re-verify against the pricing page and
    update BOTH the table and this pin with a fresh date.
    """
    per_mtok = {  # (input USD/MTok, output USD/MTok)
        "claude-opus-4-7": (5, 25),
        "claude-opus-4-7[1m]": (5, 25),
        "claude-opus-4-8": (5, 25),
        "claude-opus-4-8[1m]": (5, 25),
        "claude-sonnet-4-6": (3, 15),
        "claude-sonnet-4-6[1m]": (3, 15),
        "claude-haiku-4-5": (1, 5),
    }
    table = get_provider_costs()["anthropic"]
    assert set(table) == set(per_mtok)
    for model, (in_mtok, out_mtok) in per_mtok.items():
        assert table[model]["input_per_1k_usd"] == pytest.approx(in_mtok / 1000), model
        assert table[model]["output_per_1k_usd"] == pytest.approx(out_mtok / 1000), model


def test_lookup_token_price_ollama_catchall() -> None:
    assert lookup_token_price("ollama:qwen3:14b") == (0.0, 0.0)


def test_lookup_token_price_unknown_provider_returns_zero() -> None:
    assert lookup_token_price("unknown:something") == (0.0, 0.0)


def test_lookup_token_price_claude_code_catchall_is_conservative() -> None:
    """An unknown future model on claude-code bills at the Fable-5-tier
    ceiling rather than 0 — over-reporting beats silently free."""
    in_price, out_price = lookup_token_price("claude-code:claude-future-9000")
    assert in_price > 0
    assert out_price > 0
    # Must be >= every known row so "conservative" stays true as the
    # table evolves.
    for model, row in get_provider_costs()["anthropic"].items():
        assert in_price >= row["input_per_1k_usd"], model
        assert out_price >= row["output_per_1k_usd"], model


def test_compute_generation_cost_with_breakdown() -> None:
    # 1012 total in = 12 marginal + 100 cache-write + 900 cache-read; 9 out.
    usd = compute_generation_cost(
        "anthropic:claude-opus-4-7",
        input_tokens=1012,
        cache_read_tokens=900,
        cache_write_tokens=100,
        output_tokens=9,
    )
    expected = (
        (12 / 1000) * 0.005
        + (100 / 1000) * 0.005 * CACHE_WRITE_INPUT_MULTIPLIER
        + (900 / 1000) * 0.005 * CACHE_READ_INPUT_MULTIPLIER
        + (9 / 1000) * 0.025
    )
    assert usd == pytest.approx(expected)


def test_compute_generation_cost_no_breakdown_approximates() -> None:
    """Legacy rows (no cache columns): all input bills at base rate —
    the documented over-count approximation."""
    usd = compute_generation_cost(
        "claude-code:claude-opus-4-7",
        input_tokens=1000,
        cache_read_tokens=None,
        cache_write_tokens=None,
        output_tokens=100,
    )
    assert usd == pytest.approx((1000 / 1000) * 0.005 + (100 / 1000) * 0.025)


def test_compute_generation_cost_handles_nones_and_unknown() -> None:
    assert (
        compute_generation_cost(
            "unknown:model",
            input_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=None,
        )
        == 0.0
    )


def test_compute_generation_cost_breakdown_never_negative_marginal() -> None:
    """Defensive: corrupt rows where read+write exceed the summed input
    clamp marginal at 0 instead of producing a negative line item."""
    usd = compute_generation_cost(
        "anthropic:claude-sonnet-4-6",
        input_tokens=100,
        cache_read_tokens=900,
        cache_write_tokens=100,
        output_tokens=0,
    )
    expected = (100 / 1000) * 0.003 * CACHE_WRITE_INPUT_MULTIPLIER + (
        900 / 1000
    ) * 0.003 * CACHE_READ_INPUT_MULTIPLIER
    assert usd == pytest.approx(expected)


# --- issue #21: openai-compatible transport rate card ---


def test_openai_pricing_table_pinned_to_published_rates() -> None:
    """Pin the OpenAI table so price drift is visible in review (issue #21).

    Source: https://platform.openai.com/docs/pricing — Flagship models,
    **Standard** tier, **short-context** column group (verified
    2026-07-29). Long-context prompts bill 2x these rates on the same
    page; the rate card does not model that tier, so very long prompts
    under-report (documented in cost.py beside the table). Regional
    data-residency endpoints add a 10% uplift, also unmodelled.

    If this test fails after editing cost.py, re-verify against the
    pricing page and update BOTH the table and this pin with a fresh date.
    """
    per_mtok = {  # (input USD/MTok, output USD/MTok)
        "gpt-5.6-sol": (5.00, 30.00),
        "gpt-5.6-terra": (2.50, 15.00),
        "gpt-5.6-luna": (1.00, 6.00),
        "gpt-5.5": (5.00, 30.00),
        "gpt-5.5-pro": (30.00, 180.00),
        "gpt-5.4": (2.50, 15.00),
        "gpt-5.4-mini": (0.75, 4.50),
        "gpt-5.4-nano": (0.20, 1.25),
        "gpt-5.4-pro": (30.00, 180.00),
    }
    table = get_provider_costs()["openai"]
    assert set(table) == set(per_mtok)
    for model, (in_mtok, out_mtok) in per_mtok.items():
        assert table[model]["input_per_1k_usd"] == pytest.approx(in_mtok / 1000), model
        assert table[model]["output_per_1k_usd"] == pytest.approx(out_mtok / 1000), model


def test_deepseek_pricing_table_pinned_to_published_rates() -> None:
    """Pin the DeepSeek table (issue #21).

    Source: https://api-docs.deepseek.com/quick_start/pricing (verified
    2026-07-29). Input is the CACHE-MISS price — the base rate. DeepSeek
    prices a cache hit at ~0.02x base rather than the table-wide 0.1x
    :data:`CACHE_READ_INPUT_MULTIPLIER`, so cached input over-reports
    ~5x on this provider; the absolute error is negligible (fractions of
    a cent) and over-reporting beats silently free.
    """
    per_mtok = {  # (cache-miss input USD/MTok, output USD/MTok)
        "deepseek-v4-flash": (0.14, 0.28),
        "deepseek-v4-pro": (0.435, 0.87),
    }
    table = get_provider_costs()["deepseek"]
    assert set(table) == set(per_mtok)
    for model, (in_mtok, out_mtok) in per_mtok.items():
        assert table[model]["input_per_1k_usd"] == pytest.approx(in_mtok / 1000), model
        assert table[model]["output_per_1k_usd"] == pytest.approx(out_mtok / 1000), model


def test_local_presets_bill_zero_explicitly() -> None:
    """ollama and lmstudio run on the user's own GPU: zero is the real
    price, not a missing entry. Both need a catch-all because a local
    model tag is arbitrary (``qwen3:14b``, ``llama-3.3-70b-instruct``)."""
    for provider in ("ollama", "lmstudio"):
        assert lookup_token_price(f"{provider}:some-local-model") == (0.0, 0.0)
        assert has_token_price(f"{provider}:some-local-model"), provider


def test_has_token_price_separates_known_zero_from_unpriced() -> None:
    """The distinction the None-vs-0.0 rule rests on (issue #21).

    ``lookup_token_price`` returns (0.0, 0.0) both for a local model
    that genuinely costs nothing and for a model nobody has priced. A
    row must persist ``cost_usd=0.0`` in the first case (a real,
    aggregated zero) and ``None`` in the second (unknown, so the cost UI
    shows it as uncosted rather than free).
    """
    assert has_token_price("ollama:qwen3:14b") is True
    assert has_token_price("openai:gpt-5.6-sol") is True
    assert has_token_price("deepseek:deepseek-v4-pro") is True
    # Paid providers get no "*" catch-all: an unrecognised model is
    # unknown, not free.
    assert has_token_price("openai:gpt-9-unreleased") is False
    assert has_token_price("deepseek:deepseek-v9") is False
    # The generic tag (no preset chosen) can't be priced either.
    assert has_token_price("openai-compatible:mystery-model") is False
    assert has_token_price("unknown:something") is False
    # claude-code keeps its conservative catch-all, so it stays priced.
    assert has_token_price("claude-code:claude-future-9000") is True


def test_unknown_paid_model_prices_at_zero_but_is_unpriced() -> None:
    """compute_generation_cost still returns 0.0 for an unpriced tag —
    callers use has_token_price to decide None-vs-0.0, so the two must
    disagree here or the transport can't tell them apart."""
    usd = compute_generation_cost(
        "openai:gpt-9-unreleased",
        input_tokens=1000,
        cache_read_tokens=None,
        cache_write_tokens=None,
        output_tokens=500,
    )
    assert usd == 0.0
    assert has_token_price("openai:gpt-9-unreleased") is False
