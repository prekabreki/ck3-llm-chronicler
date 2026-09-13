"""Heraldry pipeline status + extraction endpoints (ck3_chronicler-27ov.47 / M-A5).

Split out of routes/settings.py: the Settings heraldry card (status +
fire-and-forget extract) and its SSE progress stream. ``compute_heraldry_status_safe``
lives here and is imported by the settings first-run wizard.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chronicler.api.events import EventBus
from chronicler.api.sse import event_stream, sse_response
from chronicler.config import (
    resolve_ck3_install_dir,
)
from chronicler.db.registry import get_data_dir
from chronicler.heraldry.extractor import extract_assets

log = logging.getLogger(__name__)

heraldry_router = APIRouter(prefix="/api/settings", tags=["settings"])
heraldry_sse_router = APIRouter(prefix="/api/sse", tags=["sse"])

# ck3_chronicler-ljza: hold strong references to fire-and-forget tasks.
_background_tasks: set[asyncio.Task] = set()


# --- ck3_chronicler-a3jc (f9w.2): Heraldry pipeline Settings card ---
# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class HeraldryStatusResponse(BaseModel):
    """Heraldry pipeline status for the Settings card (ck3_chronicler-a3jc).

    ``extracted`` is True when ``<data>/heraldry/manifest.json`` exists
    and parses. ``palette_colors`` / ``patterns_count`` / ``emblems_count``
    come from counting files on disk (or 0 when not extracted).
    ``ck3_install_dir_resolved`` and ``ck3_install_dir_exists`` mirror
    the f9w.1 paths panel — the Settings card disables the Run-extract
    button when ck3_install_dir doesn't resolve.
    ``is_stale`` is True when the source (CK3's coat-of-arms dir) has a
    later mtime than the recorded ``last_extraction_at`` — a soft hint
    that re-extract is in order after a CK3 patch.
    """

    extracted: bool
    last_extraction_at: str | None
    palette_colors: int
    patterns_count: int
    emblems_count: int
    ck3_install_dir_resolved: str | None
    ck3_install_dir_exists: bool
    is_stale: bool


class HeraldryExtractStarted(BaseModel):
    """Returned from POST /api/settings/heraldry/extract.

    Mirrors the importer's start-response: gives the client an SSE URL
    to subscribe to for progress + completion events.
    """

    extract_id: str
    sse_url: str


class HeraldryExtractRequest(BaseModel):
    """POST body for /api/settings/heraldry/extract."""

    force: bool = False


def _heraldry_data_dir() -> Path:
    return get_data_dir() / "heraldry"


@heraldry_router.get("/heraldry", response_model=HeraldryStatusResponse)
def get_heraldry_status() -> HeraldryStatusResponse:
    """Snapshot of the heraldry-extraction state for the Settings card.

    The card reads this on mount + after every extract completes. Stale
    detection compares the source CoA dir's mtime to the manifest's
    recorded ``extracted_at`` so a CK3 patch flips ``is_stale`` to True.
    """
    from chronicler.heraldry.extractor import compute_heraldry_status

    install = resolve_ck3_install_dir()
    install_dir = install.value if install.exists else None
    status = compute_heraldry_status(_heraldry_data_dir(), install_dir)

    return HeraldryStatusResponse(
        extracted=status.extracted,
        last_extraction_at=status.last_extraction_at,
        palette_colors=status.palette_colors,
        patterns_count=status.patterns_count,
        emblems_count=status.emblems_count,
        ck3_install_dir_resolved=str(install.value) if install.value is not None else None,
        ck3_install_dir_exists=install.exists,
        is_stale=status.is_stale,
    )


# audit F-44 / ck3_chronicler-w3wk: Pydantic envelopes for the
# /api/sse/heraldry-extract/{extract_id} channel. Each frame is a
# discriminated-union by ``stage`` so the FE can switch on stage
# without optional-field gymnastics. The dispatched models live next
# to the route that emits them so the wire shape and the publisher
# stay co-located.
class _HeraldryStartedFrame(BaseModel):
    extract_id: str
    stage: Literal["started"]
    force: bool


class _HeraldryProgressFrame(BaseModel):
    extract_id: str
    stage: Literal["progress"]
    group: str
    current: int
    total: int


class _HeraldryDoneFrame(BaseModel):
    extract_id: str
    stage: Literal["done"]
    palette_colors: int
    patterns: int
    emblems: int
    skipped_designer: int


class _HeraldryErrorFrame(BaseModel):
    extract_id: str
    stage: Literal["error"]
    message: str


HeraldryProgressFrame = (
    _HeraldryStartedFrame | _HeraldryProgressFrame | _HeraldryDoneFrame | _HeraldryErrorFrame
)


def _publish_heraldry(
    bus: EventBus,
    extract_id: str,
    frame: HeraldryProgressFrame,
) -> None:
    bus.publish(f"heraldry:{extract_id}", frame.model_dump())


@heraldry_router.post(
    "/heraldry/extract",
    response_model=HeraldryExtractStarted,
    status_code=202,
)
async def start_heraldry_extract(
    request: Request,
    body: HeraldryExtractRequest,
) -> HeraldryExtractStarted:
    """Kick off heraldry extraction in a background thread.

    Returns 202 with the SSE URL the client should subscribe to for
    progress events. 400 when the CK3 install dir doesn't resolve —
    the user must set it via the f9w.1 paths panel first.
    """
    install = resolve_ck3_install_dir()
    if not install.exists or install.value is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "CK3 install dir is not configured or doesn't exist; "
                "set ck3_install_dir via Settings → Paths first"
            ),
        )

    install_dir = install.value
    output_dir = _heraldry_data_dir()
    bus: EventBus = request.app.state.event_bus
    extract_id = str(uuid.uuid4())

    async def _runner() -> None:
        loop = asyncio.get_running_loop()

        def _progress(group: str, current: int, total: int) -> None:
            _publish_heraldry(
                bus,
                extract_id,
                _HeraldryProgressFrame(
                    extract_id=extract_id,
                    stage="progress",
                    group=group,
                    current=current,
                    total=total,
                ),
            )

        _publish_heraldry(
            bus,
            extract_id,
            _HeraldryStartedFrame(extract_id=extract_id, stage="started", force=body.force),
        )

        try:
            summary = await loop.run_in_executor(
                None,
                lambda: extract_assets(
                    install_dir,
                    output_dir,
                    force=body.force,
                    progress=_progress,
                ),
            )
        except Exception as e:
            log.exception("heraldry extract failed for extract_id=%s", extract_id)
            _publish_heraldry(
                bus,
                extract_id,
                _HeraldryErrorFrame(
                    extract_id=extract_id,
                    stage="error",
                    message=f"{type(e).__name__}: {e}",
                ),
            )
            return

        _publish_heraldry(
            bus,
            extract_id,
            _HeraldryDoneFrame(
                extract_id=extract_id,
                stage="done",
                palette_colors=summary.palette_colors,
                patterns=summary.patterns_extracted,
                emblems=summary.emblems_extracted,
                skipped_designer=summary.skipped_designer,
            ),
        )

    task = asyncio.create_task(_runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return HeraldryExtractStarted(
        extract_id=extract_id,
        sse_url=f"/api/sse/heraldry-extract/{extract_id}",
    )


@heraldry_sse_router.get("/heraldry-extract/{extract_id}")
async def stream_heraldry_extract(request: Request, extract_id: str) -> StreamingResponse:
    """SSE stream of heraldry-extract progress for one in-flight job.

    Channel: ``heraldry:<id>`` on the same EventBus the importer uses.
    Connection stays open until the client disconnects; consumers
    should close after seeing stage='done' or stage='error'.
    """
    bus: EventBus = request.app.state.event_bus
    return sse_response(event_stream(request, bus, f"heraldry:{extract_id}"))


def compute_heraldry_status_safe():  # type: ignore[no-untyped-def]
    """Wrapper around compute_heraldry_status that handles the nominal
    case (paths probe miss → install_dir=None) without raising."""
    from chronicler.heraldry.extractor import compute_heraldry_status

    install = resolve_ck3_install_dir()
    install_dir = install.value if install.exists else None
    return compute_heraldry_status(_heraldry_data_dir(), install_dir)
