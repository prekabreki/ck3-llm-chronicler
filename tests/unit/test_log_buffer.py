"""Tests for the in-process log ring buffer (ck3_chronicler-yrv3)."""

from __future__ import annotations

import logging
import threading

import pytest

from chronicler.api.log_buffer import (
    MAX_BUFFERED_LINES,
    LogBufferHandler,
    reset_log_buffer_for_testing,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_log_buffer_for_testing()
    yield
    reset_log_buffer_for_testing()


def _make_handler() -> LogBufferHandler:
    return LogBufferHandler(maxlen=128)


def test_emit_appends_envelope_with_monotonic_seq() -> None:
    h = _make_handler()
    logger = logging.getLogger("test_buffer_monotonic")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    logger.info("first")
    logger.info("second")
    logger.info("third")
    envelopes = h.recent(limit=10)
    assert [e["message"] for e in envelopes] == ["first", "second", "third"]
    seqs = [e["seq"] for e in envelopes]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3  # strictly increasing → distinct


def test_buffer_caps_at_maxlen_keeping_newest() -> None:
    h = LogBufferHandler(maxlen=5)
    logger = logging.getLogger("test_buffer_cap")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    for i in range(20):
        logger.info("line-%d", i)
    envelopes = h.recent(limit=100)
    assert len(envelopes) == 5
    assert [e["message"] for e in envelopes] == [
        "line-15",
        "line-16",
        "line-17",
        "line-18",
        "line-19",
    ]


def test_recent_filters_by_min_level() -> None:
    h = _make_handler()
    logger = logging.getLogger("test_buffer_levels")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    logger.debug("dbg")
    logger.info("inf")
    logger.warning("warn")
    logger.error("err")
    only_warn_and_up = h.recent(limit=10, min_level="WARNING")
    assert [e["message"] for e in only_warn_and_up] == ["warn", "err"]


def test_recent_ignores_unknown_min_level_and_returns_all() -> None:
    h = _make_handler()
    logger = logging.getLogger("test_buffer_levels_unknown")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    logger.info("a")
    logger.warning("b")
    # 'TRACE' isn't a stdlib level; treat as no-filter rather than 400.
    envelopes = h.recent(limit=10, min_level="TRACE")
    assert [e["message"] for e in envelopes] == ["a", "b"]


def test_since_returns_only_new_envelopes_past_cursor() -> None:
    h = _make_handler()
    logger = logging.getLogger("test_buffer_since")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    logger.info("first")
    logger.info("second")
    _, cursor = h.since(last_seen_seq=0)
    # Buffer has two envelopes; cursor is at the second.
    assert cursor == 2
    # Append one more; since(cursor) returns only the new one.
    logger.info("third")
    fresh, new_cursor = h.since(last_seen_seq=cursor)
    assert [e["message"] for e in fresh] == ["third"]
    assert new_cursor == 3
    # Cursor at the new max returns nothing.
    again, _ = h.since(last_seen_seq=new_cursor)
    assert again == []


def test_known_loggers_returns_sorted_distinct_names() -> None:
    h = _make_handler()
    a = logging.getLogger("chronicler.save.ingest")
    b = logging.getLogger("chronicler.narrative.scheduler")
    c = logging.getLogger("chronicler.api.routes")
    for logger in (a, b, c):
        logger.addHandler(h)
        logger.setLevel(logging.DEBUG)
    a.info("x")
    b.info("y")
    c.info("z")
    a.info("repeat")  # dup logger name shouldn't appear twice
    assert h.known_loggers() == [
        "chronicler.api.routes",
        "chronicler.narrative.scheduler",
        "chronicler.save.ingest",
    ]


def test_attach_is_idempotent_on_the_same_logger() -> None:
    """Re-mounting the same handler on the same logger is a no-op —
    important for test fixtures that reset between tests without
    spawning duplicate handlers in the process."""
    h = _make_handler()
    logger = logging.getLogger("test_buffer_attach")
    h.attach(logger)
    h.attach(logger)
    h.attach(logger)
    # Filter to LogBufferHandler instances of *this* handler.
    instances = [hd for hd in logger.handlers if hd is h]
    assert len(instances) == 1


def test_emit_is_thread_safe_seqs_remain_unique() -> None:
    """Concurrent emits from many threads must produce distinct seqs
    — chronicler logs from uvicorn workers, save-tail, and the
    narrative scheduler simultaneously, and a duplicate seq would
    break the SSE poller's dedup."""
    h = LogBufferHandler(maxlen=10_000)
    logger = logging.getLogger("test_buffer_threads")
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)

    def _spam() -> None:
        for i in range(200):
            logger.info("tid=%s i=%d", threading.get_ident(), i)

    threads = [threading.Thread(target=_spam) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    envelopes = h.recent(limit=10_000)
    seqs = [e["seq"] for e in envelopes]
    assert len(seqs) == 1600
    assert len(set(seqs)) == 1600


def test_max_buffered_lines_constant_is_reasonable() -> None:
    """Sanity: the cap is large enough for a save-tail tick's worth
    of logs without being so large that it bloats memory."""
    assert 500 <= MAX_BUFFERED_LINES <= 10_000
