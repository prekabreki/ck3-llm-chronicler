"""Tests for the tailer's file-watcher core.

Covers the synchronous ``TailedFile.read_new_lines`` since that's where the
offset/truncation/partial-line logic lives. The async ``tail()`` wrapper is
just ``awatch`` + ``read_new_lines``; an integration test in V01-P06 will
exercise it end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from chronicler.tailer.watcher import TailedFile


def _append(path: Path, text: str) -> None:
    with path.open("ab") as fh:
        fh.write(text.encode("utf-8"))


def test_returns_nothing_for_missing_file(tmp_path: Path) -> None:
    state = TailedFile(tmp_path / "missing.log")
    assert list(state.read_new_lines()) == []


def test_reads_complete_lines(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "first\nsecond\nthird\n")
    state = TailedFile(log)
    lines = list(state.read_new_lines())
    assert [line.text for line in lines] == ["first", "second", "third"]
    assert state.offset == log.stat().st_size


def test_handles_crlf(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "first\r\nsecond\r\n")
    state = TailedFile(log)
    lines = list(state.read_new_lines())
    assert [line.text for line in lines] == ["first", "second"]


def test_partial_trailing_line_held_back(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "complete\nincompl")
    state = TailedFile(log)
    lines = list(state.read_new_lines())
    assert [line.text for line in lines] == ["complete"]
    # Offset stops at the start of the partial line.
    assert state.offset == len("complete\n")

    # Finish the partial line in a later append; the next call yields it.
    _append(log, "ete\n")
    more = list(state.read_new_lines())
    assert [line.text for line in more] == ["incomplete"]


def test_subsequent_calls_only_yield_new_lines(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "alpha\nbeta\n")
    state = TailedFile(log)
    first = list(state.read_new_lines())
    assert [line.text for line in first] == ["alpha", "beta"]
    second = list(state.read_new_lines())
    assert second == []

    _append(log, "gamma\n")
    third = list(state.read_new_lines())
    assert [line.text for line in third] == ["gamma"]


def test_truncation_resets_offset(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "old1\nold2\n")
    state = TailedFile(log)
    list(state.read_new_lines())
    assert state.offset > 0

    # Truncate (e.g. our rotate_debug_log script ran).
    log.write_bytes(b"")
    _append(log, "fresh\n")

    new = list(state.read_new_lines())
    assert [line.text for line in new] == ["fresh"]


def test_resume_from_offset(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "alpha\nbeta\n")
    after_alpha = len("alpha\n")
    state = TailedFile(log, start_offset=after_alpha)
    lines = list(state.read_new_lines())
    assert [line.text for line in lines] == ["beta"]


def test_empty_file(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    log.write_bytes(b"")
    state = TailedFile(log)
    assert list(state.read_new_lines()) == []
    assert state.offset == 0


def test_no_growth_yields_nothing(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "alpha\n")
    state = TailedFile(log)
    list(state.read_new_lines())
    # File unchanged — second call yields nothing.
    assert list(state.read_new_lines()) == []


def test_offset_reflects_only_complete_lines(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    _append(log, "alpha\nbeta")  # beta has no terminator
    state = TailedFile(log)
    list(state.read_new_lines())
    # alpha was yielded, beta was not — offset stops at beta's start.
    assert state.offset == len("alpha\n")


def test_replaces_invalid_utf8_safely(tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    log.write_bytes(b"good\nbad \xff\xfe ending\n")
    state = TailedFile(log)
    lines = list(state.read_new_lines())
    assert lines[0].text == "good"
    # The invalid bytes were replaced, parser still got a usable line.
    assert "ending" in lines[1].text
