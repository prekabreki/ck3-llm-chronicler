"""Tests for the SSE ingest-stream endpoint (ck3_chronicler-owk).

The async generator behind the endpoint (``event_stream``) is exercised
directly so we don't depend on httpx ASGITransport's chunk-pull
timing for streaming responses (which is finicky on Windows event
loops). The 404-on-unknown-campaign path goes through the full FastAPI
stack since it's a request/response one-shot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import httpx
import pytest

from chronicler.api import create_app
from chronicler.api.events import EventBus
from chronicler.api.sse import event_stream, format_sse
from chronicler.db import (
    Base,
    make_engine_for_path,
)
from chronicler.db.registry import create_campaign


@pytest.fixture
def registry_with_campaign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    registry = tmp_path / "registry.db"
    campaign_db = tmp_path / "campaigns" / "stream.db"
    campaign_db.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine_for_path(campaign_db)
    Base.metadata.create_all(engine)
    engine.dispose()
    create_campaign("stream", db_path=str(campaign_db), registry=registry)
    return registry


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only the attribute the
    SSE generator touches (is_disconnected) is needed."""

    def __init__(self) -> None:
        self._disconnected = False

    def disconnect(self) -> None:
        self._disconnected = True

    async def is_disconnected(self) -> bool:
        return self._disconnected


def test_format_sse_emits_data_frame() -> None:
    out = format_sse({"event_id": 1, "kind": "event_ingested"})
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    payload = out[len("data: ") : -2]
    assert json.loads(payload) == {"event_id": 1, "kind": "event_ingested"}


@pytest.mark.asyncio
async def test_stream_yields_initial_connect_comment() -> None:
    bus = EventBus()
    request = _FakeRequest()
    gen = event_stream(request, bus, "camp-1")
    first = await gen.__anext__()
    assert "chronicler-sse-connected" in first
    request.disconnect()
    # Drain to clean up the generator
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_stream_emits_published_events_as_sse_frames() -> None:
    bus = EventBus()
    request = _FakeRequest()
    gen = event_stream(request, bus, "camp-1")

    # First frame is the connect comment. register() runs synchronously
    # before that yield, so the queue is already on the bus.
    first = await gen.__anext__()
    assert "chronicler-sse-connected" in first
    assert bus.subscriber_count("camp-1") == 1

    # One pending __anext__ to receive the next frame, then publish into it.
    next_task = asyncio.create_task(gen.__anext__())
    bus.publish("camp-1", {"kind": "event_ingested", "event_id": 1})
    frame = await asyncio.wait_for(next_task, timeout=3.0)
    assert frame == format_sse({"kind": "event_ingested", "event_id": 1})

    bus.publish("camp-1", {"kind": "event_ingested", "event_id": 2})
    frame2 = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
    assert frame2 == format_sse({"kind": "event_ingested", "event_id": 2})

    request.disconnect()
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_stream_unregisters_subscriber_on_disconnect() -> None:
    bus = EventBus()
    request = _FakeRequest()
    gen = event_stream(request, bus, "camp-1")
    await gen.__anext__()  # connect comment — register() runs synchronously
    assert bus.subscriber_count("camp-1") == 1

    request.disconnect()
    # Drain the generator: the loop's first is_disconnected() check breaks
    # immediately and the finally unsubscribes. Deterministic — no fixed
    # sleep and no pending __anext__ to cancel.
    async for _ in gen:
        pass
    assert bus.subscriber_count("camp-1") == 0


@pytest.mark.asyncio
async def test_stream_emits_heartbeat_pings_when_idle(monkeypatch) -> None:
    """Idle stream emits ': ping' comment frames at the heartbeat interval."""
    # Speed the heartbeat up so the test isn't slow
    import chronicler.api.sse as mod

    monkeypatch.setattr(mod, "HEARTBEAT_SECONDS", 0.2)

    bus = EventBus()
    request = _FakeRequest()
    gen = mod.event_stream(request, bus, "camp-1")
    await gen.__anext__()  # connect comment

    # Wait for a heartbeat — should arrive within ~200ms
    frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert frame == ": ping\n\n"

    request.disconnect()
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_stream_emits_repeated_heartbeats_on_quiet_channel(monkeypatch) -> None:
    """Quiet stream must keep emitting ': ping' frames on the heartbeat cadence.

    ck3_chronicler-dsjo (root cause): the earlier event_stream wrapped
    iterator.__anext__() in asyncio.wait_for. When the outer wait_for
    fired TimeoutError it cancelled the inner aiter_queue generator,
    which exhausts after CancelledError unwinds its body. Subsequent
    __anext__() calls then raised StopAsyncIteration immediately and
    the stream silently returned right after the FIRST ping —
    surfacing as 'channel closes + reconnects every ~8s' to Chrome
    (heartbeat 2s + Chrome's 3-5s reconnect backoff).

    This test exercises 3 consecutive heartbeats — pre-fix the second
    await raised StopAsyncIteration; post-fix all three arrive on
    cadence."""
    import chronicler.api.sse as mod

    monkeypatch.setattr(mod, "HEARTBEAT_SECONDS", 0.05)

    bus = EventBus()
    request = _FakeRequest()
    gen = mod.event_stream(request, bus, "camp-1")

    await gen.__anext__()  # consume connect comment

    pings = []
    for _ in range(3):
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        pings.append(frame)

    assert pings == [": ping\n\n"] * 3, (
        f"expected 3 consecutive ping frames; got {pings!r}. Pre-fix "
        "the second await raises StopAsyncIteration because the inner "
        "aiter_queue generator is exhausted after the first wait_for "
        "cancellation."
    )

    request.disconnect()
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_stream_keeps_serving_events_after_heartbeat(monkeypatch) -> None:
    """An event published AFTER the first heartbeat fired must still
    be delivered. Pre-dsjo-fix the underlying iterator was destroyed
    by the heartbeat-cancellation race; the second publish never
    surfaced because the stream had already exited via
    StopAsyncIteration on its second loop iteration."""
    import chronicler.api.sse as mod

    monkeypatch.setattr(mod, "HEARTBEAT_SECONDS", 0.05)

    bus = EventBus()
    request = _FakeRequest()
    gen = mod.event_stream(request, bus, "camp-1")

    await gen.__anext__()  # connect comment
    # First heartbeat fires (no events yet, 50ms timeout expires)
    first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert first == ": ping\n\n"

    # Publish — must still flow through to the next yield, not silently
    # vanish behind a destroyed iterator.
    bus.publish("camp-1", {"kind": "event_ingested", "event_id": 42})
    frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert frame.startswith("data: ")
    assert '"event_id":42' in frame

    request.disconnect()
    async for _ in gen:
        pass


@pytest.mark.asyncio
async def test_sse_endpoint_404_for_unknown_campaign(registry_with_campaign: Path) -> None:
    """One-shot 404 case still goes through the full FastAPI stack."""
    app = create_app(registry_path=registry_with_campaign)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sse/ingest/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sse_endpoint_returns_correct_content_type(
    registry_with_campaign: Path,
) -> None:
    """Successful endpoint returns text/event-stream + no-cache headers.

    Wrapped in asyncio.timeout because httpx.ASGITransport doesn't
    propagate {"type": "http.disconnect"} on response close back to the
    ASGI app — the server-side event_stream coroutine stays alive in
    queue.get() forever. Pre-dsjo-fix this test happened to pass
    because the old iterator pattern self-terminated via
    StopAsyncIteration after the first heartbeat (the very bug dsjo
    fixed). Headers are all we care about; cancel after capture."""
    app = create_app(registry_path=registry_with_campaign)
    transport = httpx.ASGITransport(app=app)
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(2.0):
            async with (
                httpx.AsyncClient(transport=transport, base_url="http://test") as client,
                client.stream("GET", "/api/sse/ingest/stream") as response,
            ):
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-cache"


# --- ck3_chronicler-lb5r + gf79: SSE cold-start race regression test ---


@pytest.mark.asyncio
async def test_stream_registers_queue_before_first_yield() -> None:
    """The bus.register() call must run synchronously inside event_stream
    BEFORE any yield. Previously the async-generator body of
    bus.subscribe() didn't run until the first __anext__(), so any
    publish in the window between the SSE response starting and the
    iteration loop landed in an unregistered campaign and silently
    dropped. Root cause of j32g / jjqf / dsjo cold-start drops.

    Construct the generator, call __anext__ once to consume the
    connect comment, and assert the subscriber count is already 1 —
    no waiting, no sleep-dance."""
    bus = EventBus()
    request = _FakeRequest()
    gen = event_stream(request, bus, "camp-cold-start")
    # Drive the body to the first yield (the connect comment).
    first = await gen.__anext__()
    assert "chronicler-sse-connected" in first
    # Subscriber must be registered NOW, not after another iteration.
    assert bus.subscriber_count("camp-cold-start") == 1

    request.disconnect()
    with contextlib.suppress(StopAsyncIteration):
        async for _ in gen:
            pass


@pytest.mark.asyncio
async def test_stream_receives_publish_immediately_after_first_yield() -> None:
    """A publish issued the instant the SSE response begins (right
    after the connect-comment frame is yielded) must be delivered —
    not lost in the gap between the response opening and the first
    iteration of the wait_for(__anext__) loop.

    Pre-fix this test would have failed: the queue wasn't registered
    until __anext__() ran the subscribe() body, and the publish would
    short-circuit on the no-subscribers branch of EventBus.publish."""
    bus = EventBus()
    request = _FakeRequest()
    gen = event_stream(request, bus, "camp-race")
    # First yield consumes the connect comment AND registers the queue.
    await gen.__anext__()
    # Publish *before* asking for the next iteration. The publish
    # synchronously enqueues to the registered queue.
    bus.publish("camp-race", {"kind": "event_ingested", "event_id": 7})
    frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert frame == format_sse({"kind": "event_ingested", "event_id": 7})

    request.disconnect()
    with contextlib.suppress(StopAsyncIteration):
        async for _ in gen:
            pass


@pytest.mark.asyncio
async def test_stream_unregisters_on_disconnect_via_register_path() -> None:
    """The register/aiter_queue/unsubscribe triple still cleans up the
    subscriber set when the client disconnects, even though the
    iterator no longer owns the queue lifetime."""
    bus = EventBus()
    request = _FakeRequest()
    gen = event_stream(request, bus, "camp-clean")
    await gen.__anext__()
    assert bus.subscriber_count("camp-clean") == 1
    request.disconnect()
    with contextlib.suppress(StopAsyncIteration):
        async for _ in gen:
            pass
    assert bus.subscriber_count("camp-clean") == 0
