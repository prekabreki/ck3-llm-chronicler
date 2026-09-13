"""Cross-campaign search endpoint (ck3_chronicler-b2y).

GET /api/search?q=...&scope=...&campaign=...

- ``q`` is the FTS5 query string. Falls back to LIKE when a campaign
  DB doesn't have FTS5 (older DBs from before the migration, or
  SQLite builds without FTS5).
- ``scope`` accepts ``all`` (default) or any subset of
  ``biographies,characters,events`` joined by commas.
- ``campaign`` selects a single campaign by name; the default
  (``all``) iterates every non-archived campaign in the registry and
  aggregates results.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from chronicler.api.dependencies import get_engine_cache
from chronicler.api.serializers import parse_coa_json
from chronicler.db.registry import Campaign, get_campaign_by_name, list_campaigns
from chronicler.db.search import (
    SearchHit,
    SearchKind,
    search_all_scopes,
)

router = APIRouter(prefix="/api/search", tags=["search"])

_VALID_SCOPES: dict[str, SearchKind] = {
    "biographies": "biography",
    "characters": "character",
    "events": "event",
}


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class SearchHitResponse(BaseModel):
    """One cross-campaign search hit (ck3_chronicler-b2y).

    ck3_chronicler-3yd9 (2026-05-08): adds ``coa_json`` so the SearchPage
    result card can render the same real-CoA path as Codex/Tracked
    (slice 1) instead of the procedural-by-name shield. Populated by the
    route's per-campaign batch resolver — one extra SELECT per campaign,
    not per row. ``None`` for hits without a character_id (campaign-level
    biographies, future kinds), or when no CoA has been persisted yet
    (untracked character that save-tail hasn't refreshed).
    """

    campaign_name: str
    kind: str  # "biography" | "character" | "event"
    row_id: int
    snippet: str
    character_id: int | None
    rank: float | None
    coa_json: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    """Aggregated cross-campaign search results.

    ``hits`` is the flat list across all queried scopes + campaigns;
    ``by_campaign`` is the same data partitioned for the UI's grouped
    view. Both are sorted by FTS rank when available, then by row_id
    descending so newest first.
    """

    query: str
    hits: list[SearchHitResponse]
    by_campaign: dict[str, list[SearchHitResponse]]


def _parse_scope(scope: str) -> set[SearchKind]:
    if scope == "all":
        return set(_VALID_SCOPES.values())
    out: set[SearchKind] = set()
    for token in scope.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token not in _VALID_SCOPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown scope token: {token!r}; valid: all, {', '.join(_VALID_SCOPES.keys())}"
                ),
            )
        out.add(_VALID_SCOPES[token])
    if not out:
        raise HTTPException(status_code=400, detail="scope resolved to empty set")
    return out


def _hit_to_response(
    campaign_name: str,
    hit: SearchHit,
    coa_by_id: dict[int, dict[str, Any] | None],
) -> SearchHitResponse:
    coa = coa_by_id.get(hit.character_id) if hit.character_id is not None else None
    return SearchHitResponse(
        campaign_name=campaign_name,
        kind=hit.kind,
        row_id=hit.row_id,
        snippet=hit.snippet,
        character_id=hit.character_id,
        rank=hit.rank,
        coa_json=coa,
    )


def _resolve_coa_batch(session, character_ids: set[int]) -> dict[int, dict[str, Any] | None]:
    """Bulk-load coa_json for the given character_ids in one SELECT.

    ck3_chronicler-3yd9 (2026-05-08): the SearchPage card needs the same
    real-CoA path as Codex/Tracked. Doing it row-by-row would mean N
    queries per result page; instead, one IN-clause per campaign session
    fetches every shield in a single round-trip. Malformed JSON falls
    through to ``None`` so the FE renders the procedural fallback rather
    than crashing the page.
    """
    if not character_ids:
        return {}
    from sqlalchemy import bindparam

    stmt = text("SELECT ck3_id, coa_json FROM characters WHERE ck3_id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    rows = session.execute(stmt, {"ids": list(character_ids)}).all()
    out: dict[int, dict[str, Any] | None] = {}
    for r in rows:
        out[int(r.ck3_id)] = parse_coa_json(r.coa_json)
    return out


def _sort_key(hit: SearchHitResponse) -> tuple[float, int]:
    """Lower rank wins (FTS5 convention); ties broken by row_id desc."""
    rank = hit.rank if hit.rank is not None else 1e9
    return (rank, -hit.row_id)


def _search_one_campaign(
    *,
    factory,
    campaign_name: str,
    q: str,
    scopes: set[SearchKind],
    limit_per_scope: int,
) -> tuple[str, list[SearchHitResponse]]:
    """Run the FTS5/LIKE search for a single campaign, returning the
    (name, hits) pair. Pure-sync — runs inside asyncio.to_thread.

    ck3_chronicler-3yd9 (2026-05-08): after the search executes, batch-
    resolve coa_json for every distinct character_id touched by the hits
    in a single follow-up SELECT, so result cards can render real CoA
    via the same HeraldryWithFallback path the Codex/Tracked use."""
    with factory() as session:
        hits = list(search_all_scopes(session, q, scopes=scopes, limit_per_scope=limit_per_scope))
        char_ids: set[int] = {h.character_id for h in hits if h.character_id is not None}
        coa_by_id = _resolve_coa_batch(session, char_ids)
        return campaign_name, [_hit_to_response(campaign_name, h, coa_by_id) for h in hits]


@router.get("", response_model=SearchResponse)
async def search(
    request: Request,
    q: str = Query(..., min_length=1),
    scope: str = "all",
    campaign: str = "all",
    limit_per_scope: int = Query(20, ge=1, le=200),
) -> SearchResponse:
    """Run a search across one or all campaigns and selected scopes.

    audit F-22 / ck3_chronicler-l5v8: per-campaign sessions are
    independent SQLite files, so each campaign's FTS5/LIKE pass runs
    on its own thread via asyncio.to_thread + asyncio.gather. The old
    sync def serialized them on the FastAPI threadpool worker — fine
    for one campaign, but cross-campaign search (?campaign=all)
    held the worker for the duration of every campaign's queries.
    """
    scopes = _parse_scope(scope)
    cache = get_engine_cache(request)
    campaigns: list[Campaign]
    if campaign == "all":
        campaigns = list_campaigns(registry=cache.registry_path)
    else:
        c = get_campaign_by_name(campaign, registry=cache.registry_path)
        if c is None:
            raise HTTPException(status_code=404, detail=f"campaign not found: {campaign}")
        campaigns = [c]

    if not campaigns:
        return SearchResponse(query=q, hits=[], by_campaign={})

    factories = {camp.name: cache.factory_for(camp) for camp in campaigns}
    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                _search_one_campaign,
                factory=factories[camp.name],
                campaign_name=camp.name,
                q=q,
                scopes=scopes,
                limit_per_scope=limit_per_scope,
            )
            for camp in campaigns
        )
    )

    hits: list[SearchHitResponse] = []
    by_campaign: dict[str, list[SearchHitResponse]] = {}
    for campaign_name, campaign_hits in results:
        if campaign_hits:
            by_campaign[campaign_name] = sorted(campaign_hits, key=_sort_key)
            hits.extend(campaign_hits)

    return SearchResponse(
        query=q,
        hits=sorted(hits, key=_sort_key),
        by_campaign=by_campaign,
    )
