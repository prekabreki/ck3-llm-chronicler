"""Closing-ceremony routes (ck3_chronicler-z7l).

Two endpoints:

- ``GET /api/campaigns/{name}/closing-chronicle`` — returns the persisted
  chronicle if one exists, 404 otherwise. Cheap (registry read only).
- ``POST /api/campaigns/{name}/complete`` — runs the closing pipeline
  end-to-end: generates via the configured NarrativeProvider, persists
  the body to the campaign row, archives the campaign. Returns the
  generated body.

Both endpoints expose the response via :class:`ClosingChronicleResponse`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from chronicler.api.dependencies import (
    get_campaign,
    get_campaign_including_archived,
    get_engine_cache,
    get_narrative_provider,
)
from chronicler.db.registry import Campaign, archive_campaign, get_campaign_by_id
from chronicler.narrative.closing import generate_closing_chronicle
from chronicler.sync import export_sealed_campaign

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns/{name}", tags=["closing"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class ClosingChronicleResponse(BaseModel):
    """Persisted closing-chronicle body + when it was generated.

    Returned by GET /closing-chronicle and POST /complete (ck3_chronicler-z7l).
    ``generated_at`` is the ISO UTC timestamp of the LLM completion.
    ``archived`` reflects the post-complete state of the campaign — the
    GET endpoint returns the value as currently persisted; the POST
    endpoint always returns archived=True since /complete is what flips
    it.
    """

    campaign_name: str
    body: str
    generated_at: str
    archived: bool


@router.get("/closing-chronicle", response_model=ClosingChronicleResponse | None)
def get_closing_chronicle(
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> ClosingChronicleResponse | None:
    """Return the persisted closing chronicle for a campaign, or ``null``
    when none has been generated yet — the caller renders the
    "complete this campaign" CTA on null.

    F-60: returns 200 + null instead of 404 for the not-yet-sealed
    state. The "no chronicle" case is valid domain state (every
    campaign starts there), not an error condition; surfacing it as a
    4xx pollutes the browser network panel and forces the FE to handle
    a thrown exception for what is really just absence of data.

    ck3_chronicler-fk9r: this endpoint accepts archived campaigns since
    the chronicle's whole reason to exist is being read after sealing.
    """
    if not campaign.closing_chronicle:
        return None
    return ClosingChronicleResponse(
        campaign_name=campaign.name,
        body=campaign.closing_chronicle,
        generated_at=campaign.closing_chronicle_generated_at or "",
        archived=campaign.archived,
    )


@router.post("/complete", response_model=ClosingChronicleResponse)
async def complete_campaign(
    request: Request,
    campaign: Campaign = Depends(get_campaign),
    provider=Depends(get_narrative_provider),
) -> ClosingChronicleResponse:
    """End-of-campaign ceremony.

    1. Run the closing-chronicle generation against the configured
       NarrativeProvider (synthesises every tracked-character biography
       into a 1-2 page meta-narrative).
    2. Persist the chronicle to the campaign row.
    3. Archive the campaign.

    Returns the generated body. 503 if no provider is configured. 502
    if the provider fails (the campaign is NOT archived in that case —
    safe to retry).
    """
    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)
    outcome = await generate_closing_chronicle(
        campaign.id,
        factory=factory,
        provider=provider,
        registry=cache.registry_path,
    )
    if outcome.body is None:
        raise HTTPException(
            status_code=502,
            detail=f"closing-chronicle generation failed: {outcome.error}",
        )
    archive_campaign(campaign.id, registry=cache.registry_path)

    # Re-fetch to get the persisted timestamp + the freshly-flipped
    # archived flag — single source of truth lives in the registry.
    fresh = get_campaign_by_id(campaign.id, registry=cache.registry_path)
    if fresh is None:
        raise RuntimeError(
            f"closing-chronicle invariant: campaign {campaign.id} disappeared "
            "between archive_campaign and re-fetch"
        )
    # ck3_chronicler-txuo: mirror the just-sealed campaign into the
    # repo so the user's other machines pick it up on next git pull.
    # Best-effort — every sub-step (snapshot, commit, push) logs and
    # carries on if it fails. The seal itself has already succeeded
    # at this point so we never propagate sync errors back to the
    # caller; chronicler still serves the closing chronicle locally.
    #
    # ck3_chronicler-27ov.24 (audit M-A2): export does a synchronous
    # git add/commit/push (network — can block for seconds). Run it off the
    # uvicorn loop so SSE heartbeats (tuned to 2s) don't starve during a seal.
    export_result = await asyncio.to_thread(export_sealed_campaign, fresh)
    if not export_result.pushed:
        # Info-level only when the export simply landed nothing new
        # (e.g. backfill of an already-pushed snapshot). Anything
        # past snapshot-write that doesn't push warrants a warning so
        # the operator can see "1 archived campaign pending push".
        if export_result.snapshot_written:
            log.warning(
                "txuo: archive export incomplete for %s: %s",
                fresh.name,
                export_result.message,
            )
        else:
            log.info(
                "txuo: archive export skipped for %s: %s",
                fresh.name,
                export_result.message,
            )
    return ClosingChronicleResponse(
        campaign_name=fresh.name,
        body=outcome.body,
        generated_at=outcome.generated_at or "",
        archived=fresh.archived,
    )
