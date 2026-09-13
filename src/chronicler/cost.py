"""Cost accounting for LLM token spend.

The closing-chronicle cost helpers live here (ck3_chronicler-27ov.51 / M-B5):
they were parked in ``db/repository.py`` behind a duck-typed ``campaign: object``
to dodge an import cycle that does not actually exist — the registry never
imports the repository. Co-located beside the token-bucket shape, they take a
typed registry :class:`Campaign` and read its fields directly, so the getattr
chains over the very columns cs1o added are gone.

The per-1k USD pricing tables and conversion helpers
(:func:`lookup_token_price`, :func:`compute_generation_cost`) live here too,
moved out of ``config.py`` (whose remit is path defaults) so the whole cost
surface — rate card and accounting — sits in one module (ck3_chronicler-27ov.80,
audit L24).
"""

from __future__ import annotations

from typing import TypedDict

from chronicler.db.registry.campaigns import Campaign


class TokenUsdBucket(TypedDict):
    """Per-provider token + USD accounting in the cs1o shape (audit L19).

    ``usd_persisted`` is a float (provider-reported or locally-priced cost);
    every other field is a token count. The repository aggregators used to
    annotate this as ``dict[str, int]``, which lied about ``usd_persisted``.
    """

    input: int
    output: int
    usd_persisted: float
    uncosted_input: int
    uncosted_read: int
    uncosted_write: int
    uncosted_output: int


def new_token_usd_bucket() -> TokenUsdBucket:
    """Empty per-provider bucket in the cs1o token+USD shape."""
    return {
        "input": 0,
        "output": 0,
        "usd_persisted": 0.0,
        "uncosted_input": 0,
        "uncosted_read": 0,
        "uncosted_write": 0,
        "uncosted_output": 0,
    }


def filter_closing_tokens(
    input_tokens: int | None,
    output_tokens: int | None,
    generated_at: str | None,
    *,
    month_prefix: str | None = None,
    since: str | None = None,
) -> tuple[int, int]:
    """Closing-chronicle tokens to fold into a cost bucket (ck3_chronicler-li0z).

    The closing chronicle lives on the registry campaign row, not a Biography
    row, so :func:`chronicler.db.repository.aggregate_campaign_tokens` never sees
    it. Cost endpoints call this with the campaign's persisted closing tokens +
    generated_at, applying the SAME ``month_prefix`` / ``since`` predicates the
    aggregate uses, and add the result to their totals. Returns ``(0, 0)`` when
    unrecorded or filtered out.
    """
    in_tokens = input_tokens or 0
    out_tokens = output_tokens or 0
    if in_tokens == 0 and out_tokens == 0:
        return (0, 0)
    if month_prefix is not None and not (generated_at or "").startswith(month_prefix):
        return (0, 0)
    if since is not None and not (generated_at is not None and generated_at >= since):
        return (0, 0)
    return (in_tokens, out_tokens)


def add_closing_chronicle_tokens(
    aggregate: dict[str, dict[str, float]],
    campaign: Campaign,
    *,
    month_prefix: str | None = None,
    since: str | None = None,
) -> dict[str, dict[str, float]]:
    """Inject the campaign's closing-chronicle tokens into a per-provider
    aggregate (ck3_chronicler-li0z) so the cost endpoints' provider-summing
    logic counts them. Mutates and returns ``aggregate``.

    ck3_chronicler-cs1o: buckets under the chronicle's persisted provider tag
    when present (pre-cs1o chronicles fall back to the legacy fixed key) and
    folds the persisted cost / uncosted token fields in, mirroring
    :func:`chronicler.db.repository.aggregate_campaign_tokens`'s shape."""
    in_tokens, out_tokens = filter_closing_tokens(
        campaign.closing_chronicle_input_tokens,
        campaign.closing_chronicle_output_tokens,
        campaign.closing_chronicle_generated_at,
        month_prefix=month_prefix,
        since=since,
    )
    if not (in_tokens or out_tokens):
        return aggregate
    provider = campaign.closing_chronicle_provider or "claude-code:closing-chronicle"
    bucket = aggregate.setdefault(provider, new_token_usd_bucket())
    bucket["input"] += in_tokens
    bucket["output"] += out_tokens
    cost = campaign.closing_chronicle_cost_usd
    if isinstance(cost, (int, float)):
        bucket["usd_persisted"] += float(cost)
    else:
        bucket["uncosted_input"] += in_tokens
        bucket["uncosted_read"] += campaign.closing_chronicle_cache_read_tokens or 0
        bucket["uncosted_write"] += campaign.closing_chronicle_cache_write_tokens or 0
        bucket["uncosted_output"] += out_tokens
    return aggregate


# --- token rate card (ck3_chronicler-27ov.80, audit L24: moved from config.py) ---

# ck3_chronicler-cs1o: per-provider/model token pricing in USD per 1k
# tokens, revived from the j7z table that tbrm.6 removed (generations
# bill real money — or the metered programmatic credit pool — again
# after Anthropic's 2026-06-15 billing split).
#
# "anthropic" prices the direct-API transport AND historical pre-tbrm.3
# rows; "claude-code" mirrors it because claude --print's pool is
# metered at the same standard API rates.
#
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# (verified 2026-06-12, 27ov.27). Opus 4.7/4.8 are $5/$25 per MTok —
# the previous $15/$75 rows were Opus 4.1-era prices, a 3x
# overstatement. Haiku 4.5 is $1/$5 (the old $0.80/$4 row was Haiku
# 3.5). The [1m] long-context variants bill at STANDARD rates — per
# the pricing page, Opus 4.8/4.7/4.6 and Sonnet 4.6 "include the full
# 1M token context window at standard pricing"; there is no 2x input
# surcharge anymore. The table is code, prices drift: re-verify
# against the pricing page when bumping the default model
# (test_anthropic_pricing_table_pinned_to_published_rates pins it).
_ANTHROPIC_MODEL_COSTS: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"input_per_1k_usd": 0.005, "output_per_1k_usd": 0.025},
    "claude-opus-4-7[1m]": {"input_per_1k_usd": 0.005, "output_per_1k_usd": 0.025},
    "claude-opus-4-8": {"input_per_1k_usd": 0.005, "output_per_1k_usd": 0.025},
    "claude-opus-4-8[1m]": {"input_per_1k_usd": 0.005, "output_per_1k_usd": 0.025},
    "claude-sonnet-4-6": {"input_per_1k_usd": 0.003, "output_per_1k_usd": 0.015},
    "claude-sonnet-4-6[1m]": {"input_per_1k_usd": 0.003, "output_per_1k_usd": 0.015},
    "claude-haiku-4-5": {"input_per_1k_usd": 0.001, "output_per_1k_usd": 0.005},
}

# Issue #21: the openai-compatible transport's rate card. One transport
# speaks to several vendors, so the provider tag carries the chosen
# preset (openai:, deepseek:, ollama:, lmstudio:, openrouter:) and each
# gets its own table.
#
# Deliberately NO "*" catch-all on the paid vendors: an unrecognised
# model must read as UNKNOWN, not free. The transport asks
# has_token_price() and persists cost_usd=None in that case, so the cost
# UI reports the row as uncosted instead of billing it at zero. Only the
# local presets get a catch-all, because there the zero is real.
#
# Source: https://platform.openai.com/docs/pricing — "Flagship models",
# Standard tier, SHORT-context column group (verified 2026-07-29).
# Two things this table does not model, both under-counts:
#   - long-context prompts bill 2x on the same page (no published
#     threshold to key off, so no attempt is made);
#   - regional data-residency endpoints add a 10% uplift.
# test_openai_pricing_table_pinned_to_published_rates pins every row.
_OPENAI_MODEL_COSTS: dict[str, dict[str, float]] = {
    "gpt-5.6-sol": {"input_per_1k_usd": 0.005, "output_per_1k_usd": 0.030},
    "gpt-5.6-terra": {"input_per_1k_usd": 0.0025, "output_per_1k_usd": 0.015},
    "gpt-5.6-luna": {"input_per_1k_usd": 0.001, "output_per_1k_usd": 0.006},
    "gpt-5.5": {"input_per_1k_usd": 0.005, "output_per_1k_usd": 0.030},
    "gpt-5.5-pro": {"input_per_1k_usd": 0.030, "output_per_1k_usd": 0.180},
    "gpt-5.4": {"input_per_1k_usd": 0.0025, "output_per_1k_usd": 0.015},
    "gpt-5.4-mini": {"input_per_1k_usd": 0.00075, "output_per_1k_usd": 0.0045},
    "gpt-5.4-nano": {"input_per_1k_usd": 0.0002, "output_per_1k_usd": 0.00125},
    "gpt-5.4-pro": {"input_per_1k_usd": 0.030, "output_per_1k_usd": 0.180},
}

# Source: https://api-docs.deepseek.com/quick_start/pricing (verified
# 2026-07-29). Input is the CACHE-MISS (base) rate. DeepSeek prices a
# cache hit at ~0.02x base, not the table-wide 0.1x
# CACHE_READ_INPUT_MULTIPLIER, so cached input over-reports ~5x here —
# fractions of a cent at these rates, and over-reporting beats free.
_DEEPSEEK_MODEL_COSTS: dict[str, dict[str, float]] = {
    "deepseek-v4-flash": {"input_per_1k_usd": 0.00014, "output_per_1k_usd": 0.00028},
    "deepseek-v4-pro": {"input_per_1k_usd": 0.000435, "output_per_1k_usd": 0.00087},
}

_LOCAL_FREE_COSTS: dict[str, dict[str, float]] = {
    # The user's own GPU. Zero is the real price, and a local model tag
    # is arbitrary ("qwen3:14b"), so the catch-all is the only sane key.
    "*": {"input_per_1k_usd": 0.0, "output_per_1k_usd": 0.0},
}

_PROVIDER_COSTS: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": dict(_ANTHROPIC_MODEL_COSTS),
    "openai": dict(_OPENAI_MODEL_COSTS),
    "deepseek": dict(_DEEPSEEK_MODEL_COSTS),
    "lmstudio": dict(_LOCAL_FREE_COSTS),
    "claude-code": {
        **_ANTHROPIC_MODEL_COSTS,
        # Catch-all defaults to the Fable-5 tier ($10/$50 per MTok) —
        # the highest standard published rate — so an unknown future
        # model string still bills conservatively rather than free.
        "*": {"input_per_1k_usd": 0.010, "output_per_1k_usd": 0.050},
    },
    # Predates the openai-compatible transport (historical pre-tbrm.3
    # rows priced local generations here); issue #21 made it a live
    # preset of that transport, sharing the local-free card.
    "ollama": dict(_LOCAL_FREE_COSTS),
}

# Prompt-cache pricing is a fixed multiple of base input across all
# Anthropic models: ephemeral (5-min) cache writes bill 1.25x base
# input, cache reads 0.1x.
CACHE_WRITE_INPUT_MULTIPLIER = 1.25
CACHE_READ_INPUT_MULTIPLIER = 0.10


def get_provider_costs() -> dict[str, dict[str, dict[str, float]]]:
    """Return per-provider/per-model price config (USD per 1k tokens).

    The mapping is ``{provider: {model: {input_per_1k_usd, output_per_1k_usd}}}``.
    A model key of ``"*"`` is the catch-all for that provider.
    """
    # Defensive copy so callers can mutate freely without poisoning the
    # module-level config.
    return {p: {m: dict(v) for m, v in models.items()} for p, models in _PROVIDER_COSTS.items()}


def lookup_token_price(
    provider_string: str, *, costs: dict[str, dict[str, dict[str, float]]] | None = None
) -> tuple[float, float]:
    """Resolve ``"<provider>:<model>"`` (or just ``"<provider>"``) to a
    (input_per_1k_usd, output_per_1k_usd) pair.

    Falls back to (0, 0) for unknown providers/models — a missing model
    shouldn't crash the cost endpoint, it just means we can't bill it.
    The catch-all ``"*"`` model entry on a provider matches anything.
    """
    table = costs if costs is not None else get_provider_costs()
    if ":" in provider_string:
        provider, _, model = provider_string.partition(":")
    else:
        provider, model = provider_string, ""
    models = table.get(provider) or {}
    entry = models.get(model) or models.get("*")
    if not entry:
        return 0.0, 0.0
    return entry["input_per_1k_usd"], entry["output_per_1k_usd"]


def has_token_price(
    provider_string: str, *, costs: dict[str, dict[str, dict[str, float]]] | None = None
) -> bool:
    """Whether the rate card actually carries a price for this tag.

    Issue #21: :func:`lookup_token_price` returns ``(0.0, 0.0)`` both for
    a local model that genuinely costs nothing (``ollama``, ``lmstudio``)
    and for one nobody has priced. Transports need the difference to
    honour the None-vs-0.0 rule — persist ``cost_usd=0.0`` for a real
    zero (the cost UI aggregates it as costed, see
    ``Biography.cost_usd.is_(None)`` in ``db/repository.py``) and
    ``None`` for an unknown model, which the UI reports as uncosted
    rather than free.
    """
    table = costs if costs is not None else get_provider_costs()
    provider, _, model = provider_string.partition(":")
    models = table.get(provider) or {}
    return (models.get(model) or models.get("*")) is not None


def compute_generation_cost(
    provider_string: str,
    *,
    input_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    output_tokens: int | None,
    costs: dict[str, dict[str, dict[str, float]]] | None = None,
) -> float:
    """USD for one generation (ck3_chronicler-cs1o).

    With a cache breakdown, bills each bucket at its real rate
    (marginal = input - read - write, clamped at 0; writes 1.25x base
    input; reads 0.1x). Without one — legacy rows whose cache columns
    are NULL — all input bills at base rate, the documented over-count
    approximation (~3x at the observed 0.73 cache-read ratio).
    Unknown providers/models price at 0.
    """
    in_per_1k, out_per_1k = lookup_token_price(provider_string, costs=costs)
    total_in = input_tokens or 0
    out = output_tokens or 0
    read = cache_read_tokens or 0
    write = cache_write_tokens or 0
    if read or write:
        marginal = max(total_in - read - write, 0)
        in_usd = (
            (marginal / 1000) * in_per_1k
            + (write / 1000) * in_per_1k * CACHE_WRITE_INPUT_MULTIPLIER
            + (read / 1000) * in_per_1k * CACHE_READ_INPUT_MULTIPLIER
        )
    else:
        in_usd = (total_in / 1000) * in_per_1k
    return in_usd + (out / 1000) * out_per_1k
