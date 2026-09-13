"""Tests for chronicler.tailer.log_rotate (z6jm slice 1).

The archive_and_truncate / reset_tail_offsets helpers were extracted
from scripts/rotate_debug_log.py so the HTTP rotate endpoint can call
them. Their behavioural tests stayed in tests/unit/test_scripts.py
under the legacy import path. This module covers stat_debug_log, which
is new in z6jm slice 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.tailer.log_rotate import (
    DEBUG_LOG_ROTATE_THRESHOLD_BYTES,
    DebugLogStatus,
    stat_debug_log,
)


def test_stat_debug_log_reports_missing_file_cleanly(tmp_path: Path) -> None:
    """z6jm: a fresh chronicler install with no CK3 run yet must NOT
    500 the status endpoint. Missing log -> exists=False, size=0,
    exceeded=False, no exception."""
    status = stat_debug_log(tmp_path / "missing.log")
    assert isinstance(status, DebugLogStatus)
    assert status.exists is False
    assert status.size_bytes == 0
    assert status.exceeded is False
    assert status.threshold_bytes == DEBUG_LOG_ROTATE_THRESHOLD_BYTES


def test_stat_debug_log_reports_size_under_threshold(tmp_path: Path) -> None:
    """A small log is under threshold; exceeded=False."""
    log = tmp_path / "debug.log"
    log.write_bytes(b"a few bytes of fake content\n")
    status = stat_debug_log(log)
    assert status.exists is True
    assert status.size_bytes == 28
    assert status.exceeded is False


def test_stat_debug_log_flags_exceeded_above_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z6jm: above the configured threshold the status flips exceeded=True
    so the UI can surface the 'rotate me' alert. Test threshold is
    monkeypatched down to a small value so the test doesn't have to
    write 200 MB of bytes."""
    monkeypatch.setattr(
        "chronicler.tailer.log_rotate.DEBUG_LOG_ROTATE_THRESHOLD_BYTES",
        100,
    )
    log = tmp_path / "debug.log"
    log.write_bytes(b"x" * 250)
    status = stat_debug_log(log)
    assert status.size_bytes == 250
    assert status.threshold_bytes == 100
    assert status.exceeded is True


def test_stat_debug_log_path_field_is_string(tmp_path: Path) -> None:
    """``path`` is a string for clean JSON serialisation in the response
    model — Pydantic round-trips str cleanly, Path needs a custom
    encoder that we don't want to wire just for this field."""
    log = tmp_path / "debug.log"
    status = stat_debug_log(log)
    assert isinstance(status.path, str)
    assert status.path.endswith("debug.log")
