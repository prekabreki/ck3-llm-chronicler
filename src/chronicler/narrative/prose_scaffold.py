"""Issue #20: scaffold a user's prose directory from the shipped template.

Before this, setting up a chronicler meant cloning the maintainer's
private prose repo — impossible for a public user and the single hardest
blocker in the setup path. `chronicler init-prose` copies the shippable
instruction layer (`prose-template/`) to a directory the user owns,
git-inits it, and records the path in settings.

Kept out of the CLI module on purpose: the Settings "Initialize" button
(#23) calls this same function rather than reimplementing the copy, so
there is one definition of what a valid prose directory looks like.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The manifest, relative to the template root. An explicit list rather
# than copytree: a stray file that lands in prose-template/ locally (a
# scratch note, an editor backup, a __pycache__) must not be able to ride
# along into a user's directory. The guard test in
# tests/unit/test_prose_template.py pins the same set from the other side.
TEMPLATE_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "README.md",
    "voice/biography.md",
    "voice/biography-woven.md",
    "voice/chronicle-export.md",
    "biographies/.gitkeep",
    "biographies/_smoke/synthetic-jarl-v1.md",
    "briefings/.gitkeep",
    "briefings/_smoke/synthetic-jarl-v1.md",
)

# Presence of this file is what marks a directory as an existing scaffold
# (so a re-run is a no-op instead of clobbering the user's edited rules).
_SCAFFOLD_MARKER = "CLAUDE.md"

_GIT_TIMEOUT_SECONDS = 30.0
_INITIAL_COMMIT_MESSAGE = "chore: scaffold chronicle directory (chronicler init-prose)"

# Issue #35: the scaffold commit carries chronicler's own identity, passed
# with `-c` for this one invocation. Without it, `git commit` exits non-zero
# on any machine with no global user.email/user.name — a fresh public-user
# box or a CI runner — and the scaffold silently degraded to "no repo",
# which is the worst possible first-run experience for the one command whose
# entire job is unblocking setup.
#
# `-c` rather than `git config` in the new repo, deliberately: the initial
# commit is chronicler's bookkeeping, but the prose repo is the *user's* and
# they may push it under their own name. Writing a local identity would
# override the identity they already have (or later set) for every biography
# auto-commit thereafter. This way chronicler signs only its own commit and
# the user's commits stay theirs.
_SCAFFOLD_COMMITTER_NAME = "CK3 Chronicler"
_SCAFFOLD_COMMITTER_EMAIL = "chronicler@localhost"


@dataclass(frozen=True)
class ScaffoldResult:
    path: Path
    created: bool
    already_initialised: bool
    git_initialised: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def template_root() -> Path:
    """Locate the shipped ``prose-template/``.

    Distribution is source + launchers (spec §0), so the template sits at
    the repo root: from ``src/chronicler/narrative/prose_scaffold.py``
    that is ``parents[3]``. A wheel install that relocates the package
    would miss it, which is why this raises a message naming the expected
    location rather than returning a path that silently doesn't exist.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "prose-template",  # editable / source checkout
        here.parents[2] / "prose-template",  # package-data layout
    ]
    for candidate in candidates:
        if (candidate / _SCAFFOLD_MARKER).is_file():
            return candidate
    raise RuntimeError(
        "cannot locate the bundled prose-template/ (looked in: "
        + ", ".join(str(c) for c in candidates)
        + "). Chronicler is distributed as a source checkout — run from a "
        "clone, or point CHRONICLER_PROSE_REPO_PATH at an existing prose "
        "directory instead."
    )


def default_prose_path() -> Path:
    """Where `init-prose` scaffolds when the user names no path.

    Under the platform data dir beside the campaign databases, so the
    chronicle travels with the rest of the user's chronicler state.
    """
    from chronicler.config import chronicler_data_dir

    return chronicler_data_dir() / "prose"


def looks_like_prose_dir(path: Path) -> bool:
    """Whether ``path`` already holds a chronicler prose directory."""
    return (path / _SCAFFOLD_MARKER).is_file()


def _is_effectively_empty(path: Path) -> bool:
    """Dotfile-only directories count as empty.

    A directory the user already ``git init``ed, or one that macOS
    dropped a ``.DS_Store`` into, is still an empty prose dir — refusing
    those would be a confusing dead end.
    """
    return not any(child for child in path.iterdir() if not child.name.startswith("."))


def scaffold_prose_dir(
    target: Path,
    *,
    git_init: bool = True,
    record_in_settings: bool = True,
) -> ScaffoldResult:
    """Copy the template to ``target``, git-init it, record it in settings.

    Idempotent: a second run over an existing scaffold copies nothing (the
    user's edited craft rules are the file they are most likely to have
    customised) but still re-records the path, so a wiped settings.json
    can be repaired by re-running.

    Raises :class:`ValueError` when ``target`` is a file, or a non-empty
    directory that is not already a scaffold — scattering template files
    through a mistyped path is worse than refusing.
    """
    target = Path(target).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()

    if target.exists() and not target.is_dir():
        raise ValueError(f"{target} is not a directory; init-prose needs a directory path")

    notes: list[str] = []
    already = target.is_dir() and looks_like_prose_dir(target)

    if already:
        notes.append(f"{target} already holds a chronicle directory — left untouched")
    else:
        if target.is_dir() and not _is_effectively_empty(target):
            raise ValueError(
                f"{target} is not empty and does not look like a chronicle "
                "directory (no CLAUDE.md). Refusing to write template files "
                "into it — pass an empty or new path."
            )
        _copy_template(target)
        notes.append(f"scaffolded the chronicle template into {target}")

    git_initialised = False
    if git_init:
        git_initialised = _git_init_best_effort(target, notes)

    if record_in_settings:
        _record_path(target, notes)

    return ScaffoldResult(
        path=target,
        created=not already,
        already_initialised=already,
        git_initialised=git_initialised,
        notes=tuple(notes),
    )


def _copy_template(target: Path) -> None:
    root = template_root()
    for rel in TEMPLATE_FILES:
        src = root / rel
        if not src.is_file():
            raise RuntimeError(f"the bundled template is incomplete: {rel} is missing from {root}")
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _git_init_best_effort(target: Path, notes: list[str]) -> bool:
    """``git init`` + one commit, best-effort.

    Auto-commit of biographies already degrades gracefully on a non-git
    target (prose_io.git_commit_biography), so a missing or broken git
    must never fail the scaffold — the instruction files are what matter.
    """
    if (target / ".git").exists():
        notes.append("existing git repository left as it is")
        return False
    try:
        for argv in (
            ["git", "-C", str(target), "init", "-q"],
            ["git", "-C", str(target), "add", "-A"],
            [
                "git",
                "-C",
                str(target),
                "-c",
                f"user.name={_SCAFFOLD_COMMITTER_NAME}",
                "-c",
                f"user.email={_SCAFFOLD_COMMITTER_EMAIL}",
                "commit",
                "-q",
                "-m",
                _INITIAL_COMMIT_MESSAGE,
            ],
        ):
            subprocess.run(
                argv,
                check=True,
                capture_output=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning(
            "init-prose: could not initialise a git repository in %s (%s); "
            "the chronicle files are in place and auto-commit will simply "
            "no-op until this is a repo",
            target,
            type(exc).__name__,
        )
        notes.append("git init skipped (git unavailable or failed) — files are in place")
        return False
    notes.append("initialised a git repository with the scaffold as its first commit")
    return True


def _record_path(target: Path, notes: list[str]) -> None:
    try:
        from chronicler.settings_store import update_settings

        update_settings({"prose_repo_path": str(target)})
    except Exception:  # noqa: BLE001 — settings are best-effort, never fatal
        log.exception("init-prose: could not record prose_repo_path in settings")
        notes.append(f"could not write settings — set CHRONICLER_PROSE_REPO_PATH={target} instead")
        return
    notes.append("recorded the path in settings")
