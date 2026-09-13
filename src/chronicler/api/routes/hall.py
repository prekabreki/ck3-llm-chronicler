"""Hall of Fame route — ck3_chronicler-467k.

Single endpoint backing the cross-campaign dynasty gallery on the
frontend. The FE :func:`useHallOfFame` hook calls this and renders one
card per :class:`DynastyRollup` row.

The aggregator under :mod:`chronicler.db.hall` walks the registry,
groups by ``(playthrough_id, dynasty_name)``, and pulls per-campaign
counts + the player's coa_json from each per-campaign DB. We pass the
EngineCache through so the aggregator reuses the process-wide engine
pool — a fresh aggregation on a 12-campaign install is dominated by
the COUNT(*) queries inside ``aggregate_campaign_counts``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from chronicler.api.dependencies import EngineCache, get_engine_cache
from chronicler.api.serializers import parse_coa_json
from chronicler.db.hall import DynastyRollup, aggregate_hall_of_fame

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["hall-of-fame"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
# ck3_chronicler-467k: Hall of Fame cross-DB rollups. One row per
# (playthrough_id, dynasty_name) tuple aggregated from every registered
# campaign. The wire shape is 1:1 with what the FE useHallOfFame() hook
# already consumes from fixtures — switching the queryFn to the real
# endpoint is the FE side of this change.
class DynastyRollupResponse(BaseModel):
    """One Hall of Fame card's worth of rolled-up dynasty data."""

    id: str
    playthrough_id: str | None
    dynasty_name: str
    span_label: str
    span_end_label: str | None
    span_days: int | None
    last_event_iso: str | None
    campaigns_count: int
    tracked_count: int
    biographies_count: int
    blurb: str | None
    is_active: bool
    sealed_at_label: str | None
    primary_campaign_name: str
    coa_json: dict[str, Any] | None
    heraldry_seed: str


class HallOfFameResponse(BaseModel):
    """Top-level Hall of Fame payload."""

    rollups: list[DynastyRollupResponse]


@router.get("/hall-of-fame", response_model=HallOfFameResponse)
def get_hall_of_fame(
    request: Request,
    cache: EngineCache = Depends(get_engine_cache),
) -> HallOfFameResponse:
    """Return one rollup per dynasty across every registered campaign.

    Empty when the registry has no campaigns. Order is whatever the
    aggregator returns (insertion order of the group dict, which is
    grouped-by-recency); the FE applies its own sort chip selection
    over this set.
    """
    rollups = aggregate_hall_of_fame(
        registry=cache.registry_path,
        factory_for=cache.factory_for,
    )
    return HallOfFameResponse(
        rollups=[_to_response(r) for r in rollups],
    )


def _to_response(r: DynastyRollup) -> DynastyRollupResponse:
    """Wire-format transform — coa_json is stored as a string on the
    Character row (JSON text); the FE consumes it as a parsed object so
    we parse it once at the route boundary. Malformed JSON drops
    to None, identical to the Library card's defensive parse."""
    coa = parse_coa_json(r.coa_json)
    return DynastyRollupResponse(
        id=r.id,
        playthrough_id=r.playthrough_id,
        dynasty_name=r.dynasty_name,
        span_label=r.span_label,
        span_end_label=r.span_end_label,
        span_days=r.span_days,
        last_event_iso=r.last_event_iso,
        campaigns_count=r.campaigns_count,
        tracked_count=r.tracked_count,
        biographies_count=r.biographies_count,
        blurb=r.blurb,
        is_active=r.is_active,
        sealed_at_label=r.sealed_at_label,
        primary_campaign_name=r.primary_campaign_name,
        coa_json=coa,
        heraldry_seed=r.heraldry_seed,
    )
