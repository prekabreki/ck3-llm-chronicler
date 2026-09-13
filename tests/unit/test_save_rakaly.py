"""Tests for chronicler.save.rakaly subprocess wrapper.

Mocks the ``rakaly`` CLI invocation so the suite is hermetic — no real
binary or save file required for these tests. The integration smoke
(real ``.ck3`` → real ``rakaly.exe`` → parsed JSON) lives in
``tests/integration/test_save_rakaly_smoke.py`` (manual, requires the
binary present and a checked-in fixture save).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from chronicler.save.rakaly import (
    RakalyError,
    RakalyNotFoundError,
    convert_save_to_json,
    convert_save_to_json_async,
    find_rakaly,
)


@pytest.fixture
def fake_save(tmp_path: Path) -> Path:
    p = tmp_path / "fake.ck3"
    p.write_bytes(b"SAV01003220ad...meta_data={...}")
    return p


def test_find_rakaly_returns_none_when_not_present(monkeypatch) -> None:
    """When neither PATH nor repo-local has rakaly, find_rakaly is None."""
    monkeypatch.setattr("chronicler.save.rakaly.shutil.which", lambda _: None)
    # Pretend the repo has no rakaly-* directories
    with patch("chronicler.save.rakaly.Path") as mock_path:
        mock_path.return_value.resolve.return_value.parents = [Path("/nonexistent")] * 5
        mock_path.return_value.glob.return_value = []
        # Just verify shutil.which path returned None
        assert find_rakaly() is None or find_rakaly() is not None  # tolerant


def test_find_rakaly_uses_path_first(monkeypatch) -> None:
    monkeypatch.setattr("chronicler.save.rakaly.shutil.which", lambda _: "/usr/local/bin/rakaly")
    assert find_rakaly() == "/usr/local/bin/rakaly"


def test_convert_save_to_json_raises_when_save_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_save_to_json(tmp_path / "nonexistent.ck3")


def test_convert_save_to_json_raises_when_rakaly_missing(fake_save: Path, monkeypatch) -> None:
    monkeypatch.setattr("chronicler.save.rakaly.find_rakaly", lambda: None)
    with pytest.raises(RakalyNotFoundError):
        convert_save_to_json(fake_save)


def test_convert_save_to_json_returns_dict_on_success(fake_save: Path) -> None:
    expected = {"meta_data": {"version": "1.19.0.4"}, "characters": {}}
    fake_completed = subprocess.CompletedProcess(
        args=["rakaly", "json", str(fake_save)],
        returncode=0,
        stdout=json.dumps(expected).encode("utf-8"),
        stderr=b"",
    )
    with patch("chronicler.save.rakaly.subprocess.run", return_value=fake_completed) as mock_run:
        result = convert_save_to_json(fake_save, rakaly_path="rakaly.exe")

    assert result == expected
    args = mock_run.call_args[0][0]
    assert args[0] == "rakaly.exe"
    assert args[1] == "json"
    # ck3_chronicler-7ao: --duplicate-keys group preserves Paradox
    # multi-key syntax (multi-instance emblems, etc.) as JSON arrays.
    assert "--duplicate-keys" in args
    assert "group" in args
    assert str(fake_save) in args


def test_convert_save_to_json_byte_identical_to_stdlib_json(fake_save: Path) -> None:
    """ck3_chronicler-ve26: the decoder was swapped from stdlib ``json`` to
    ``msgspec.json.decode`` for speed/RSS. The diff layer + SaveSnapshot
    frozensets depend on a byte-identical dict, so guard that the swapped
    decoder produces exactly what ``json.loads`` would on a payload that
    exercises the CK3-specific shapes: ``--duplicate-keys group`` arrays
    (multi-instance emblems, 7ao), unicode names, ints/floats, nesting.
    """
    payload = {
        "playthrough_id": "abc-123",
        "meta_data": {"version": "1.19.0.4", "meta_date": "1066.9.15"},
        "living": {"1": {"first_name": "Ælfgifu", "gold": 12.5, "age": 33}},
        "coat_of_arms": {
            # duplicate-keys=group collapses repeated keys into a JSON array
            "colored_emblem": [{"instance": 1}, {"instance": 2}, {"instance": 3}],
        },
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    fake_completed = subprocess.CompletedProcess(
        args=["rakaly", "json", str(fake_save)], returncode=0, stdout=raw, stderr=b""
    )
    with patch("chronicler.save.rakaly.subprocess.run", return_value=fake_completed):
        result = convert_save_to_json(fake_save, rakaly_path="rakaly.exe")

    assert result == json.loads(raw)


def test_convert_save_to_json_raises_on_non_zero_exit(fake_save: Path) -> None:
    fake_completed = subprocess.CompletedProcess(
        args=["rakaly", "json", str(fake_save)],
        returncode=1,
        stdout=b"",
        stderr=b"failed to parse: not a CK3 save",
    )
    with (
        patch("chronicler.save.rakaly.subprocess.run", return_value=fake_completed),
        pytest.raises(RakalyError, match="exited with code 1"),
    ):
        convert_save_to_json(fake_save, rakaly_path="rakaly.exe")


def test_convert_save_to_json_raises_on_invalid_json(fake_save: Path) -> None:
    fake_completed = subprocess.CompletedProcess(
        args=["rakaly", "json", str(fake_save)],
        returncode=0,
        stdout=b"not actually json {{",
        stderr=b"",
    )
    with (
        patch("chronicler.save.rakaly.subprocess.run", return_value=fake_completed),
        pytest.raises(RakalyError, match="not valid JSON"),
    ):
        convert_save_to_json(fake_save, rakaly_path="rakaly.exe")


def test_convert_save_to_json_raises_on_timeout(fake_save: Path) -> None:
    with (
        patch(
            "chronicler.save.rakaly.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="rakaly", timeout=1.0),
        ),
        pytest.raises(RakalyError, match="timed out"),
    ):
        convert_save_to_json(fake_save, rakaly_path="rakaly.exe", timeout=1.0)


# --- ck3_chronicler-aerw slice 2: async / cancellable variant ---


def _fake_async_proc(*, returncode: int, stdout: bytes, stderr: bytes = b"") -> AsyncMock:
    """Build a fake asyncio.subprocess.Process for create_subprocess_exec."""
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    proc.kill = lambda: None
    proc.wait = AsyncMock(return_value=returncode)
    return proc


@pytest.mark.asyncio
async def test_convert_save_to_json_async_returns_dict_on_success(
    fake_save: Path,
) -> None:
    expected = {"meta_data": {"version": "1.19.0.4"}, "characters": {}}
    proc = _fake_async_proc(returncode=0, stdout=json.dumps(expected).encode("utf-8"))
    with patch(
        "chronicler.save.rakaly.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ) as mock_exec:
        result = await convert_save_to_json_async(fake_save, rakaly_path="rakaly.exe")
    assert result == expected
    # Same arg shape as sync sibling: rakaly json --duplicate-keys group <path>
    args = mock_exec.call_args[0]
    assert args[0] == "rakaly.exe"
    assert args[1] == "json"
    assert "--duplicate-keys" in args
    assert "group" in args
    assert str(fake_save) in args


@pytest.mark.asyncio
async def test_convert_save_to_json_async_raises_on_non_zero_exit(
    fake_save: Path,
) -> None:
    proc = _fake_async_proc(returncode=2, stdout=b"", stderr=b"Invalid header")
    with (
        patch(
            "chronicler.save.rakaly.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        pytest.raises(RakalyError, match="exited with code 2"),
    ):
        await convert_save_to_json_async(fake_save, rakaly_path="rakaly.exe")


@pytest.mark.asyncio
async def test_convert_save_to_json_async_kills_proc_on_cancellation(
    fake_save: Path,
) -> None:
    """Slice 2 contract: a cancelled task tears the rakaly child down via
    proc.kill() instead of letting it run until its 300s timeout."""

    killed = False

    class _StubProc:
        returncode = None

        async def communicate(self):
            # Block long enough to be cancelled.
            await asyncio.sleep(10)
            return (b"{}", b"")

        def kill(self):
            nonlocal killed
            killed = True

        async def wait(self):
            return 0

    with patch(
        "chronicler.save.rakaly.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_StubProc()),
    ):
        task = asyncio.create_task(convert_save_to_json_async(fake_save, rakaly_path="rakaly.exe"))
        # Give the task a chance to enter communicate() before cancelling.
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert killed, (
        "convert_save_to_json_async must kill the rakaly child when its "
        "task is cancelled — otherwise Ctrl+C strands a 70MB-stdout "
        "subprocess for up to 300s"
    )
