"""HTTP-level tests for the Logs API (ck3_chronicler-yrv3).

Covers the cold-load endpoint /api/logs/recent + the known-loggers
helper. The /api/sse/logs endpoint itself is tested indirectly via
the unit tests on LogBufferHandler.since() since the SSE wire format
is just a JSON-per-line envelope of the same data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicler.api import create_app
from chronicler.api.log_buffer import (
    get_log_buffer,
    reset_log_buffer_for_testing,
)


@pytest.fixture(autouse=True)
def _reset_buffer():
    reset_log_buffer_for_testing()
    yield
    reset_log_buffer_for_testing()


@pytest.fixture(autouse=True)
def _verbose_chronicler_logger():
    """Pin the ``chronicler`` logger at DEBUG for the duration of each
    test so logger.info(...) calls actually reach handlers. Test runners
    sometimes leave it at WARNING which would otherwise drop our
    fixtures' emits before the LogBufferHandler sees them."""
    root = logging.getLogger("chronicler")
    prev = root.level
    root.setLevel(logging.DEBUG)
    yield
    root.setLevel(prev)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Fresh app instance; the lifespan attaches the log buffer to
    the chronicler root logger so any log calls during the test
    populate the buffer."""
    registry = tmp_path / "registry.db"
    app = create_app(registry_path=registry)
    with TestClient(app) as c:
        yield c


def test_recent_logs_returns_empty_array_on_cold_buffer(client: TestClient) -> None:
    """A new buffer with no emits should return [] (not 500, not 404).
    The FE handles empty backfill gracefully."""
    resp = client.get("/api/logs/recent")
    assert resp.status_code == 200
    # Empty list — startup may have logged nothing under the chronicler
    # namespace (depends on lifespan ordering), so we just assert shape.
    body = resp.json()
    assert isinstance(body, list)


def test_recent_logs_surfaces_emitted_chronicler_log_lines(
    client: TestClient,
) -> None:
    """A log.info on chronicler.* lands in the buffer and shows up
    on GET /api/logs/recent. This is the core round-trip."""
    log = logging.getLogger("chronicler.test_api_logs")
    log.info("hello from the test")
    log.warning("careful now")

    resp = client.get("/api/logs/recent?limit=50")
    assert resp.status_code == 200
    messages = [e["message"] for e in resp.json()]
    assert "hello from the test" in messages
    assert "careful now" in messages


def test_recent_logs_min_level_filters_server_side(client: TestClient) -> None:
    log = logging.getLogger("chronicler.test_api_logs_level")
    log.debug("dbg-line")
    log.info("inf-line")
    log.warning("warn-line")
    log.error("err-line")

    resp = client.get("/api/logs/recent?min_level=WARNING")
    assert resp.status_code == 200
    messages = [e["message"] for e in resp.json()]
    assert "dbg-line" not in messages
    assert "inf-line" not in messages
    assert "warn-line" in messages
    assert "err-line" in messages


def test_recent_logs_limit_bounds_returned_count(client: TestClient) -> None:
    log = logging.getLogger("chronicler.test_api_logs_limit")
    for i in range(20):
        log.info("burst-%d", i)
    resp = client.get("/api/logs/recent?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) <= 5
    # The last 5 messages should be the freshest from our burst.
    messages = [e["message"] for e in body]
    assert "burst-19" in messages


def test_recent_logs_rejects_out_of_range_limit(client: TestClient) -> None:
    """Limits outside [1, 2000] are rejected by the Query()
    validator — keeps a careless URL from blowing up the response."""
    assert client.get("/api/logs/recent?limit=0").status_code == 422
    assert client.get("/api/logs/recent?limit=99999").status_code == 422


def test_known_loggers_lists_distinct_names(client: TestClient) -> None:
    a = logging.getLogger("chronicler.api.routes")
    b = logging.getLogger("chronicler.save.ingest")
    a.info("from a")
    b.info("from b")
    resp = client.get("/api/logs/loggers")
    assert resp.status_code == 200
    names = resp.json()
    assert "chronicler.api.routes" in names
    assert "chronicler.save.ingest" in names


def test_log_envelope_shape_matches_wire_contract(client: TestClient) -> None:
    """The FE consumes envelopes by keys {seq, ts, level, logger,
    message}; this guards against accidental shape drift."""
    logging.getLogger("chronicler.test_envelope_shape").info("shape-check")
    resp = client.get("/api/logs/recent?limit=50")
    body = resp.json()
    matched = [e for e in body if e["message"] == "shape-check"]
    assert matched, "Expected emitted log line to be present in buffer"
    env = matched[0]
    assert set(env.keys()) == {"seq", "ts", "level", "logger", "message"}
    assert env["level"] == "INFO"
    assert env["logger"] == "chronicler.test_envelope_shape"
    assert isinstance(env["seq"], int)
    # Timestamp is ISO-8601 UTC with millisecond precision: shape only.
    assert env["ts"].endswith("Z")
    assert "T" in env["ts"]


def test_buffer_singleton_is_attached_to_chronicler_root_logger(
    client: TestClient,
) -> None:
    """Lifespan must attach the buffer handler. Without it the routes
    return empty arrays regardless of emit volume — silent failure
    mode would be confusing in the field."""
    root = logging.getLogger("chronicler")
    from chronicler.api.log_buffer import LogBufferHandler

    handler_instances = [h for h in root.handlers if isinstance(h, LogBufferHandler)]
    assert len(handler_instances) == 1
    assert handler_instances[0] is get_log_buffer()


def test_halt_endpoint_returns_202_and_schedules_exit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ck3_chronicler-ghfi: POST /api/halt accepts the request and
    schedules an os._exit. We patch os._exit so the test runner
    doesn't get killed; the test asserts (a) the response is 202 and
    (b) os._exit was called (just verifies the kill path runs)."""
    import chronicler.api.routes.logs as logs_module

    exit_calls: list[int] = []
    monkeypatch.setattr(logs_module.os, "_exit", lambda code: exit_calls.append(code))
    # Shorten the delay so the test doesn't waste 400 ms waiting.
    monkeypatch.setattr(logs_module, "_HALT_DELAY_S", 0.01)

    resp = client.post("/api/halt")
    assert resp.status_code == 202

    # The os._exit call is scheduled on the event loop with a 10 ms
    # delay; give the loop a beat to run it.
    import time

    for _ in range(50):
        if exit_calls:
            break
        time.sleep(0.01)
    assert exit_calls == [0]
