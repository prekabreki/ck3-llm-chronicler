"""Save-import HTTP endpoint with SSE progress (ck3_chronicler-u3m).

Two endpoints:

- ``POST /api/campaigns/{name}/import-save`` accepts a server-side save
  path (the import modal in the v0.7 portal already shows a path
  picker — no upload required), kicks off the import in a background
  task, and returns the generated ``import_id`` immediately. The
  client then opens the SSE endpoint below to follow progress.
- ``GET /api/sse/import/{import_id}`` subscribes to the EventBus on
  the per-import channel ``import:<id>`` and streams every published
  ImportProgress event (encoded as a single SSE ``data:`` frame)
  until the import completes or the client disconnects.

Re-using the same EventBus the ingest pipeline already publishes to
keeps the wire format uniform — the frontend has one SSE handler.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chronicler.api.dependencies import get_campaign, get_engine_cache
from chronicler.api.events import EventBus
from chronicler.api.sse import event_stream, sse_response
from chronicler.db.registry import Campaign
from chronicler.save.importer import ImportProgress, ImportStage, import_save

router = APIRouter(prefix="/api/campaigns/{name}", tags=["importer"])
sse_router = APIRouter(prefix="/api/sse", tags=["sse"])

log = logging.getLogger(__name__)

# ck3_chronicler-ljza: hold strong references to fire-and-forget tasks
# so the GC can't reap them mid-execution. Same pattern as
# narrative/scheduler.py:_tasks.
_background_tasks: set[asyncio.Task] = set()


class ImportRequest(BaseModel):
    save_path: str
    force_reset_playthrough: bool = False


class ImportStarted(BaseModel):
    """Returned from POST /import-save — points the client at the SSE channel."""

    import_id: str
    campaign_name: str
    sse_url: str


class ImportProgressFrame(BaseModel):
    """Pydantic envelope for a single /api/sse/import/{import_id} frame
    (audit F-44 / ck3_chronicler-w3wk).

    Mirrors the wire shape the FE's ImportModal consumes; the
    ImportStage literal is the canonical source for both sides — F-01
    was caused by a divergence between this enum and a hand-written
    FE list. Keeping them paired prevents the "import stuck at 0/N
    forever" regression from re-occurring.
    """

    import_id: str
    stage: ImportStage  # 'read_save' | 'parse_history' | ... | 'done' | 'error'
    fraction: float
    message: str


def _publish_progress(bus: EventBus, import_id: str, progress: ImportProgress) -> None:
    frame = ImportProgressFrame(
        import_id=import_id,
        stage=progress.stage,
        fraction=progress.fraction,
        message=progress.message,
    )
    bus.publish(f"import:{import_id}", frame.model_dump())


@router.post("/import-save", response_model=ImportStarted, status_code=202)
async def start_import(
    request: Request,
    body: ImportRequest,
    campaign: Campaign = Depends(get_campaign),
) -> ImportStarted:
    """Kick off a save import in a background task.

    Returns 202 with the ``import_id`` to subscribe to. 400 when the
    server-side path doesn't exist; 404 (via get_campaign) when the
    campaign is unknown.
    """
    save_path = Path(body.save_path)
    if not save_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"save file not found on server: {save_path}",
        )

    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)
    bus: EventBus = request.app.state.event_bus
    import_id = str(uuid.uuid4())

    async def _runner() -> None:
        loop = asyncio.get_running_loop()

        def _cb(progress: ImportProgress) -> None:
            # Publish from whatever thread the import_save loop is
            # running on. The bus is thread-safe enough for this — the
            # subscriber-set discard is racy in theory but a no-op in
            # practice (we'd see a stale queue, not a crash). For the
            # synchronous import_save path everything happens on the
            # event loop's thread anyway.
            _publish_progress(bus, import_id, progress)

        try:
            # import_save is sync (DB work + LLM-free); offload to a
            # thread so we don't pin the event loop.
            await loop.run_in_executor(
                None,
                lambda: import_save(
                    save_path,
                    factory=factory,
                    progress=_cb,
                    force_reset_playthrough=body.force_reset_playthrough,
                ),
            )
        except Exception as e:
            # Any unhandled exception still publishes an error event so
            # the SSE client sees a final state.
            log.exception("background import failed for import_id=%s", import_id)
            _publish_progress(
                bus,
                import_id,
                ImportProgress(stage="error", fraction=1.0, message=f"{type(e).__name__}: {e}"),
            )

    task = asyncio.create_task(_runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return ImportStarted(
        import_id=import_id,
        campaign_name=campaign.name,
        sse_url=f"/api/sse/import/{import_id}",
    )


@sse_router.get("/import/{import_id}")
async def stream_import(request: Request, import_id: str) -> StreamingResponse:
    """SSE stream of ImportProgress events for one in-flight import.

    Channel: ``import:<id>`` on the same EventBus the ingest pipeline
    uses. The connection stays open until the client disconnects;
    progress consumers should close after seeing stage='done' or
    stage='error'.
    """
    bus: EventBus = request.app.state.event_bus
    return sse_response(event_stream(request, bus, f"import:{import_id}"))
