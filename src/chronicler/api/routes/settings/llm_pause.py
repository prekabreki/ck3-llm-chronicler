"""Global LLM pause endpoints (ck3_chronicler-gx7b)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chronicler.settings_store import load_settings, update_settings

log = logging.getLogger(__name__)

router = APIRouter()


class LLMPauseDrainReport(BaseModel):
    """ck3_chronicler-gx7b: counts the unpause handler scheduled across
    all open campaigns when transitioning paused → unpaused."""

    biographies_scheduled: int


class LLMPauseResponse(BaseModel):
    """ck3_chronicler-gx7b: global LLM pause state. While ``paused``,
    save-tail keeps ingesting but no biographies auto-fire from event
    triggers. ``paused_at`` is the ISO timestamp the
    user flipped the toggle ON (None when not paused) so the FE can render
    'paused for 2h 14m'. ``last_drain`` carries the counts from the most
    recent unpause-drain — None if there hasn't been one this session.
    """

    paused: bool
    paused_at: str | None
    last_drain: LLMPauseDrainReport | None = None


class LLMPauseUpdate(BaseModel):
    """PUT body for toggling pause. ``paused=true`` sets the flag (and
    writes paused_at = now). ``paused=false`` clears both and triggers
    the drain across every open campaign before returning."""

    paused: bool


def _llm_pause_state() -> tuple[bool, str | None]:
    """Read the current pause state from settings.json."""
    settings = load_settings()
    paused = bool(settings.get("llm_paused", False))
    paused_at = settings.get("llm_paused_at") if paused else None
    return paused, paused_at if isinstance(paused_at, str) else None


@router.get("/llm-pause", response_model=LLMPauseResponse)
def get_llm_pause(request: Request) -> LLMPauseResponse:
    """ck3_chronicler-gx7b: current global LLM pause state.

    Returns ``paused=True`` when the user has flipped the Settings toggle
    ON; in that state, save-tail keeps ingesting but no biographies
    auto-fire. ``paused_at`` is the ISO timestamp of the toggle-ON moment
    so the FE can render "paused for 2h 14m".
    """
    paused, paused_at = _llm_pause_state()
    return LLMPauseResponse(
        paused=paused,
        paused_at=paused_at,
        last_drain=request.app.state.last_drain_report,
    )


@router.put("/llm-pause", response_model=LLMPauseResponse)
async def put_llm_pause(body: LLMPauseUpdate, request: Request) -> LLMPauseResponse:
    """ck3_chronicler-gx7b: flip the global LLM pause toggle.

    paused=True: persist ``llm_paused: true`` + ``llm_paused_at: <now>``;
    invalidate the ingest cache so the next event-tick sees the new state.

    paused=False: clear both keys; invalidate the cache; then iterate
    every non-archived campaign in the registry and call
    :func:`drain_for_campaign` against each. The scheduler's gates
    (tracked / paused-per-char) plus the per-character lock and the
    DB-side dedup keep the drain safe under any races with a
    still-running save-tail tick. (27ov.43 fixed this list — it used
    to name a cooldown gate, a has_active check, and a
    scheduler.resume_for_drain method that never existed.)

    Returns the post-flip state including the drain counts when
    transitioning paused → unpaused.
    """
    from chronicler.api.dependencies import resolve_or_build_scheduler
    from chronicler.db.registry import list_campaigns
    from chronicler.narrative.pause import (
        _reset_llm_paused_cache,
        drain_for_campaign,
    )

    prior_paused, _ = _llm_pause_state()

    if body.paused:
        # Pause: write flag + paused_at. Drain not relevant.
        update_settings(
            {
                "llm_paused": True,
                "llm_paused_at": datetime.now(UTC).isoformat(),
            }
        )
        _reset_llm_paused_cache()
        # Clear the previous drain report so the FE doesn't show stale
        # counts after a re-pause.
        request.app.state.last_drain_report = None
        paused, paused_at = _llm_pause_state()
        return LLMPauseResponse(
            paused=paused,
            paused_at=paused_at,
            last_drain=None,
        )

    # Unpause: clear both keys, invalidate cache, drain across campaigns.
    update_settings({"llm_paused": None, "llm_paused_at": None})
    _reset_llm_paused_cache()

    if not prior_paused:
        # Already unpaused — no drain needed, just confirm state.
        return LLMPauseResponse(paused=False, paused_at=None, last_drain=None)

    # Drain across every open campaign. Skip silently when a campaign
    # can't be scheduler-resolved (no provider configured for that
    # campaign at this moment) — its work will be picked up by the next
    # save-tail tick that picks up the biography work. Best-effort by
    # design.
    cache = request.app.state.engine_cache
    registry_path = getattr(cache, "registry_path", None)
    aggregate_bio = 0
    campaigns = list_campaigns(include_archived=False, registry=registry_path)
    for campaign in campaigns:
        try:
            scheduler = resolve_or_build_scheduler(request, campaign)
        except Exception:
            log.warning(
                "llm-pause drain: skipping campaign %s — could not resolve a scheduler",
                campaign.id,
            )
            continue
        factory = scheduler.factory
        try:
            report = drain_for_campaign(
                factory=factory,
                scheduler=scheduler,
                campaign_id=campaign.id,
                registry_path=registry_path,
            )
        except Exception:
            log.exception(
                "llm-pause drain: campaign %s failed mid-drain; continuing",
                campaign.id,
            )
            continue
        aggregate_bio += report.biographies_scheduled
        log.info(
            "llm-pause drain: campaign %s scheduled %d biographies",
            campaign.id,
            report.biographies_scheduled,
        )

    request.app.state.last_drain_report = LLMPauseDrainReport(
        biographies_scheduled=aggregate_bio,
    )
    return LLMPauseResponse(
        paused=False,
        paused_at=None,
        last_drain=request.app.state.last_drain_report,
    )
