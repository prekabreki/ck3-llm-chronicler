"""CK3 debug.log status and rotation (ck3_chronicler-z6jm slice 1)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from chronicler.tailer.log_rotate import (
    archive_and_truncate,
    reset_tail_offsets,
    stat_debug_log,
)

router = APIRouter()


class DebugLogStatusResponse(BaseModel):
    """ck3_chronicler-z6jm slice 1: snapshot of the CK3 debug.log size +
    rotation threshold. The Settings page polls this once a minute and
    raises a UI alert when ``exceeded`` is true. ``path`` is exposed so
    the alert can name the actual file the user (or chronicler) would
    rotate."""

    exists: bool
    size_bytes: int
    threshold_bytes: int
    exceeded: bool
    path: str


class DebugLogRotateResponse(BaseModel):
    """ck3_chronicler-z6jm slice 1: result of a manual rotate request.

    On success: ``rotated=True``, ``archive_path`` names the gzip
    archive, ``offsets_reset`` is the count of campaign tail_offset
    rows reset to 0.

    On the no-op path (log missing or empty): ``rotated=False``,
    ``message`` explains. Status code is still 200 — the user clicked
    a button and got a defined outcome, not an error.

    The endpoint deliberately does NOT distinguish "CK3 is open" from
    other rotation failures here; the helper logs that to stderr but
    still returns success-with-archive-but-original-not-truncated. The
    UI's responsibility is to remind the user 'close CK3 first'."""

    rotated: bool
    archive_path: str | None
    offsets_reset: int
    message: str


@router.get(
    "/debug-log-status",
    response_model=DebugLogStatusResponse,
)
def get_debug_log_status() -> DebugLogStatusResponse:
    """ck3_chronicler-z6jm slice 1: read-only snapshot of CK3's
    debug.log size + rotation threshold. The Settings page polls this
    once a minute and raises a UI alert when ``exceeded`` is true."""
    status = stat_debug_log()
    return DebugLogStatusResponse(
        exists=status.exists,
        size_bytes=status.size_bytes,
        threshold_bytes=status.threshold_bytes,
        exceeded=status.exceeded,
        path=status.path,
    )


@router.post(
    "/debug-log-rotate",
    response_model=DebugLogRotateResponse,
)
async def post_debug_log_rotate() -> DebugLogRotateResponse:
    """ck3_chronicler-z6jm slice 1: manual rotate.

    Archives debug.log to ``<logs>/archives/debug-YYYYMMDD-HHMM.log.gz``
    and truncates the original to 0 bytes. Resets every campaign's
    persisted ``tail_offset`` to 0 so the next chronicler tick picks
    up from the empty file.

    Best-effort on Windows: if CK3 holds the file open, the truncate
    phase fails silently (logged); the archive still gets written.
    The UI's job is to remind the user to close CK3 first.

    audit F-04 / ck3_chronicler-shen: gzip on a 200 MB log used to run
    inline — that pinned a threadpool worker for the duration. Now
    async + offloaded via asyncio.to_thread."""
    from chronicler.config import get_ck3_debug_log

    log_path = get_ck3_debug_log()
    archive_path = await asyncio.to_thread(archive_and_truncate, log_path)
    if archive_path is None:
        return DebugLogRotateResponse(
            rotated=False,
            archive_path=None,
            offsets_reset=0,
            message="no log to rotate (missing or already empty)",
        )
    offsets_reset = await asyncio.to_thread(reset_tail_offsets, [])
    return DebugLogRotateResponse(
        rotated=True,
        archive_path=str(archive_path),
        offsets_reset=offsets_reset,
        message=f"archived to {archive_path}",
    )
