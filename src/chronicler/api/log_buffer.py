"""In-process log ring buffer with SSE fan-out (ck3_chronicler-yrv3).

Surfaces chronicler's own Python log output to the browser via a new
Logs tab. Lets the user run the backend headlessly (``pythonw -m
chronicler.cli.main serve``) with no console window and still see what
the process is doing.

Architecture:

- :class:`LogBufferHandler` is a stdlib :class:`logging.Handler`
  subclass attached to the ``chronicler`` root logger in the FastAPI
  lifespan. Each emitted record becomes a small envelope
  (``{seq, ts, level, logger, message}``) appended to a bounded
  :class:`collections.deque`. ``seq`` is a monotonically-increasing
  counter so subscribers can detect new records without re-comparing
  envelope contents.
- The /api/logs/recent endpoint reads a slice of the deque (cold-load
  backfill for the page).
- The /api/sse/logs endpoint polls ``last_seen_seq`` every
  ``POLL_INTERVAL_S`` and yields newly-appended envelopes as SSE
  ``data:`` frames. Polling is cheap (one deque read + one int
  compare per tick) and keeps the implementation thread-safe without
  cross-thread asyncio plumbing — the ingest loop and worker threads
  can call ``log.info(...)`` directly without worrying about which
  event loop owns the buffer.

The deque is capped at :data:`MAX_BUFFERED_LINES`; older lines fall
off as new ones arrive. A subscriber that's been idle long enough for
the buffer to wrap around gets the surviving tail — that's acceptable
for a logs panel (refresh re-syncs via /api/logs/recent).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections import deque

# Pydantic v2 + FastAPI need typing_extensions.TypedDict on Python <3.12
# to build a schema from the route's return annotation.
from typing_extensions import TypedDict

# Cap matches the FE viewport cap; both surfaces are bounded the same.
# Sized for "show me roughly the last save-tail tick's worth of logs"
# (~200-500 lines per tick for a normal ingest cycle), with headroom.
MAX_BUFFERED_LINES = 2_000

# How often the SSE poller checks for new envelopes. 200 ms is well
# below the perceptual lag threshold for a logs view and keeps the
# poll-loop cost negligible (one int compare + slice).
POLL_INTERVAL_S = 0.2


_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class LogEnvelope(TypedDict):
    """One log line in the wire format consumed by /api/logs/recent
    and /api/sse/logs.

    ``seq`` is a process-monotonic integer assigned at emit-time. The
    FE uses it to dedupe across the cold-load (/api/logs/recent) and
    live-stream (/api/sse/logs) channels and to detect gaps when the
    deque wraps.
    """

    seq: int
    ts: str
    level: str
    logger: str
    message: str


class LogBufferHandler(logging.Handler):
    """Thread-safe ring buffer + sequence-counter for log records.

    Attach to the ``chronicler`` root logger once at app startup. All
    emits are synchronized by ``self._lock`` so cross-thread emits
    (uvicorn workers, save-tail loop, narrative scheduler) compose
    safely.

    Idempotent ``attach()`` — calling twice on the same logger is a
    no-op (we check identity). Lets us re-mount in test fixtures
    without duplicating handlers.
    """

    def __init__(self, maxlen: int = MAX_BUFFERED_LINES) -> None:
        super().__init__(level=logging.DEBUG)
        self._buffer: deque[LogEnvelope] = deque(maxlen=maxlen)
        self._lock = threading.RLock()
        self._next_seq = 1

    def emit(self, record: logging.LogRecord) -> None:
        """Append one envelope to the ring. Never raises — emit
        failures are observability noise and shouldn't perturb the
        emitting code path."""
        try:
            envelope: LogEnvelope = {
                "seq": 0,  # filled under lock to keep seq monotonic
                "ts": _format_ts(record.created),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        except Exception:  # noqa: BLE001 — best-effort format
            return
        with self._lock:
            envelope["seq"] = self._next_seq
            self._next_seq += 1
            self._buffer.append(envelope)

    # --- read API (FastAPI routes call these) ---

    def recent(
        self,
        *,
        limit: int = 500,
        min_level: str | None = None,
    ) -> list[LogEnvelope]:
        """Return the most-recent ``limit`` envelopes, optionally
        filtered server-side by minimum level. Used by the cold-load
        endpoint /api/logs/recent.

        Filtering happens after slicing so the result is "the last
        N envelopes at or above min_level"; if the buffer wraps, we
        only see the surviving tail. The FE understands this and
        treats the returned list as a backfill, not a full history.
        """
        threshold = _LEVEL_NAMES.index(min_level) if min_level in _LEVEL_NAMES else None
        with self._lock:
            snapshot = list(self._buffer)
        if threshold is not None:
            snapshot = [e for e in snapshot if _LEVEL_NAMES.index(e["level"]) >= threshold]
        return snapshot[-limit:] if limit > 0 else snapshot

    def since(self, last_seen_seq: int) -> tuple[list[LogEnvelope], int]:
        """Return all envelopes with ``seq > last_seen_seq``, plus
        the new ``last_seen_seq`` after applying them.

        Used by the SSE poller. The (envelopes, new_seq) tuple
        eliminates a round-trip — the caller advances its cursor
        without re-reading buffer.peek().
        """
        with self._lock:
            snapshot = list(self._buffer)
            current_max = self._next_seq - 1
        fresh = [e for e in snapshot if e["seq"] > last_seen_seq]
        return fresh, current_max

    def known_loggers(self) -> list[str]:
        """Distinct logger names currently in the buffer, sorted.

        Powers the FE's per-logger filter dropdown so the user can
        narrow to e.g. just ``chronicler.save.ingest`` without typing
        the path.
        """
        with self._lock:
            names = {e["logger"] for e in self._buffer}
        return sorted(names)

    # --- lifecycle ---

    def attach(self, logger: logging.Logger | None = None) -> None:
        """Attach to ``logger`` (default: the ``chronicler`` root).

        Idempotent — if an instance of this handler is already on the
        logger, returns without adding a second one.
        """
        target = logger or logging.getLogger("chronicler")
        for existing in target.handlers:
            if isinstance(existing, LogBufferHandler):
                return
        target.addHandler(self)

    def detach(self, logger: logging.Logger | None = None) -> None:
        """Remove this handler from ``logger``. Used by tests."""
        target = logger or logging.getLogger("chronicler")
        with contextlib.suppress(ValueError):
            target.removeHandler(self)


def _format_ts(epoch: float) -> str:
    """ISO-8601 UTC with millisecond precision.

    Matches the format the FE already parses in other surfaces
    (last_save_ingested_at, generated_at, etc.). Millisecond precision
    keeps adjacent-second log lines distinguishable in the UI.
    """
    # time.gmtime is faster than datetime.utcnow().isoformat() and
    # matches the rest of the codebase's wire-format conventions.
    secs = int(epoch)
    millis = int((epoch - secs) * 1000)
    g = time.gmtime(secs)
    return (
        f"{g.tm_year:04d}-{g.tm_mon:02d}-{g.tm_mday:02d}T"
        f"{g.tm_hour:02d}:{g.tm_min:02d}:{g.tm_sec:02d}."
        f"{millis:03d}Z"
    )


# Module-level singleton. Created lazily on first call to
# ``get_log_buffer()`` so importing this module is side-effect free.
_singleton: LogBufferHandler | None = None
_singleton_lock = threading.Lock()


def get_log_buffer() -> LogBufferHandler:
    """Return the process-wide LogBufferHandler, creating it on first
    call.

    Used by ``app.py`` to attach the handler at startup, and by the
    routes to read from it.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = LogBufferHandler()
    return _singleton


def reset_log_buffer_for_testing() -> None:
    """Test-only: drop the singleton so the next get_log_buffer() call
    yields a fresh instance. Pairs with detach() on the chronicler
    logger if a test attached the previous instance."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.detach()
        _singleton = None
