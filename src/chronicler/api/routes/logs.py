"""Logs endpoints — surface the in-process log buffer to the FE.

GET /api/logs/recent — cold-load backfill. JSON array of the most-
recent N envelopes (optionally level-filtered server-side).

GET /api/sse/logs — live stream. Polls the LogBufferHandler every
``POLL_INTERVAL_S`` and yields any envelopes with a seq greater than
the last one yielded. Reuses the SSE wire format from
``chronicler.api.sse`` (heartbeat comment frame, JSON ``data:`` payload).

POST /api/halt — shut down the chronicler process (ghfi). Returns 202
immediately, then ``os._exit``s after a short delay so the response
has time to flush to the FE. The FE's Logs page wraps this in a
confirm dialog and ``window.close()``s itself on success.
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse

from chronicler.api.log_buffer import (
    POLL_INTERVAL_S,
    LogBufferHandler,
    LogEnvelope,
    get_log_buffer,
)
from chronicler.api.sse import format_sse, sse_response

router = APIRouter(prefix="/api", tags=["logs"])

log = logging.getLogger(__name__)

# Independent of chronicler.api.sse.HEARTBEAT_SECONDS (2.0s) — the two
# need not match. A proxy idle-timeout set above the larger of the two
# ping cadences covers both channels, which is all the heartbeat is for.
_HEARTBEAT_SECONDS = 5.0

# ck3_chronicler-ljza: hold strong references to fire-and-forget tasks.
_background_tasks: set[asyncio.Task] = set()


@router.get("/logs/recent")
def get_recent_logs(
    limit: int = Query(default=500, ge=1, le=2000),
    min_level: str | None = Query(default=None),
) -> list[LogEnvelope]:
    """Return the most-recent log envelopes. Used by the FE's Logs
    page on cold load to backfill before the SSE stream takes over.

    ``min_level`` accepts one of ``DEBUG``, ``INFO``, ``WARNING``,
    ``ERROR``, ``CRITICAL``. Unknown values are silently ignored —
    the FE typing constrains the inputs and we'd rather degrade to
    "show everything" than 400 on a typo.
    """
    buf = get_log_buffer()
    return buf.recent(limit=limit, min_level=min_level)


@router.get("/logs/loggers")
def get_known_loggers() -> list[str]:
    """Distinct logger names currently in the buffer. Powers the FE
    per-logger filter dropdown. Sorted alphabetically."""
    buf = get_log_buffer()
    return buf.known_loggers()


@router.get("/sse/logs")
async def stream_logs(request: Request) -> StreamingResponse:
    """SSE stream of newly-emitted log envelopes.

    Poll-based to keep the implementation thread-safe — the
    LogBufferHandler is filled from many threads (uvicorn workers,
    save-tail tasks, narrative scheduler), and a poll-on-seq design
    avoids cross-thread asyncio.Queue handoffs.
    """
    buf = get_log_buffer()
    return sse_response(_stream(request, buf))


async def _stream(request: Request, buf: LogBufferHandler):
    """Yield SSE frames for new log envelopes as they arrive.

    On connect, the cursor is set to the buffer's current max seq —
    new subscribers don't replay the entire buffer (the FE already
    pulled that via /api/logs/recent). They only see live tail from
    here forward.

    Disconnect detection mirrors ingest_stream._stream — polled
    between ticks so the subscriber unwinds promptly when the client
    closes the page.
    """
    yield ": chronicler-log-stream-connected\n\n"

    # Seed the cursor from the current max so we only emit *new* lines.
    _, last_seen_seq = buf.since(last_seen_seq=2**63 - 1)
    last_heartbeat = asyncio.get_running_loop().time()

    while True:
        if await request.is_disconnected():
            return

        envelopes, new_seq = buf.since(last_seen_seq=last_seen_seq)
        if envelopes:
            for env in envelopes:
                yield format_sse(env)
            last_seen_seq = new_seq
            last_heartbeat = asyncio.get_running_loop().time()

        now = asyncio.get_running_loop().time()
        if now - last_heartbeat >= _HEARTBEAT_SECONDS:
            yield ": ping\n\n"
            last_heartbeat = now

        await asyncio.sleep(POLL_INTERVAL_S)


# ck3_chronicler-ghfi: halt the chronicler process from the SPA.
# Closing the headless launcher's browser window doesn't kill the
# underlying python.exe; the user had to use Task Manager to actually
# stop chronicler. This endpoint gives the FE a clean way to stop the
# whole process from the in-app Logs page.
#
# `os._exit` skips Python's normal shutdown handlers (atexit, threading
# .Thread.daemon=False, etc.) — that's deliberate. We want a hard stop;
# the user pressed the button precisely because they want chronicler
# gone. Outstanding work (queued biographies, in-flight save-tail
# parses) is lost; the next launch picks up from the persisted state.
_HALT_DELAY_S = 0.4


@router.post("/halt", status_code=202)
async def halt_chronicler() -> Response:
    """Schedule the process to exit ~400 ms after this response flushes.

    The delay lets the 202 reach the FE so it can `window.close()` the
    browser tab without seeing a connection-error toast first.
    """
    log.warning(
        "halt requested via /api/halt; chronicler will exit in %.1fs",
        _HALT_DELAY_S,
    )

    async def _exit_after_delay() -> None:
        await asyncio.sleep(_HALT_DELAY_S)
        os._exit(0)

    task = asyncio.create_task(_exit_after_delay())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return Response(status_code=202)
