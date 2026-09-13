"""Cost + session-boundary routes (ck3_chronicler-0224 / ma96).

Three endpoints under ``/api/campaigns/{name}/cost/``:

- ``POST /end-session`` — session-boundary marker. Memory consolidation
  sweep removed (plan: cozy-coalescing-shannon). Now a no-op that
  returns 0 tracked_considered; retained for API compatibility.

- ``GET /session-summary`` returns the dual-meter snapshot — lifetime
  (campaign all-time) and session (since the user-controlled boundary)
  bucketed counters. Powers the visibility meter component.

- ``POST /reset-session`` zeros the session counter by stamping a fresh
  ``session_started_at`` timestamp into the chronicler settings file.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from chronicler.api.dependencies import (
    get_campaign_including_archived,
    get_session_including_archived,
)
from chronicler.cost import add_closing_chronicle_tokens, compute_generation_cost
from chronicler.db.registry import Campaign
from chronicler.db.repository import aggregate_campaign_tokens
from chronicler.settings_store import load_settings, update_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns/{name}", tags=["cost"])

DEFAULT_TARGET_LIFETIME_INPUT_TOKENS = 1_000_000
SETTINGS_KEY_TARGET = "target_lifetime_input_tokens"
SETTINGS_KEY_SESSION_STARTED_AT = "session_started_at"
# ck3_chronicler-cs1o: monthly USD soft target. Default $100 — the Team
# Premium per-seat programmatic credit the default claude-code transport
# burns (post-2026-06-15). Settings-tunable ($200 for Max 20x, or
# whatever real-dollar budget an anthropic-backend user wants watched).
# Soft bands only — generation is never blocked (ma96 philosophy).
DEFAULT_TARGET_MONTHLY_USD = 100.0
SETTINGS_KEY_TARGET_MONTHLY_USD = "target_monthly_usd"


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class EndSessionResponse(BaseModel):
    """ck3_chronicler-0224: response from POST /cost/end-session.

    Returns immediately with ``tracked_considered=0``. The consolidation
    sweep that this endpoint formerly triggered has been removed; the
    endpoint is retained for API back-compat only.
    """

    campaign_id: str
    tracked_considered: int


class TokenBucket(BaseModel):
    """Two-number summary used in the dual visibility meter."""

    input: int
    output: int


class CostSessionSummary(BaseModel):
    """ck3_chronicler-0224: dual-meter response from GET /cost/session-summary.

    ``lifetime`` sums all biography tokens persisted for the campaign
    (all-time). ``session`` filters to rows whose ``generated_at``
    is at or after ``since`` — the user controls that boundary via
    ``POST /cost/reset-session``. ``target_lifetime_input`` is the soft
    target (default 1_000_000) the UI compares the lifetime input bar
    against; banners trigger at 75/100/150%, OS notification at 100%.
    No auto-pause — these numbers are diagnostic only.
    """

    lifetime: TokenBucket
    session: TokenBucket
    since: str | None
    target_lifetime_input: int
    # ck3_chronicler-cs1o: USD revival. ``month_usd`` is the current
    # calendar month's spend — the number the meter compares against
    # ``target_monthly_usd`` (default $100, the Team Premium per-seat
    # programmatic credit; the pool refreshes monthly and unused credit
    # expires). Same 75/100/150% band semantics as the token target.
    lifetime_usd: float
    session_usd: float
    month_usd: float
    target_monthly_usd: float


class CostSessionResetResponse(BaseModel):
    """ck3_chronicler-0224: response from POST /cost/reset-session.

    The session counter resets by writing a fresh ``session_started_at``
    timestamp to the chronicler settings file. The lifetime number is
    unaffected.
    """

    since: str


class CostBucket(BaseModel):
    """One time-window's token usage + USD (ck3_chronicler-j7z).

    ck3_chronicler-tbrm.6 dropped the per-token USD cost when claude
    --print rode the flat subscription; ck3_chronicler-cs1o revived it —
    after the 2026-06-15 billing split the claude-code transport bills a
    metered monthly credit pool and the anthropic transport bills the
    API key, so dollars are meaningful again. ``usd`` prefers per-row
    persisted cost figures and prices legacy rows via the rate card's
    summed-input approximation.
    """

    input_tokens: int
    output_tokens: int
    usd: float


class CostSummaryResponse(BaseModel):
    """Cost-summary endpoint payload (ck3_chronicler-j7z).

    ``this_campaign`` is all-time; ``this_month`` is the current
    calendar month UTC. tbrm.6 dropped the budget_cap_usd field along
    with the USD column itself.
    """

    this_campaign: CostBucket
    this_month: CostBucket


def _resolve_session_since() -> str | None:
    """Return the persisted session-started timestamp, or None if never reset.

    None means "lifetime == session" for the meter. The user kicks off a
    counter by hitting ``POST /reset-session`` once at the start of their
    play session; subsequent GETs filter from that timestamp."""
    raw = load_settings().get(SETTINGS_KEY_SESSION_STARTED_AT)
    return raw if isinstance(raw, str) and raw else None


def _resolve_target_lifetime() -> int:
    raw = load_settings().get(SETTINGS_KEY_TARGET)
    if isinstance(raw, int) and raw > 0:
        return raw
    return DEFAULT_TARGET_LIFETIME_INPUT_TOKENS


def _resolve_target_monthly_usd() -> float:
    raw = load_settings().get(SETTINGS_KEY_TARGET_MONTHLY_USD)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return DEFAULT_TARGET_MONTHLY_USD


def _sum_token_buckets(
    by_provider: dict[str, dict[str, float]],
) -> TokenBucket:
    """Collapse the per-provider aggregate to a single (input, output) pair."""
    total_in = 0
    total_out = 0
    for bucket in by_provider.values():
        total_in += int(bucket.get("input", 0))
        total_out += int(bucket.get("output", 0))
    return TokenBucket(input=total_in, output=total_out)


def sum_bucket_usd(by_provider: dict[str, dict[str, float]]) -> float:
    """ck3_chronicler-cs1o: USD for a per-provider aggregate.

    Per provider: the persisted per-row cost sum (transport's own
    figures), plus the rate-card price of the rows that recorded no
    cost (legacy rows — no cache breakdown either, so they bill the
    documented summed-input approximation via
    :func:`chronicler.cost.compute_generation_cost`).
    """
    total = 0.0
    for provider, bucket in by_provider.items():
        total += float(bucket.get("usd_persisted", 0.0))
        uncosted_input = int(bucket.get("uncosted_input", 0))
        uncosted_output = int(bucket.get("uncosted_output", 0))
        if uncosted_input or uncosted_output:
            total += compute_generation_cost(
                provider,
                input_tokens=uncosted_input,
                cache_read_tokens=int(bucket.get("uncosted_read", 0)) or None,
                cache_write_tokens=int(bucket.get("uncosted_write", 0)) or None,
                output_tokens=uncosted_output,
            )
    return total


@router.post(
    "/cost/end-session",
    response_model=EndSessionResponse,
    status_code=202,
)
def end_session(
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> EndSessionResponse:
    """ck3_chronicler-0224: session-boundary marker.

    Memory consolidation sweep removed (plan: cozy-coalescing-shannon).
    Retained for API compatibility; returns 0 tracked_considered.
    """
    log.info(
        "end-session called for campaign %s (consolidation sweep removed)",
        campaign.id,
    )
    return EndSessionResponse(campaign_id=campaign.id, tracked_considered=0)


@router.get(
    "/cost/session-summary",
    response_model=CostSessionSummary,
)
def get_session_summary(
    campaign: Campaign = Depends(get_campaign_including_archived),
    session=Depends(get_session_including_archived),
) -> CostSessionSummary:
    """ck3_chronicler-0224: dual-meter cost snapshot for the campaign.

    Returns lifetime + session buckets. Lifetime sums everything ever;
    session filters to rows whose ``generated_at >= session_started_at``
    where the latter is whatever the user last stamped via
    ``POST /cost/reset-session`` (or ``None`` when never reset — meter
    treats session as identical to lifetime in that case).

    Hard-codes ``target_lifetime_input`` to the persisted setting
    (default 1_000_000). The UI compares the lifetime input number to
    that target to decide which banner colour to show (75/100/150%).
    """
    since = _resolve_session_since()
    target = _resolve_target_lifetime()

    lifetime_by_provider = aggregate_campaign_tokens(session)
    # ck3_chronicler-li0z: closing chronicle lives on the registry row, so the
    # Biography aggregate misses it — fold it in for both buckets.
    add_closing_chronicle_tokens(lifetime_by_provider, campaign)
    lifetime = _sum_token_buckets(lifetime_by_provider)
    lifetime_usd = sum_bucket_usd(lifetime_by_provider)

    if since is not None:
        session_by_provider = aggregate_campaign_tokens(session, since=since)
        add_closing_chronicle_tokens(session_by_provider, campaign, since=since)
        session_bucket = _sum_token_buckets(session_by_provider)
        session_usd = sum_bucket_usd(session_by_provider)
    else:
        # No reset yet — session counter mirrors lifetime so the meter
        # has SOMETHING to show on first launch.
        session_bucket = lifetime
        session_usd = lifetime_usd

    # ck3_chronicler-cs1o: calendar-month USD — what the meter compares
    # against the monthly soft target (the programmatic credit pool
    # refreshes monthly and unused credit expires).
    month_prefix = datetime.now(UTC).strftime("%Y-%m")
    month_by_provider = aggregate_campaign_tokens(session, month_prefix=month_prefix)
    add_closing_chronicle_tokens(month_by_provider, campaign, month_prefix=month_prefix)
    month_usd = sum_bucket_usd(month_by_provider)

    return CostSessionSummary(
        lifetime=lifetime,
        session=session_bucket,
        since=since,
        target_lifetime_input=target,
        lifetime_usd=round(lifetime_usd, 6),
        session_usd=round(session_usd, 6),
        month_usd=round(month_usd, 6),
        target_monthly_usd=_resolve_target_monthly_usd(),
    )


@router.post(
    "/cost/reset-session",
    response_model=CostSessionResetResponse,
    status_code=200,
)
def reset_session_counter(
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> CostSessionResetResponse:
    """ck3_chronicler-0224: zero the session counter.

    Writes the current UTC ISO timestamp to ``session_started_at`` in
    the chronicler settings file. Subsequent ``GET /session-summary``
    calls filter their token aggregation to rows generated at or after
    this timestamp.

    Lifetime numbers are unaffected. The reset is global (single user,
    single settings file) — resetting for any campaign resets for all,
    matching the "this play session across all campaigns" UX
    expectation.
    """
    now = datetime.now(UTC).isoformat()
    update_settings({SETTINGS_KEY_SESSION_STARTED_AT: now})
    log.info(
        "session counter reset via /cost/reset-session (campaign=%s, since=%s)",
        campaign.id,
        now,
    )
    return CostSessionResetResponse(since=now)


def _to_bucket(by_provider: dict[str, dict[str, float]]) -> CostBucket:
    """Sum a per-provider token map into a single CostBucket (with USD).

    ck3_chronicler-cs1o revived the USD attribution tbrm.6 dropped —
    after the 2026-06-15 billing split, claude --print bills a metered
    monthly credit pool and the anthropic transport bills the API key,
    so dollars are meaningful again. Per-row persisted cost is summed
    where present; uncosted (legacy) rows price via the rate card's
    summed-input approximation.

    ck3_chronicler-27ov.47 (M-A5): moved here from settings.py and folded
    onto :func:`_sum_token_buckets` + :func:`sum_bucket_usd`, deleting the
    duplicated summing loop + the settings->cost function-level import.
    """
    tokens = _sum_token_buckets(by_provider)
    return CostBucket(
        input_tokens=tokens.input,
        output_tokens=tokens.output,
        usd=round(sum_bucket_usd(by_provider), 6),
    )


@router.get("/cost-summary", response_model=CostSummaryResponse)
def get_cost_summary(
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> CostSummaryResponse:
    """Token throughput for this campaign.

    ``this_campaign`` is the all-time aggregate; ``this_month`` filters
    to ``generated_at LIKE 'YYYY-MM%'`` for the current UTC month. Both
    sum across biographies + memories. NULL token columns count as 0.
    tbrm.6: no USD column — the chronicler is on a Claude Code premium
    subscription so per-call billing is meaningless.
    """
    all_time = aggregate_campaign_tokens(session)
    this_month_prefix = datetime.now(UTC).strftime("%Y-%m")
    monthly = aggregate_campaign_tokens(session, month_prefix=this_month_prefix)
    # ck3_chronicler-li0z: fold in the closing chronicle's registry-stored
    # tokens (not a Biography row, so the aggregate misses it).
    add_closing_chronicle_tokens(all_time, campaign)
    add_closing_chronicle_tokens(monthly, campaign, month_prefix=this_month_prefix)
    return CostSummaryResponse(
        this_campaign=_to_bucket(all_time),
        this_month=_to_bucket(monthly),
    )
