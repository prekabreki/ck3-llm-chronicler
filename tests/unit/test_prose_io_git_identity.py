"""Issue #36: git identity fallback for biography auto-commits.

When no global git identity is configured, git_commit_biography falls back
to chronicler's own identity (same as the scaffold commit) rather than
silently failing to commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chronicler.narrative import prose_io


def _forget_the_global_git_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the process look like a machine with no git identity.

    Same pattern as
    tests/unit/test_prose_scaffold.py::_forget_the_global_git_identity.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)


def _setup_prose_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with the expected directory structure."""
    repo = tmp_path / "prose"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    brief_dir = repo / "briefings" / "test-campaign"
    bio_dir = repo / "biographies" / "test-campaign"
    brief_dir.mkdir(parents=True)
    bio_dir.mkdir(parents=True)
    (brief_dir / "char1-v1.md").write_text("Test briefing")
    (bio_dir / "char1-v1.md").write_text("Test biography")
    return repo


@pytest.mark.asyncio
async def test_commit_falls_back_to_chronicler_identity_when_no_global_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no global git identity, the commit must succeed and be authored
    by chronicler's identity (same as the scaffold)."""
    _forget_the_global_git_identity(tmp_path, monkeypatch)
    repo = _setup_prose_repo(tmp_path)

    await prose_io.git_commit_biography(
        prose_repo=repo,
        rel_brief="briefings/test-campaign/char1-v1.md",
        rel_bio="biographies/test-campaign/char1-v1.md",
        character_id="char1",
        campaign_uuid="test-campaign",
        version=1,
        model="test-model",
    )

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%an <%ae>"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "CK3 Chronicler <chronicler@localhost>"


@pytest.mark.asyncio
async def test_commit_uses_real_identity_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a global git identity configured, the commit must be authored
    by that identity, not chronicler's — the probe must not override it."""
    gitconfig = tmp_path / "global-gitconfig"
    gitconfig.write_text("[user]\n\tname = Real User\n\temail = real@user.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-system-gitconfig"))
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)

    repo = _setup_prose_repo(tmp_path)

    await prose_io.git_commit_biography(
        prose_repo=repo,
        rel_brief="briefings/test-campaign/char1-v1.md",
        rel_bio="biographies/test-campaign/char1-v1.md",
        character_id="char1",
        campaign_uuid="test-campaign",
        version=1,
        model="test-model",
    )

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%an <%ae>"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "Real User <real@user.com>"
