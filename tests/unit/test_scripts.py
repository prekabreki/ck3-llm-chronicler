"""Light tests for the scripts/ utilities (run_tiger, rotate_debug_log).

These don't exercise the external dependencies (ck3-tiger binary, CK3
itself) — they verify the wrapper logic, error paths, and that the
expected files are produced when the inputs cooperate.
"""

from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path

import pytest


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_tiger():
    return _load("run_tiger")


@pytest.fixture
def rotate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Load rotate_debug_log with CHRONICLER_DATA_DIR pointing into tmp."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    return _load("rotate_debug_log")


def test_run_tiger_missing_binary(run_tiger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_tiger, "find_tiger", lambda: None)
    rc = run_tiger.main(["--mod", str(Path("does-not-matter"))])
    assert rc == 127


def test_run_tiger_missing_mod_dir(
    run_tiger, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run_tiger, "find_tiger", lambda: "fake-tiger")
    rc = run_tiger.main(["--mod", str(tmp_path / "not-here")])
    assert rc == 2


def test_archive_and_truncate_handles_missing_log(rotate, tmp_path: Path) -> None:
    result = rotate.archive_and_truncate(tmp_path / "debug.log")
    assert result is None


def test_archive_and_truncate_skips_empty(rotate, tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    log.write_bytes(b"")
    assert rotate.archive_and_truncate(log) is None


def test_archive_and_truncate_compresses_and_clears(rotate, tmp_path: Path) -> None:
    log = tmp_path / "debug.log"
    log.write_bytes(b"a long line of fake debug content\n" * 100)
    original_size = log.stat().st_size

    archive = rotate.archive_and_truncate(log)
    assert archive is not None
    assert archive.suffix == ".gz"
    assert archive.exists()

    with gzip.open(archive, "rb") as fh:
        decompressed = fh.read()
    assert len(decompressed) == original_size

    assert log.stat().st_size == 0


def test_reset_tail_offsets_no_campaigns(rotate, tmp_path: Path) -> None:
    # CHRONICLER_DATA_DIR set by fixture; no campaigns yet.
    n = rotate.reset_tail_offsets([])
    assert n == 0


def test_reset_tail_offsets_resets_only_nonzero(rotate, tmp_path: Path) -> None:
    from chronicler.db.registry import create_campaign, get_tail_offset, set_tail_offset

    # Two campaigns: one with offset, one fresh.
    a = create_campaign("A")
    b = create_campaign("B")
    set_tail_offset(a.id, 4096)

    n = rotate.reset_tail_offsets([])
    assert n == 1
    assert get_tail_offset(a.id) == 0
    assert get_tail_offset(b.id) == 0  # already was 0


def test_reset_tail_offsets_warns_on_unknown_campaign(
    rotate, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """audit F-25 / ck3_chronicler-aa1o: warning is now emitted via the
    ``chronicler.tailer.log_rotate`` logger rather than print(...,
    file=sys.stderr) so that the HTTP route handler can call this
    helper without polluting uvicorn's stdout."""
    import logging

    with caplog.at_level(logging.WARNING, logger="chronicler.tailer.log_rotate"):
        rotate.reset_tail_offsets(["does-not-exist"])
    messages = [r.getMessage() for r in caplog.records]
    assert any("no such campaign" in m for m in messages), messages
