"""Shared Server-Sent Events helpers.

ck3_chronicler-27ov.78 (audit L12): the EventBus SSE bridge used to live
as a private ``_stream`` in the ingest-stream *route* module, which the
importer and heraldry routes reached across into. The standard SSE
response (identical media-type + headers) was copy-pasted in four route
modules. Both now live here, so a route module is no longer the de-facto
home of cross-cutting SSE plumbing.

The log-tail stream (routes/logs.py) keeps its own poll-based loop — it
reads a LogBufferHandler rather than an EventBus queue — but shares
``format_sse`` and ``sse_response`` from here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from chronicler.api.events import EventBus

log = logging.getLogger(__name__)

# ck3_chronicler-j32g → dsjo: heartbeat dropped from 15s → 5s → 2s.
# j32g cut 15s → 5s (smoke 2026-05-08 saw ~18s reconnect cycle). dsjo
# follow-up (2026-05-15): 5s heartbeat raced with uvicorn's default
# timeout_keep_alive=5s and Node's default http.Server keepAliveTimeout
# also 5s — both could fire mid-heartbeat on a quiet stream. 2s gives
# margin without meaningful wire cost (one ~10-byte comment frame). The
# matching uvicorn timeout_keep_alive=120 in cli/main.py is the real
# server-side cure; this is belt-and-braces. Final dev-side hypothesis
# still to validate: Vite's underlying connect http.Server
# keepAliveTimeout (Node default 5000ms) may close the downstream Chrome
# connection independent of proxyTimeout=0. Test by `curl -N
# http://localhost:8000/api/sse/ingest/<campaign>` direct (bypasses
# Vite) vs through `:5173`; if direct stays open and proxied closes,
# the fix lives in vite.config.ts.
HEARTBEAT_SECONDS = 2.0

# Cap on each ``queue.get()`` wait so disconnect detection is at most
# this many seconds late on a quiet channel. Smaller than the default
# heartbeat so the disconnect-check at the top of the loop fires
# between heartbeat ticks too.
_SLICE_SECONDS = 0.5

# Headers shared by every text/event-stream response (was copy-pasted in
# ingest_stream, importer, heraldry and logs).
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable nginx buffering when present
}


def sse_response(content: AsyncIterator[str]) -> StreamingResponse:
    """Wrap an SSE frame generator in a StreamingResponse with the
    standard event-stream media type + headers."""
    return StreamingResponse(
        content,
        media_type="text/event-stream",
        headers=dict(_SSE_HEADERS),
    )


def format_sse(event: dict) -> str:
    """Encode one event dict as a single SSE ``data:`` frame.

    JSON serialised to keep the wire format uniform — the client
    decodes via ``JSON.parse(message.data)``. Frames end with
    ``\\n\\n`` per the SSE spec.
    """
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


async def event_stream(
    request: Request,
    bus: EventBus,
    campaign_id: str,
) -> AsyncIterator[str]:
    """Async generator: SSE wire frames for one EventBus subscriber.

    Shared by the ingest, importer and heraldry SSE routes (each passes a
    different channel id). Heartbeats every HEARTBEAT_SECONDS keep the
    connection alive on quiet channels. Disconnect detection:
    ``request.is_disconnected()`` is polled between events so the
    subscriber is unregistered promptly rather than leaking until the
    next publish.

    ck3_chronicler-lb5r: the queue is registered via the synchronous
    :meth:`EventBus.register` *before* the first yield. Previously the
    async-generator body of ``bus.subscribe`` didn't run until the
    first ``__anext__()``, so any publish in the window between the
    SSE response starting and the first iteration was silently lost.
    Root cause of j32g / jjqf / dsjo cold-start drops.
    """
    stop = asyncio.Event()
    # Register synchronously so the subscriber queue is on the bus's
    # set BEFORE we yield the connection-comment frame. Any publish
    # racing in after our caller's bus.publish() finishes can no longer
    # land before us.
    queue, unsubscribe = bus.register(campaign_id)

    # Initial comment frame so connection-level proxies see immediate
    # bytes. Without this some intermediaries delay the first event
    # until a flush threshold.
    yield ": chronicler-sse-connected\n\n"

    last_heartbeat = asyncio.get_running_loop().time()
    connected_at = last_heartbeat
    # ck3_chronicler-dsjo root cause (was misdiagnosed as Vite proxy
    # keepAliveTimeout): the earlier implementation wrapped
    # ``iterator.__anext__()`` of an EventBus.aiter_queue async
    # generator in ``asyncio.wait_for(..., timeout=heartbeat)``. When
    # the heartbeat fired, wait_for threw CancelledError into the
    # inner generator's ``q.get()``; that propagated out of the
    # generator body (no ``except CancelledError:`` clause inside
    # aiter_queue) and the async-gen frame *exhausted*. The next
    # ``__anext__()`` call raised StopAsyncIteration immediately and
    # event_stream returned right after the FIRST ping — Chrome saw the
    # stream end + reconnected ~3-5s later (backoff), surfacing as
    # "channel closes + reconnects every ~8s". Direct queue.get() per
    # iteration avoids the issue: each get() is its own coroutine,
    # cancellation only affects that one get, the queue object lives
    # on for the next iteration.
    #
    # _SLICE_SECONDS caps each queue.get() wait so the loop returns to
    # check is_disconnected at most that often — needed when the client
    # closes during a quiet stream (httpx ASGI transport signals
    # disconnect via request._receive, which is_disconnected polls).
    # Without slicing, a quiet stream waits a full HEARTBEAT_SECONDS
    # before noticing the disconnect.
    try:
        while True:
            if await request.is_disconnected():
                age = asyncio.get_running_loop().time() - connected_at
                client = getattr(request, "client", None)
                client_port = getattr(client, "port", None) if client else None
                log.info(
                    "sse disconnect channel=%s age=%.2fs client_port=%s",
                    campaign_id,
                    age,
                    client_port,
                )
                stop.set()
                return
            if stop.is_set():
                return

            elapsed = asyncio.get_running_loop().time() - last_heartbeat
            if elapsed >= HEARTBEAT_SECONDS:
                yield ": ping\n\n"
                last_heartbeat = asyncio.get_running_loop().time()
                continue

            slice_t = min(_SLICE_SECONDS, HEARTBEAT_SECONDS - elapsed)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=slice_t)
            except TimeoutError:
                # Either time to ping or just time to recheck disconnect;
                # loop top decides on the next iteration.
                continue
            yield format_sse(event)
    finally:
        stop.set()
        unsubscribe()
