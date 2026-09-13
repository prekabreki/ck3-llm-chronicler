"""Tests for chronicler.save.watcher — autosave directory tracking.

The async :func:`watch_saves` is exercised at the bottom of this file by
monkeypatching ``watchfiles.awatch`` (the only OS-level dependency), so no
real filesystem-watch backend is needed: the gedg retry-backoff, the
emit-existing drain on entry, the ``.ck3`` relevant-file filter, and the
happy-path emit are each covered. The synchronous
:class:`SaveDirectoryState` is the focus of the first block — its poll()
method drives all the deduplication and pattern logic, and is test-friendly
without an event loop.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from chronicler.save import watcher as watcher_mod
from chronicler.save.watcher import SaveDirectoryState, SaveFileEvent, watch_saves


def _write_save(
    path: Path, content: bytes = b"SAV01\nfake save", *, mtime: float | None = None
) -> None:
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_state_emits_existing_when_requested(tmp_path: Path) -> None:
    _write_save(tmp_path / "autosave.ck3")
    _write_save(tmp_path / "autosave_1.ck3")
    state = SaveDirectoryState(tmp_path, emit_existing=True)
    events = list(state.poll())
    assert len(events) == 2
    names = {e.path.name for e in events}
    assert names == {"autosave.ck3", "autosave_1.ck3"}


def test_state_skips_existing_by_default(tmp_path: Path) -> None:
    _write_save(tmp_path / "autosave.ck3")
    state = SaveDirectoryState(tmp_path)  # emit_existing=False
    assert list(state.poll()) == []


def test_state_emits_new_file_after_init(tmp_path: Path) -> None:
    _write_save(tmp_path / "autosave.ck3")
    state = SaveDirectoryState(tmp_path)
    # Pre-existing file is in _seen, no events.
    assert list(state.poll()) == []
    # New file appears.
    _write_save(tmp_path / "autosave_1.ck3")
    events = list(state.poll())
    assert len(events) == 1
    assert events[0].path.name == "autosave_1.ck3"


def test_state_emits_when_existing_file_rewritten(tmp_path: Path) -> None:
    """CK3 rotates by overwriting autosave.ck3 with the new save —
    same path, new content. State should emit because (mtime, size)
    changed."""
    p = tmp_path / "autosave.ck3"
    _write_save(p, b"old content")
    state = SaveDirectoryState(tmp_path)
    assert list(state.poll()) == []
    # Rewrite with different content, force mtime advance
    time.sleep(0.05)
    _write_save(p, b"new content with different size!!", mtime=time.time())
    events = list(state.poll())
    assert len(events) == 1
    assert events[0].path.name == "autosave.ck3"
    assert events[0].size_bytes == len(b"new content with different size!!")


def test_state_dedupes_unchanged_file(tmp_path: Path) -> None:
    p = tmp_path / "autosave.ck3"
    _write_save(p)
    state = SaveDirectoryState(tmp_path, emit_existing=True)
    first = list(state.poll())
    assert len(first) == 1
    # Poll again without changing the file — no new event.
    assert list(state.poll()) == []


def test_pattern_filter(tmp_path: Path) -> None:
    """Set pattern to only match autosaves; manual saves shouldn't fire."""
    _write_save(tmp_path / "autosave.ck3")
    _write_save(tmp_path / "Godwin_Kent_1066.ck3")  # manual save
    state = SaveDirectoryState(tmp_path, pattern="autosave*.ck3", emit_existing=True)
    events = list(state.poll())
    assert len(events) == 1
    assert events[0].path.name == "autosave.ck3"


def test_state_handles_missing_directory(tmp_path: Path) -> None:
    """A non-existent dir on init shouldn't crash; poll should yield nothing."""
    state = SaveDirectoryState(tmp_path / "nonexistent")
    assert list(state.poll()) == []


def test_save_event_carries_size_and_mtime(tmp_path: Path) -> None:
    p = tmp_path / "autosave.ck3"
    _write_save(p, b"x" * 1024)
    state = SaveDirectoryState(tmp_path, emit_existing=True)
    events = list(state.poll())
    assert isinstance(events[0], SaveFileEvent)
    assert events[0].size_bytes == 1024
    assert events[0].mtime_ns > 0


def test_disappearing_file_doesnt_crash_poll(tmp_path: Path) -> None:
    """If a file is deleted between glob and stat, poll should skip it
    rather than raise."""
    p = tmp_path / "autosave.ck3"
    _write_save(p)
    state = SaveDirectoryState(tmp_path)
    # Delete the file. State's _seen still has it but poll's scan won't
    # find it — no events emitted.
    p.unlink()
    assert list(state.poll()) == []


# --- ck3_chronicler-fi7: multi-pattern matching ---


def test_state_accepts_tuple_of_patterns(tmp_path: Path) -> None:
    """Pattern tuple matches each glob independently and dedupes by
    resolved path. The default DEFAULT_SAVE_PATTERN shape — exact-match
    pair for autosave.ck3 + autosave_exit.ck3 — must catch the exit
    save without picking up rotation backups."""
    _write_save(tmp_path / "autosave.ck3")
    _write_save(tmp_path / "autosave_exit.ck3")
    _write_save(tmp_path / "autosave_1.ck3")
    state = SaveDirectoryState(
        tmp_path,
        pattern=("autosave.ck3", "autosave_exit.ck3"),
        emit_existing=True,
    )
    events = list(state.poll())
    names = {e.path.name for e in events}
    assert names == {"autosave.ck3", "autosave_exit.ck3"}


def test_state_accepts_comma_separated_pattern(tmp_path: Path) -> None:
    """CLI ergonomic: a comma-separated string is split into a tuple of
    globs so typer can carry multiple patterns through ``--pattern``."""
    _write_save(tmp_path / "autosave.ck3")
    _write_save(tmp_path / "autosave_exit.ck3")
    _write_save(tmp_path / "autosave_1.ck3")
    state = SaveDirectoryState(
        tmp_path,
        pattern="autosave.ck3,autosave_exit.ck3",
        emit_existing=True,
    )
    names = {e.path.name for e in state.poll()}
    assert names == {"autosave.ck3", "autosave_exit.ck3"}


def test_state_dedupes_when_patterns_overlap(tmp_path: Path) -> None:
    """A file matching multiple globs in the pattern tuple emits once,
    not once per matching pattern."""
    _write_save(tmp_path / "autosave.ck3")
    state = SaveDirectoryState(
        tmp_path,
        pattern=("autosave.ck3", "autosave*.ck3"),
        emit_existing=True,
    )
    events = list(state.poll())
    assert len(events) == 1
    assert events[0].path.name == "autosave.ck3"


# --- ck3_chronicler-gedg: watch_saves survives transient awatch errors ---


@pytest.mark.asyncio
async def test_watch_saves_retries_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First awatch invocation raises OSError; the loop should log,
    sleep, and retry — surviving instead of propagating the exception."""
    _write_save(tmp_path / "autosave.ck3")

    call_count = {"n": 0}

    async def fake_awatch(_path, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated transient filesystem hiccup")
        # Second call: yield one change set then end.
        yield [(1, str(tmp_path / "autosave.ck3"))]

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(watcher_mod, "awatch", fake_awatch)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    gen = watch_saves(tmp_path, emit_existing=False)
    # Drain — should yield zero events from the OSError retry path, then
    # complete when fake_awatch's second invocation exits cleanly.
    events = [evt async for evt in gen]

    # The save was pre-populated as 'seen' so no new event is yielded.
    assert events == []
    # awatch was called twice (initial + retry after exception).
    assert call_count["n"] == 2
    # The 2.0s backoff fired exactly once.
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_watch_saves_emits_existing_on_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With emit_existing=True the files already in the directory are drained
    before the awatch loop, then the generator completes cleanly when awatch
    returns (here: an awatch that yields nothing)."""
    _write_save(tmp_path / "autosave.ck3")
    _write_save(tmp_path / "autosave_1.ck3")

    async def empty_awatch(_path, **_kwargs):
        for _change in []:  # empty async generator — exits immediately
            yield _change

    monkeypatch.setattr(watcher_mod, "awatch", empty_awatch)

    events = [evt async for evt in watch_saves(tmp_path, emit_existing=True)]

    assert {e.path.name for e in events} == {"autosave.ck3", "autosave_1.ck3"}


@pytest.mark.asyncio
async def test_watch_saves_emits_new_file_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save that appears after the watch starts is emitted when awatch
    reports a change to it."""

    async def fake_awatch(_path, **_kwargs):
        # The save materialises only after the watch is live, so it is not
        # pre-seeded as 'seen' at SaveDirectoryState init.
        _write_save(tmp_path / "autosave.ck3")
        yield [(1, str(tmp_path / "autosave.ck3"))]

    monkeypatch.setattr(watcher_mod, "awatch", fake_awatch)

    events = [evt async for evt in watch_saves(tmp_path, emit_existing=False)]

    assert [e.path.name for e in events] == ["autosave.ck3"]


@pytest.mark.asyncio
async def test_watch_saves_skips_poll_when_no_ck3_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relevant-file filter gates the directory scan: when awatch reports
    only non-.ck3 changes, poll() is not run and nothing is emitted — even if
    a .ck3 happens to be on disk."""

    async def fake_awatch(_path, **_kwargs):
        _write_save(tmp_path / "autosave.ck3")
        yield [(1, str(tmp_path / "notes.txt"))]  # only a sidecar .txt changed

    monkeypatch.setattr(watcher_mod, "awatch", fake_awatch)

    events = [evt async for evt in watch_saves(tmp_path, emit_existing=False)]

    assert events == []
