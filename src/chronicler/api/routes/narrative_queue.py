"""Narrative-queue inspection + control endpoints (ck3_chronicler-27ov.47 / M-A5).

Split out of routes/settings.py: the per-campaign narrative queue snapshot,
character throughput stats, and the cancel / regenerate / reorder controls,
plus the scheduler-lookup helpers they share.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chronicler.narrative.queue_state import NarrativeQueueState

narrative_router = APIRouter(prefix="/api/settings", tags=["settings"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class NarrativeQueueItem(BaseModel):
    """One narrative-generation task as exposed by the queue endpoint
    (ck3_chronicler-eev).

    Mirrors :class:`chronicler.narrative.queue_state.QueueItem` over the
    wire so the AppShell activity strip can render queued/active/recent
    rows uniformly. ``error`` is populated only for failed items;
    ``duration_ms`` only for completed/failed.
    """

    item_id: int
    character_id: int
    # ck3_chronicler-27ov.81 (audit L30): first name resolved BE-side at
    # enqueue. The FE used to join character_id against the active
    # campaign's top-250 relevance window, which missed tracked souls on
    # large campaigns and could never name cross-campaign items. Null
    # when the scheduler couldn't resolve it; FE falls back to the id.
    character_name: str | None
    # audit F-58 / ck3_chronicler-hgnb: tightened from `str` to a Literal
    # so the FE doesn't have to narrow on every render.
    kind: Literal["biography"]
    status: Literal["queued", "generating", "completed", "failed"]
    enqueued_at: str
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    error: str | None


class NarrativeQueueResponse(BaseModel):
    """Snapshot of the in-process narrative-generation queue
    (ck3_chronicler-eev).

    Process-wide because there's only one save-tail loop per chronicler
    process. ``avg_duration_ms`` averages completed-only durations and
    is used by the AppShell strip's ETA hint (avg × queue depth).
    """

    queued: list[NarrativeQueueItem]
    active: list[NarrativeQueueItem]
    recent: list[NarrativeQueueItem]
    completed_count: int
    failed_count: int
    avg_duration_ms: int | None


class NarrativeQueueCancelResponse(BaseModel):
    """ck3_chronicler-c5wq: response from DELETE /narrative-queue/{item_id}.

    ``cancelled`` is True when the scheduler found an active/queued task
    and signalled cancellation; False when the item had already finished
    or never existed (the client may be working against a stale snapshot).
    """

    cancelled: bool
    item_id: int


class NarrativeQueueRegenerateResponse(BaseModel):
    """ck3_chronicler-c5wq: response from POST /narrative-queue/{item_id}/regenerate.

    Mirrors :class:`RegenerateBiographyResponse` — the new ``item_id``
    correlates SSE narrative_* frames so the FE can track the regenerated
    task through its lifecycle.
    """

    character_id: int
    kind: Literal["biography"]
    item_id: int | None


class NarrativeQueueReorderRequest(BaseModel):
    """ck3_chronicler-c5wq: request body for PATCH /narrative-queue/reorder.

    ``item_ids`` lists the queued items in the desired dispatch order;
    items not mentioned are appended in current order. Unknown ids are
    silently ignored.
    """

    item_ids: list[int]


class NarrativeQueueReorderResponse(BaseModel):
    """ck3_chronicler-c5wq: response from the reorder endpoint.

    Implementation note: reorder cancels existing queued tasks and
    re-spawns them in the requested order, so item_ids change. The
    returned ``new_item_ids`` are the new dispatch sequence; clients
    should refetch the queue snapshot afterwards.
    """

    new_item_ids: list[int]


class NarrativeCharacterStatsRow(BaseModel):
    """ck3_chronicler-fjln: one per-character lifetime aggregate row.

    Mirrors :class:`chronicler.narrative.queue_state.CharacterStats` over
    the wire. Frontend joins ``character_id`` to the campaign's
    characters list to surface a name beside each row; until that join
    resolves, falling back to "character {id}" matches the existing
    activity-strip pattern.
    """

    character_id: int
    # ck3_chronicler-27ov.81 (audit L30): name stamped BE-side from the
    # QueueItem on each transition, so the queue page no longer joins
    # against the campaign's characters list. Null until first stamped.
    character_name: str | None
    kind: Literal["biography"]
    completed_count: int
    failed_count: int
    median_duration_ms: int | None
    last_success_at: str | None


class NarrativeCharacterStatsResponse(BaseModel):
    """ck3_chronicler-fjln: snapshot of per-character aggregates served
    by the queue page's stats panel.

    Stable-ordered by (character_id, kind) so the UI can render without
    its own sort step. Empty list when the scheduler hasn't completed
    any work yet — frontend uses that to skip rendering the panel."""

    rows: list[NarrativeCharacterStatsRow]


def _all_known_schedulers(request: Request) -> list:
    """ck3_chronicler-c5wq: iterate every scheduler that might own an
    item_id. The narrative queue is process-wide but schedulers are
    per-campaign, so cancel/reorder need to fan out across all of them.

    Order: live save-tail schedulers first (``narrative_schedulers``),
    then lazy-built ones (``_lazy_regen_schedulers``). A given scheduler
    may appear in both dicts during a live-loop teardown race — that's
    benign; the second cancel_item call is a no-op (task already done).
    """
    schedulers: list = []
    live = getattr(request.app.state, "narrative_schedulers", None)
    if isinstance(live, dict):
        schedulers.extend(live.values())
    lazy = getattr(request.app.state, "_lazy_regen_schedulers", None)
    if isinstance(lazy, dict):
        schedulers.extend(lazy.values())
    return schedulers


def _scheduler_for_campaign(request: Request, campaign_uuid: str):
    """Return the scheduler that owns ``campaign_uuid``, or None.

    Both scheduler dicts are keyed by campaign id (ck3_chronicler-m4cn).
    Character ids are campaign-local, so dispatching a regenerate to any
    other campaign's scheduler generates against the wrong DB
    (ck3_chronicler-27ov.4 / audit H10).
    """
    for attr in ("narrative_schedulers", "_lazy_regen_schedulers"):
        d = getattr(request.app.state, attr, None)
        if isinstance(d, dict):
            sched = d.get(campaign_uuid)
            if sched is not None:
                return sched
    return None


def _serialise_queue_item(item) -> NarrativeQueueItem:
    return NarrativeQueueItem(
        item_id=item.item_id,
        character_id=item.character_id,
        character_name=item.character_name,
        kind=item.kind,
        status=item.status,
        enqueued_at=item.enqueued_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
        duration_ms=item.duration_ms,
        error=item.error,
    )


@narrative_router.get(
    "/narrative-queue/character-stats",
    response_model=NarrativeCharacterStatsResponse,
)
def get_narrative_character_stats(
    request: Request,
) -> NarrativeCharacterStatsResponse:
    """ck3_chronicler-fjln: per-character lifetime aggregates for the
    queue page's stats panel.

    Empty rows list when the scheduler hasn't completed any work yet —
    the frontend skips rendering the panel cleanly in that case. Stable-
    ordered by (character_id, kind) so the UI doesn't need its own sort
    step. Stats persist for the chronicler process lifetime; nothing
    survives a restart in v1 (per fjln's wrap-memory risk note)."""
    queue: NarrativeQueueState = request.app.state.narrative_queue
    rows = [
        NarrativeCharacterStatsRow(
            character_id=row.character_id,
            character_name=row.character_name,
            kind=row.kind,
            completed_count=row.completed_count,
            failed_count=row.failed_count,
            median_duration_ms=row.median_duration_ms,
            last_success_at=row.last_success_at,
        )
        for row in queue.character_stats()
    ]
    return NarrativeCharacterStatsResponse(rows=rows)


@narrative_router.get("/narrative-queue", response_model=NarrativeQueueResponse)
def get_narrative_queue(request: Request) -> NarrativeQueueResponse:
    """Process-wide snapshot of the live narrative-generation queue
    (ck3_chronicler-eev).

    Returned shape mirrors :class:`NarrativeQueueState.snapshot` — the
    AppShell activity strip fetches once on mount and refetches on every
    ``narrative_*`` SSE frame to stay in sync with mid-flight changes.
    Empty when no campaign is being tailed (always-present field on
    ``app.state.narrative_queue`` so the route never 404s).
    """
    queue: NarrativeQueueState = request.app.state.narrative_queue
    snap = queue.snapshot()
    return NarrativeQueueResponse(
        queued=[_serialise_queue_item(i) for i in snap.queued],
        active=[_serialise_queue_item(i) for i in snap.active],
        recent=[_serialise_queue_item(i) for i in snap.recent],
        completed_count=snap.completed_count,
        failed_count=snap.failed_count,
        avg_duration_ms=snap.avg_duration_ms,
    )


@narrative_router.delete(
    "/narrative-queue/{item_id}",
    response_model=NarrativeQueueCancelResponse,
)
async def cancel_narrative_queue_item(
    item_id: int, request: Request
) -> NarrativeQueueCancelResponse:
    """ck3_chronicler-c5wq: cancel a queued or in-flight narrative task.

    Returns ``cancelled=False`` (not 404) when the item is already
    completed or unknown — the FE may be working against a stale
    snapshot, and a 404 would make the queue page feel unstable. The
    user can always tell from the next refresh whether the cancel
    landed.

    Cancellation is hard: ``task.cancel()`` propagates a CancelledError
    that kills the underlying ``claude --print`` subprocess (clean
    because output is captured via stdout — no partial files on disk).
    """
    for sched in _all_known_schedulers(request):
        cancel_fn = getattr(sched, "cancel_item", None)
        if cancel_fn is None:
            continue
        cancelled = await cancel_fn(item_id)
        if cancelled:
            return NarrativeQueueCancelResponse(cancelled=True, item_id=item_id)
    return NarrativeQueueCancelResponse(cancelled=False, item_id=item_id)


@narrative_router.post(
    "/narrative-queue/{item_id}/regenerate",
    response_model=NarrativeQueueRegenerateResponse,
    status_code=202,
)
def regenerate_narrative_queue_item(
    item_id: int, request: Request
) -> NarrativeQueueRegenerateResponse:
    """ck3_chronicler-c5wq: re-run a completed or failed narrative task.

    Resolves (character_id, kind) from the recent-completions ring on
    :class:`NarrativeQueueState`, then delegates to the scheduler that owns
    the item's campaign. Cooldown is bypassed inside ``regenerate`` —
    explicit user actions never wait on the cooldown.

    ck3_chronicler-27ov.4 (audit H10): character ids are campaign-local, so
    we MUST dispatch to ``item.campaign_uuid``'s scheduler — picking the first
    scheduler generated against the wrong DB when two campaigns were open.
    Items with no campaign_uuid (older callers / tests) keep the legacy
    first-scheduler behaviour, which is only ever exercised single-campaign.

    Returns 404 when the item is not in the recent ring (the client may
    have a stale snapshot from before the ring rolled the item off).

    Returns 503 when no scheduler can serve this — no NarrativeProvider is
    configured, or the item's campaign has no live/lazy scheduler.
    """
    queue: NarrativeQueueState = request.app.state.narrative_queue
    item = queue.find_recent(item_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"item {item_id} not found in recent completions",
        )
    if item.campaign_uuid is not None:
        sched = _scheduler_for_campaign(request, item.campaign_uuid)
        candidates = [sched] if sched is not None else []
    else:
        candidates = _all_known_schedulers(request)
    for sched in candidates:
        regen_fn = getattr(sched, "regenerate", None)
        if regen_fn is None:
            continue
        # 27ov.43: regenerate() no longer takes kind — the queue is
        # single-kind ("biography"); item.kind survives on the wire shape.
        new_id = regen_fn(item.character_id)
        return NarrativeQueueRegenerateResponse(
            character_id=item.character_id,
            kind=item.kind,
            item_id=new_id,
        )
    raise HTTPException(
        status_code=503,
        detail="no scheduler available to regenerate",
    )


@narrative_router.patch(
    "/narrative-queue/reorder",
    response_model=NarrativeQueueReorderResponse,
)
async def reorder_narrative_queue(
    body: NarrativeQueueReorderRequest, request: Request
) -> NarrativeQueueReorderResponse:
    """ck3_chronicler-c5wq: reprioritise queued narrative tasks.

    Items in ``body.item_ids`` come first in the supplied order; items
    not mentioned are appended in their current order. Unknown / stale
    ids are silently ignored.

    Implementation note: reorder is implemented as cancel-and-respawn
    in :meth:`NarrativeScheduler.reorder_queued` because asyncio.Semaphore
    is FIFO and can't be reordered after the fact. The new item_ids
    returned here differ from the input ids — clients should refetch
    the queue snapshot after this call to update their UI.

    With multiple per-campaign schedulers, every one is called; the
    queue items each scheduler owns are reordered independently. The
    returned ``new_item_ids`` is the concatenation.
    """
    aggregated: list[int] = []
    for sched in _all_known_schedulers(request):
        reorder_fn = getattr(sched, "reorder_queued", None)
        if reorder_fn is None:
            continue
        new_ids = await reorder_fn(body.item_ids)
        aggregated.extend(new_ids)
    return NarrativeQueueReorderResponse(new_item_ids=aggregated)
