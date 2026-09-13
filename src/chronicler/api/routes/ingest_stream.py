"""SSE endpoint that streams ingest events to the v0.7 portal (ck3_chronicler-owk).

GET /api/sse/ingest/{name} subscribes to the in-process EventBus
(:mod:`chronicler.api.events`) for the named campaign and writes each
published event back to the client as a Server-Sent Events frame.

The stream loop, frame encoder and response wrapper are shared SSE
plumbing and live in :mod:`chronicler.api.sse` (event_stream /
sse_response); the importer and heraldry routes reuse them too.
Heartbeats: a ``: ping\\n\\n`` comment frame is emitted every
``HEARTBEAT_SECONDS`` so idle connections survive proxy / browser
inactivity timeouts.

We deliberately don't depend on sse-starlette: the wire format is a
handful of lines and FastAPI's StreamingResponse handles the rest.
Less moving parts, no extra dep.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from chronicler.api.dependencies import get_campaign
from chronicler.api.events import EventBus
from chronicler.api.sse import event_stream, sse_response
from chronicler.db.registry import Campaign

router = APIRouter(prefix="/api/sse", tags=["sse"])


@router.get("/ingest/{name}")
async def stream_ingest(
    request: Request,
    campaign: Campaign = Depends(get_campaign),
) -> StreamingResponse:
    """Server-Sent Events stream of ingest activity for one campaign.

    Each event is delivered as an SSE ``data:`` frame containing the
    JSON-encoded payload published by the ingest hooks (see
    ck3_chronicler-ek2). The connection stays open until the client
    disconnects; idle connections receive a ``: ping`` comment every
    HEARTBEAT_SECONDS (2s).

    404 (via the get_campaign dependency) when the campaign is unknown.
    """
    bus: EventBus = request.app.state.event_bus
    return sse_response(event_stream(request, bus, campaign.id))
