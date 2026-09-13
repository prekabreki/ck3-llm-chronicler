"""Best-effort git plumbing for archive sync (ck3_chronicler-27ov.54 / M-P5).

``_run_git`` (the timeout/no-raise subprocess wrapper) and the shared
``_stage_commit_push`` add->commit->push ladder, split out of
archive_export.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from chronicler.util import win_subprocess

# Conservative timeout per git invocation. The push step can hit the
# network so we leave a generous window — 60s covers slow uploads on a
# residential connection while still failing fast if something is
# genuinely hung. Snapshot + commit are local-only and finish in
# milliseconds; the timeout is a defence against a wedged git process,
# not a perf budget.
_GIT_TIMEOUT_SECONDS = 60


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    """Thin wrapper. Captures stdout/stderr, applies a timeout, never
    raises on non-zero exit. Callers inspect ``returncode``."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            # ck3_chronicler-w26q: suppress the console-window flash this git
            # call would otherwise pop on Windows — the closing/archive flow
            # fires it many times in a row.
            creationflags=win_subprocess.creationflags(),
            startupinfo=win_subprocess.startupinfo(),
        )
    except FileNotFoundError:
        # git binary missing — chronicler running on a machine without
        # git installed. Surface a synthesised CompletedProcess so the
        # caller's returncode-based error path handles this uniformly.
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=127,
            stdout="",
            stderr="git executable not found",
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=124,
            stdout="",
            stderr=f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s",
        )


def _stage_commit_push(repo_root: Path, paths: list[str], message: str) -> tuple[bool, bool, str]:
    """Run the ``git add -A`` -> ``commit`` -> ``push`` ladder for ``paths`` once.

    ck3_chronicler-27ov.54 (M-P5): export_sealed_campaign and
    tombstone_campaign both hand-rolled this identical sequence with ten
    near-duplicate ExportResult constructions. Factored out here; the
    callers translate the outcome into their own ExportResult flag
    conventions (``snapshot_written``/``sidecar_written`` differ between
    seal and tombstone).

    Best-effort: never raises (``_run_git`` synthesises a CompletedProcess
    for a missing/wedged git). Returns ``(committed, pushed, detail)``:

    - add fails        -> ``(False, False, "git add failed: ...")``
    - nothing to commit -> ``(False, False, "nothing to commit")`` (benign no-op)
    - commit fails     -> ``(False, False, "git commit failed: ...")``
    - push fails       -> ``(True, False, "push failed: ...")`` (commit is local)
    - success          -> ``(True, True, "pushed")``

    ``git add -A`` (vs a bare ``git add``) is used so a path that was
    deleted on disk — the tombstone case — has its removal staged; for
    freshly-written paths it behaves identically to a plain add.
    """
    add_proc = _run_git(repo_root, "add", "-A", "--", *paths)
    if add_proc.returncode != 0:
        return False, False, f"git add failed: {add_proc.stderr.strip() or add_proc.returncode}"

    commit_proc = _run_git(repo_root, "commit", "-m", message, "--only", "--", *paths)
    if commit_proc.returncode != 0:
        if "nothing to commit" in (commit_proc.stdout + commit_proc.stderr).lower():
            return False, False, "nothing to commit"
        err = (commit_proc.stderr or commit_proc.stdout).strip()
        return False, False, f"git commit failed: {err or commit_proc.returncode}"

    push_proc = _run_git(repo_root, "push")
    if push_proc.returncode != 0:
        err = (push_proc.stderr or push_proc.stdout).strip()
        return True, False, f"push failed: {err or push_proc.returncode}"

    return True, True, "pushed"
