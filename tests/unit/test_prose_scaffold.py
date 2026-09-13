"""Issue #20: `chronicler init-prose` and the scaffold it copies.

Replaces the setup step that used to be "clone the maintainer's private
prose repo", which a public user cannot do. The scaffold logic lives apart
from the CLI command so the Settings "Initialize" button (#23) can call
the same code path rather than reimplementing it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chronicler.narrative.prose_scaffold import (
    ScaffoldResult,
    default_prose_path,
    scaffold_prose_dir,
    template_root,
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Never touch the real settings file. Without this, running the suite
    on a configured machine rewrites the owner's prose_repo_path."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data"))


def test_template_root_is_the_shipped_template() -> None:
    root = template_root()
    assert (root / "CLAUDE.md").is_file()
    assert (root / "voice" / "biography.md").is_file()


def test_default_prose_path_is_under_the_data_dir(tmp_path, monkeypatch) -> None:
    """Terminal default is the scaffold target, not a hardcoded Windows
    path (the old DEFAULT_PROSE_REPO_PATH was `C:/git/...`, which is wrong
    on every other machine and for every public user)."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data"))
    assert default_prose_path() == tmp_path / "data" / "prose"


def test_scaffold_creates_the_full_template(tmp_path) -> None:
    target = tmp_path / "prose"
    result = scaffold_prose_dir(target, git_init=False)

    assert isinstance(result, ScaffoldResult)
    assert result.created is True
    assert result.already_initialised is False
    assert result.path == target
    for rel in (
        "CLAUDE.md",
        "README.md",
        "voice/biography.md",
        "voice/biography-woven.md",
        "voice/chronicle-export.md",
        "biographies/_smoke/synthetic-jarl-v1.md",
        "briefings/_smoke/synthetic-jarl-v1.md",
    ):
        assert (target / rel).is_file(), rel


def test_scaffolded_dir_satisfies_the_system_prompt_assembly(tmp_path) -> None:
    """The whole point: a fresh scaffold must be able to generate. #19's
    assembly fails loud on a missing register or voice file, so this is
    the end-to-end proof that the manifest is complete for every kind."""
    from chronicler.narrative.prose_io import assemble_system_prompt

    target = tmp_path / "prose"
    scaffold_prose_dir(target, git_init=False)
    for kind in ("biography", "biography_woven", "chronicle_export"):
        assembled = assemble_system_prompt(prose_repo=target, kind=kind)
        assert assembled.strip(), kind
        # register first, then the kind's voice rules — both present
        assert "chronicler" in assembled.lower(), kind


def test_second_run_is_a_no_op_and_does_not_overwrite(tmp_path) -> None:
    """Idempotent: a user re-running the command must not lose edits to
    their own craft rules, which is the file they are most likely to have
    customised."""
    target = tmp_path / "prose"
    scaffold_prose_dir(target, git_init=False)
    (target / "CLAUDE.md").write_text("MY OWN EDITED RULES\n", encoding="utf-8")

    result = scaffold_prose_dir(target, git_init=False)

    assert result.already_initialised is True
    assert result.created is False
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "MY OWN EDITED RULES\n"


def test_refuses_a_non_empty_directory_that_is_not_a_scaffold(tmp_path) -> None:
    """Pointing init-prose at the wrong directory (a documents folder, a
    source checkout) must fail loud rather than scatter template files
    through it."""
    target = tmp_path / "not-prose"
    target.mkdir()
    (target / "important.txt").write_text("do not clobber me", encoding="utf-8")

    with pytest.raises(ValueError, match="not empty"):
        scaffold_prose_dir(target, git_init=False)

    assert (target / "important.txt").read_text(encoding="utf-8") == "do not clobber me"
    assert not (target / "CLAUDE.md").exists()


def test_an_empty_existing_directory_is_fine(tmp_path) -> None:
    target = tmp_path / "prose"
    target.mkdir()
    result = scaffold_prose_dir(target, git_init=False)
    assert result.created is True
    assert (target / "CLAUDE.md").is_file()


def test_a_dir_with_only_dotfiles_is_treated_as_empty(tmp_path) -> None:
    """A directory the user already `git init`ed, or one macOS dropped a
    .DS_Store into, is still an empty prose dir for our purposes."""
    target = tmp_path / "prose"
    (target / ".git").mkdir(parents=True)
    (target / ".DS_Store").write_text("", encoding="utf-8")
    result = scaffold_prose_dir(target, git_init=False)
    assert result.created is True
    assert (target / "CLAUDE.md").is_file()


def test_records_the_path_in_settings(tmp_path) -> None:
    from chronicler.settings_store import load_settings

    target = tmp_path / "prose"
    scaffold_prose_dir(target, git_init=False)
    assert load_settings().get("prose_repo_path") == str(target)


def test_settings_are_recorded_on_a_second_run_too(tmp_path) -> None:
    """A user who scaffolded before the settings key existed (or wiped
    settings.json) must be able to re-point the app by re-running."""
    from chronicler.settings_store import load_settings, update_settings

    target = tmp_path / "prose"
    scaffold_prose_dir(target, git_init=False)
    update_settings({"prose_repo_path": "/somewhere/stale"})
    scaffold_prose_dir(target, git_init=False)
    assert load_settings().get("prose_repo_path") == str(target)


def _forget_the_global_git_identity(tmp_path, monkeypatch) -> None:
    """Make the process look like a machine that has never run `git config
    --global user.email` — a fresh public-user box, or a CI runner.

    Pointing the config env vars at paths that do not exist is portable
    (git treats a missing config file as an empty one); `os.devnull` is not,
    since it is `nul` on Windows. The GIT_AUTHOR_*/GIT_COMMITTER_* vars are
    cleared too, since they would supply an identity even with no config.
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


def test_git_init_makes_a_repo_with_one_commit(tmp_path, monkeypatch) -> None:
    """Issue #35: this has to hold with no global git identity configured.
    It used to pass only on a developer machine that had one, and the CI
    runner (which has none) was the first thing to notice."""
    _forget_the_global_git_identity(tmp_path, monkeypatch)
    target = tmp_path / "prose"
    result = scaffold_prose_dir(target, git_init=True)
    assert result.git_initialised is True
    assert (target / ".git").is_dir()
    log = subprocess.run(
        ["git", "-C", str(target), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_scaffold_commit_does_not_claim_the_users_identity(tmp_path, monkeypatch) -> None:
    """The scaffold commit is chronicler's own bookkeeping, so it carries
    chronicler's identity — but via `-c`, so nothing is written into the
    user's new repo that would hijack authorship of their later commits."""
    _forget_the_global_git_identity(tmp_path, monkeypatch)
    target = tmp_path / "prose"
    scaffold_prose_dir(target, git_init=True)

    author = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--pretty=%an <%ae>"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert author.stdout.strip() == "CK3 Chronicler <chronicler@localhost>"

    # No identity persisted in the repo's own config: a user who pushes this
    # repo commits as themselves, not as chronicler.
    configured = subprocess.run(
        ["git", "-C", str(target), "config", "--local", "--get-regexp", "^user\\."],
        capture_output=True,
        text=True,
    )
    assert configured.stdout.strip() == ""


def test_git_init_is_skipped_when_a_repo_already_exists(tmp_path) -> None:
    target = tmp_path / "prose"
    target.mkdir()
    subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
    result = scaffold_prose_dir(target, git_init=True)
    assert result.created is True
    assert result.git_initialised is False


def test_git_failure_is_best_effort_and_never_loses_the_scaffold(tmp_path, monkeypatch) -> None:
    """Auto-commit already degrades gracefully on a non-git target, so a
    missing or broken git must not fail the scaffold — the files are what
    matter."""
    target = tmp_path / "prose"

    def boom(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", boom)
    result = scaffold_prose_dir(target, git_init=True)

    assert result.created is True
    assert result.git_initialised is False
    assert (target / "CLAUDE.md").is_file()


def test_scaffold_never_copies_pycache_or_stray_files(tmp_path) -> None:
    """The copy is manifest-driven, not a blind copytree — so a stray file
    that lands in prose-template/ locally cannot ride along into a user's
    directory."""
    target = tmp_path / "prose"
    scaffold_prose_dir(target, git_init=False)
    names = {p.name for p in target.rglob("*") if p.is_file()}
    assert not any(n.endswith(".pyc") for n in names)
    assert "__pycache__" not in {p.name for p in target.rglob("*")}


def test_target_may_be_a_file_path_and_fails_clearly(tmp_path) -> None:
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        scaffold_prose_dir(target, git_init=False)


def test_relative_and_user_paths_are_expanded(tmp_path, monkeypatch) -> None:
    """`init-prose ~/chronicle` must not create a literal '~' directory.

    Issue #35: patching only HOME redirected `~` on POSIX but not on
    Windows, where `Path.expanduser()` goes through `ntpath.expanduser` and
    consults USERPROFILE first (then HOMEDRIVE+HOMEPATH) and never reads
    HOME — so the test scaffolded into the runner's real profile directory
    and compared it against tmp_path. Set what each platform actually
    reads; the irrelevant one is harmless.
    """
    monkeypatch.setenv("HOME", str(tmp_path))  # POSIX
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    result = scaffold_prose_dir(Path("~/chronicle"), git_init=False)
    assert result.path == tmp_path / "chronicle"
    assert (tmp_path / "chronicle" / "CLAUDE.md").is_file()
