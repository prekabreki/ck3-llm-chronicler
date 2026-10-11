"""Tests for the ``chronicler launch`` verb (issue #9).

All subprocess spawning, the HTTP probe and the openers (Steam / browser)
are mocked so no real server, game or browser is involved.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from chronicler.cli import ingest
from chronicler.cli.main import app


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _patch_openers(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[bool], list[str]]:
    game_calls: list[bool] = []
    browser_calls: list[str] = []
    monkeypatch.setattr("chronicler.cli.ingest._launch_game", lambda: game_calls.append(True))
    monkeypatch.setattr(
        "chronicler.cli.ingest._open_browser", lambda url: browser_calls.append(url)
    )
    return game_calls, browser_calls


class _FakeResponse:
    def __init__(self, code: int) -> None:
        self._code = code

    def getcode(self) -> int:
        return self._code

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_launch_listed_in_root_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "launch" in result.stdout
    assert "start dev + CK3 + browser in one click" in result.stdout


def test_launch_help_lists_flags() -> None:
    """The declared params are the contract ``--help`` is generated from; parse
    them rather than the rendered text, which rich interleaves with ANSI colour
    codes under forced colour (ck3_chronicler-27ov.16)."""
    import typer

    launch = typer.main.get_command(app).commands["launch"]
    declared = {opt for param in launch.params for opt in param.opts}
    for required in ("--port", "--no-game", "--no-browser"):
        assert required in declared, f"launch must declare {required}; got {sorted(declared)}"


def test_launcher_log_path_under_data_dir(env: Path) -> None:
    assert ingest._launcher_log_path() == env / "logs" / "launcher.log"


def test_server_is_running_probes_provider_status_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        requested["url"] = url
        requested["timeout"] = timeout
        return _FakeResponse(200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert ingest._server_is_running("http://127.0.0.1:8000/") is True
    assert requested["url"] == "http://127.0.0.1:8000/api/settings/provider-status"
    assert requested["timeout"] == 1.0


def test_server_is_running_treats_foreign_process_as_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: _FakeResponse(404))
    assert ingest._server_is_running("http://127.0.0.1:8000/") is False


def test_server_is_running_treats_refusal_as_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, timeout: float | None = None) -> _FakeResponse:
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert ingest._server_is_running("http://127.0.0.1:8000/") is False


def test_spawn_dev_detaches_and_redirects_to_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(cmd: list[str], **kwargs: object) -> Mock:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return Mock()

    monkeypatch.setattr("chronicler.cli.ingest.subprocess.Popen", fake_popen)
    log_file = tmp_path / "logs" / "launcher.log"

    ingest._spawn_dev(8000, log_file)

    assert captured["cmd"] == [sys.executable, "-m", "chronicler", "dev", "--port", "8000"]
    kwargs = captured["kwargs"]
    if sys.platform == "win32":
        assert "creationflags" in kwargs
        assert "start_new_session" not in kwargs
    else:
        assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"].name == str(log_file)
    assert kwargs["stderr"].name == str(log_file)


def test_spawn_dev_detaches_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    captured: dict[str, object] = {}

    def fake_popen(cmd: list[str], **kwargs: object) -> Mock:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return Mock()

    monkeypatch.setattr("chronicler.cli.ingest.subprocess.Popen", fake_popen)
    log_file = tmp_path / "logs" / "launcher.log"

    ingest._spawn_dev(8000, log_file)

    kwargs = captured["kwargs"]
    assert "start_new_session" not in kwargs
    assert kwargs["creationflags"] == 0x8 | 0x200 | 0x08000000


def test_launch_already_running_reuses_server_and_opens(
    env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_calls, browser_calls = _patch_openers(monkeypatch)
    popen_calls: list[tuple] = []

    monkeypatch.setattr("chronicler.cli.ingest._server_is_running", lambda url: True)
    monkeypatch.setattr(
        "chronicler.cli.ingest.subprocess.Popen",
        lambda *a, **k: popen_calls.append((a, k)),
    )

    result = runner.invoke(app, ["launch"])
    assert result.exit_code == 0, result.output
    assert popen_calls == []
    assert game_calls == [True]
    assert browser_calls == ["http://127.0.0.1:8000/"]


def test_launch_fresh_start_spawns_then_opens(
    env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_calls, browser_calls = _patch_openers(monkeypatch)
    child = Mock()
    child.poll.return_value = None
    popen_cmds: list[list[str]] = []
    popen_kwargs: dict[str, object] = {}

    states = iter([False, True])
    monkeypatch.setattr("chronicler.cli.ingest._server_is_running", lambda url: next(states))

    def fake_popen(cmd: list[str], **kwargs: object) -> Mock:
        popen_cmds.append(cmd)
        popen_kwargs.update(kwargs)
        return child

    monkeypatch.setattr("chronicler.cli.ingest.subprocess.Popen", fake_popen)

    result = runner.invoke(app, ["launch", "--port", "9000"])
    assert result.exit_code == 0, result.output
    assert popen_cmds[0] == [sys.executable, "-m", "chronicler", "dev", "--port", "9000"]
    if sys.platform == "win32":
        assert "creationflags" in popen_kwargs
    else:
        assert popen_kwargs["start_new_session"] is True
    assert game_calls == [True]
    assert browser_calls == ["http://127.0.0.1:9000/"]


def test_launch_child_dies_exits_nonzero_naming_log(
    env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_openers(monkeypatch)
    child = Mock()
    child.poll.return_value = 1

    monkeypatch.setattr("chronicler.cli.ingest._server_is_running", lambda url: False)
    monkeypatch.setattr("chronicler.cli.ingest.subprocess.Popen", lambda *a, **k: child)

    result = runner.invoke(app, ["launch"])
    assert result.exit_code == 1
    assert "exited before" in result.output
    assert "launcher.log" in result.output


def test_launch_times_out_exits_nonzero_naming_log(
    env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_openers(monkeypatch)
    child = Mock()
    child.poll.return_value = None

    monkeypatch.setattr("chronicler.cli.ingest._server_is_running", lambda url: False)
    monkeypatch.setattr("chronicler.cli.ingest.subprocess.Popen", lambda *a, **k: child)
    monkeypatch.setattr("chronicler.cli.ingest._POLL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("chronicler.cli.ingest._POLL_INTERVAL_SECONDS", 0.001)

    result = runner.invoke(app, ["launch"])
    assert result.exit_code == 1
    assert "timed out" in result.output
    assert "launcher.log" in result.output


def test_launch_no_game_no_browser_skips_openers(
    env: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_calls, browser_calls = _patch_openers(monkeypatch)
    child = Mock()
    child.poll.return_value = None

    states = iter([False, True])
    monkeypatch.setattr("chronicler.cli.ingest._server_is_running", lambda url: next(states))
    monkeypatch.setattr("chronicler.cli.ingest.subprocess.Popen", lambda *a, **k: child)

    result = runner.invoke(app, ["launch", "--no-game", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert game_calls == []
    assert browser_calls == []
