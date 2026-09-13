"""Issue #20: the `chronicler init-prose` command surface."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from chronicler.cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data"))


def test_init_prose_scaffolds_the_given_path(runner, tmp_path) -> None:
    target = tmp_path / "chronicle"
    result = runner.invoke(app, ["init-prose", str(target), "--no-git"])
    assert result.exit_code == 0, result.output
    assert (target / "CLAUDE.md").is_file()
    assert (target / "voice" / "biography.md").is_file()
    assert str(target) in result.output


def test_init_prose_defaults_to_the_data_dir(runner, tmp_path) -> None:
    result = runner.invoke(app, ["init-prose", "--no-git"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "data" / "prose" / "CLAUDE.md").is_file()


def test_init_prose_is_idempotent_and_exits_zero(runner, tmp_path) -> None:
    """A user re-running the command should get a clear "already there"
    message and a success exit code, not an error."""
    target = tmp_path / "chronicle"
    runner.invoke(app, ["init-prose", str(target), "--no-git"])
    (target / "CLAUDE.md").write_text("MY RULES\n", encoding="utf-8")

    result = runner.invoke(app, ["init-prose", str(target), "--no-git"])

    assert result.exit_code == 0, result.output
    assert "already" in result.output.lower()
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "MY RULES\n"


def test_init_prose_refuses_a_non_empty_directory_with_exit_1(runner, tmp_path) -> None:
    target = tmp_path / "documents"
    target.mkdir()
    (target / "taxes.pdf").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["init-prose", str(target), "--no-git"])

    assert result.exit_code == 1
    assert "not empty" in result.output.lower()
    assert not (target / "CLAUDE.md").exists()


def test_init_prose_prints_next_steps(runner, tmp_path) -> None:
    """A public user's next question after scaffolding is "now what" — the
    command answers it rather than leaving them to find the docs."""
    target = tmp_path / "chronicle"
    result = runner.invoke(app, ["init-prose", str(target), "--no-git"])
    assert result.exit_code == 0
    lowered = result.output.lower()
    assert "claude.md" in lowered
    assert "doctor" in lowered


def test_init_prose_records_settings_so_the_app_finds_it(runner, tmp_path) -> None:
    from chronicler.config import resolve_prose_repo_path

    target = tmp_path / "chronicle"
    result = runner.invoke(app, ["init-prose", str(target), "--no-git"])
    assert result.exit_code == 0
    assert resolve_prose_repo_path().value == target
