"""ck3_chronicler-cs1o: prose-repo artifact I/O shared by both narrative
transports.

The prose repo is the system of record regardless of WHO generated the
text — every briefing + biography pair lands on disk under
``briefings/<campaign>/`` + ``biographies/<campaign>/`` and is
git-committed, whether the prose came from ``claude --print``
(ClaudeCodeProvider) or the direct Messages API (AnthropicProvider).
This module is the extraction of that side-effect machinery out of
claude_code.py so the two transports cannot drift.

Everything here was moved verbatim from claude_code.py (tbrm/0224/cj2f/
sezy/vcv2 lineage — original issue tags preserved on each function);
the only behavioural extension is the ``agent`` parameter on
:func:`format_attribution_footer`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import subprocess
import sys
import weakref
from pathlib import Path

from chronicler.narrative.prose_scaffold import (
    _SCAFFOLD_COMMITTER_EMAIL,
    _SCAFFOLD_COMMITTER_NAME,
)
from chronicler.narrative.provider import NarrativeRequest

log = logging.getLogger(__name__)

CHRONICLER_PROSE_REPO_PATH_ENV = "CHRONICLER_PROSE_REPO_PATH"


def get_prose_repo_path() -> Path:
    """Resolve the prose directory with settings-file precedence.

    ck3_chronicler-tbrm.4: defers to :func:`config.resolve_prose_repo_path`
    so the user's Settings → Prose repo override applies when the provider
    is constructed.

    Issue #20: this module used to carry its own
    ``DEFAULT_PROSE_REPO_PATH = Path("C:/git/ck3_chronicler_prose")`` as a
    last-resort fallback — an OS-unaware duplicate of config's, so on
    Linux the two resolvers disagreed about where the prose lived. One
    resolver now, and its terminal default is the ``init-prose`` target.
    """
    from chronicler.config import default_prose_repo_path, resolve_prose_repo_path

    resolved = resolve_prose_repo_path()
    if resolved.value is not None:
        return resolved.value
    return default_prose_repo_path()


_VERSION_FILENAME_RE = re.compile(r"-v(\d+)\.md$")


def next_version(target_dir: Path, basename: str) -> int:
    """Return the next free ``vN`` for ``<target_dir>/<basename>-vN.md``.

    Reads the directory once; ``v1`` for an empty/absent dir. Filenames
    that don't match ``<basename>-v<digits>.md`` are ignored — only the
    canonical scheme contributes to the version count, so a stray
    file ``<basename>-v3-old.md`` won't shift the next version.
    """
    if not target_dir.is_dir():
        return 1
    prefix = f"{basename}-v"
    versions: list[int] = []
    for entry in target_dir.iterdir():
        if not entry.name.startswith(prefix):
            continue
        match = _VERSION_FILENAME_RE.search(entry.name)
        if match is None:
            continue
        try:
            versions.append(int(match.group(1)))
        except ValueError:
            continue
    return (max(versions) + 1) if versions else 1


def briefing_paths(
    *,
    prose_repo: Path,
    campaign_uuid: str,
    character_id: str,
) -> tuple[Path, Path, int]:
    """Compute (briefing_path, biography_path, version) for a request.

    Version is the next free ``vN`` for any biography that already
    exists under ``biographies/<campaign>/`` for this character —
    matches the chronicler's existing ``Biography.version`` semantics.
    Briefing and biography share the same version so they pair up on
    disk and in git diffs.
    """
    bio_dir = prose_repo / "biographies" / campaign_uuid
    brief_dir = prose_repo / "briefings" / campaign_uuid
    version = next_version(bio_dir, character_id)
    bio_path = bio_dir / f"{character_id}-v{version}.md"
    brief_path = brief_dir / f"{character_id}-v{version}.md"
    return brief_path, bio_path, version


# The lean-path wrapper that precedes the briefing in the user turn.
# Issue #21: hoisted here from the transports. It was written once in
# claude_code.py and copied verbatim into anthropic.py, and a third copy
# for the openai-compatible transport is a third chance for the output
# contract to drift — a transport whose wrapper says something slightly
# different produces prose with a different shape (preambles, file paths,
# process narration) while every test still passes. One constant, three
# transports.
BRIEFING_WRAPPER_INSTRUCTION = (
    "Below is the briefing for one character. Write their "
    "biography in full, following the chronicler register in your "
    "system prompt. Output ONLY the finished prose — no preamble, "
    "no commentary, no file paths, no narration of your process.\n\n"
    "--- BRIEFING ---\n"
)


def render_briefing_markdown(req: NarrativeRequest) -> str:
    """Materialise a NarrativeRequest into the markdown briefing format
    that the narrative transport reads.

    The ``user_prompt`` encodes everything kind-specific about THIS call
    (header, glossary, world context, events, active memories where
    applicable). We wrap it in a markdown header so the file is
    readable on its own and diffs cleanly in git.

    ck3_chronicler-d4ex (2026-05-13) embedded ``req.system_prompt`` here
    as a "Per-call instructions" block ABOVE the briefing body, so the
    kind-specific voice rules (notably the 1-2 sentence memory rule)
    couldn't be silently overridden by the biographer-voiced CLAUDE.md.

    ck3_chronicler-0224 (2026-05-27) relocated those per-call
    instructions out of the per-call briefing file and into static
    sibling files at ``<prose_repo>/voice/<kind>.md``, so the prompt
    cache could hit on a byte-identical static prefix instead of paying
    full price for ~3 KB of constant boilerplate every call.

    Issue #19: those voice files (and the ``CLAUDE.md`` register) now
    reach the model through ``NarrativeRequest.system_prompt``, built
    once by :func:`assemble_system_prompt` and shipped verbatim by
    whichever transport runs. The ``@voice/<kind>.md`` reference the
    header used to advertise died with the agentic wrapper in
    ck3_chronicler-27ov.14; nothing has read an @-reference since. This
    file stays purely the per-call briefing.
    """
    metadata = req.metadata or {}
    character_id = metadata.get("character_id", "(unknown)")
    campaign_uuid = metadata.get("campaign_uuid", "(unknown)")

    header = (
        f"# Briefing: character {character_id} — kind={req.kind} "
        f"(prompt_version={req.prompt_version}, campaign={campaign_uuid})\n\n"
        "Source of truth for this call. Every fact emitted must trace to this "
        "briefing. The chronicler register and this kind's voice + length "
        "rules arrive separately, in the system prompt.\n\n"
        "---\n\n"
    )
    return header + req.user_prompt.strip() + "\n"


# ck3_chronicler-0224: per-kind static voice file paths under
# <prose_repo>/voice/. Issue #19: read by assemble_system_prompt alone —
# both transports get the resulting text via
# NarrativeRequest.system_prompt and no longer resolve these paths
# themselves. Falls back to the empty string for unknown kinds: a
# custom kind gets the register without dedicated voice rules rather
# than an error.
_VOICE_FILES_BY_KIND: dict[str, str] = {
    "biography": "voice/biography.md",
    "biography_woven": "voice/biography-woven.md",
    "chronicle_export": "voice/chronicle-export.md",
}


def voice_file_for_kind(kind: str) -> str:
    """Return the prose-repo-relative voice file path for ``kind``.

    Empty string when the kind has no registered voice file (test
    fixtures, future kinds without dedicated voice rules).
    """
    return _VOICE_FILES_BY_KIND.get(kind, "")


def assemble_system_prompt(*, prose_repo: Path | None, kind: str) -> str:
    """Build the final system prompt for one call: register + kind voice.

    Issue #19: the single provider-neutral assembly step. Both
    request-building call sites (``pipeline.generate_biography``,
    ``closing.generate_closing_chronicle``) call this and put the
    result on :attr:`NarrativeRequest.system_prompt`; every transport
    then ships that string verbatim (claude-code writes it to a temp
    file for ``--system-prompt-file``, API transports put it in the
    system field). No provider reads prose-repo instruction files
    itself anymore, so the two transports cannot drift apart the way
    they did between 27ov.14 and this change — the claude-code route
    was sending ``CLAUDE.md`` alone, generating every woven biography
    without ``voice/biography-woven.md``.

    ``prose_repo=None`` means the provider declares no prose dir (test
    doubles — :attr:`NarrativeProvider.prose_repo_path` defaults to
    None) and yields an empty prompt. Every production transport
    resolves a real path, so the fail-loud checks below are what a
    misconfigured install actually hits.
    """
    if prose_repo is None:
        return ""
    if not prose_repo.is_dir():
        raise RuntimeError(_missing_prose_message(f"prose repo not found at {prose_repo}"))
    register = prose_repo / "CLAUDE.md"
    if not register.is_file():
        raise RuntimeError(_missing_prose_message(f"no CLAUDE.md register at {register}"))
    parts = [register.read_text(encoding="utf-8").strip()]
    voice_rel = voice_file_for_kind(kind)
    if voice_rel:
        voice = prose_repo / voice_rel
        if not voice.is_file():
            raise RuntimeError(
                _missing_prose_message(f"no voice file for kind={kind!r} at {voice}")
            )
        parts.append(voice.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


def _missing_prose_message(detail: str) -> str:
    """Fail-loud text shared by every assembly check.

    Names both recovery paths — the scaffold command and the override
    env var — because a user hitting this has no other signal: the old
    behaviour on this path was silent generic prose.
    """
    return (
        f"{detail}. The chronicler register lives in the prose directory: "
        f"run `chronicler init-prose` to scaffold one, or point "
        f"{CHRONICLER_PROSE_REPO_PATH_ENV} (or Settings → Prose repo) at an "
        f"existing one. Refusing to generate register-less prose."
    )


def format_attribution_footer(
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    agent: str = "Claude Code",
) -> str:
    """ck3_chronicler-vcv2-followup: render the model + token-count
    attribution line that gets appended to each biography file after
    the transport finishes writing the prose.

    Format: ``\\n\\n---\\n*Set down by <agent> · <model> · <in> in / <out> out*\\n``

    Tokens render with thousands separators when known. If both
    counts are missing (envelope unparseable) the segment collapses
    to just the attribution + model so the footer is never empty.
    The leading blank line + horizontal rule separate the footer
    from the prose so a reader (and a markdown renderer) can tell
    where the biography ends and the chronicler's metadata begins.

    ck3_chronicler-cs1o: ``agent`` names the transport ("Claude Code" |
    "Anthropic API") so the on-disk trail records who set the text down.
    """
    if input_tokens is not None and output_tokens is not None:
        token_segment = f" · {input_tokens:,} in / {output_tokens:,} out"
    elif input_tokens is not None:
        token_segment = f" · {input_tokens:,} in"
    elif output_tokens is not None:
        token_segment = f" · {output_tokens:,} out"
    else:
        token_segment = ""
    return f"\n\n---\n*Set down by {agent} · {model}{token_segment}*\n"


# ck3_chronicler-sezy (2026-05-08): defensive ceiling on each git
# subprocess call. A stuck index lock or pre-commit hook prompting for
# input would otherwise block the await indefinitely, blocking the
# pipeline in 'generating' state forever (queue strip ticks up
# elapsed time forever, biography is on disk but never marked done).
# 30 seconds is comfortably more than git add/diff/commit on a small
# repo would ever need; on timeout we kill the process, log a warning,
# and return — auto-commit is best-effort, the biography survives.
_GIT_SUBPROCESS_TIMEOUT_SECONDS = 30.0


def subprocess_creationflags() -> int:
    """ck3_chronicler-gcvm/2vhv: on Windows, give the spawned subtree a
    single *hidden* console so neither it nor its grandchildren pop a window.

    The ``claude`` CLI spawns its own grandchildren (node workers, git,
    ripgrep). A Windows process has at most one console, and a child with no
    explicit console flag *inherits* its parent's. So the flag that matters
    is which console the parent gets:

    - CREATE_NO_WINDOW (0x08000000): the process gets a console, created
      hidden. Grandchildren inherit that hidden console and stay silent.
    - DETACHED_PROCESS (0x00000008): the process gets NO console at all.
      The first console-subsystem grandchild then has nothing to inherit, so
      Windows allocates it a *fresh, visible* console — and our SW_HIDE
      STARTUPINFO doesn't reach grandchildren. That is the per-biography
      window flash reported in 2vhv (reliably reproduced via the SPA
      'Regenerate biography' button).

    So we keep CREATE_NO_WINDOW and deliberately do NOT set DETACHED_PROCESS.
    The hidden private console still keeps grandchild output (e.g. the
    spurious 'may have crashed in this repository earlier:' string that
    motivated gcvm) off the chronicler's own console. Pairs with
    :func:`subprocess_startupinfo`, which hides the immediate child's window.

    On non-Windows platforms, returns 0 (flags are Windows-only constants).
    """
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW


def subprocess_startupinfo():
    """ck3_chronicler-3v0s follow-up: belt-and-suspenders for the
    creationflags helper above. On Windows, returns a STARTUPINFO that
    explicitly hides any console window the subprocess (or its cmd.exe
    shim) tries to open. Without this, ``claude.cmd`` (the npm-installed
    shim that wraps node.exe) flashes a black console during gameplay
    every time a biography fires.
    """
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


async def _run_git(
    *,
    prose_repo: Path,
    argv: tuple[str, ...],
    label: str,
) -> tuple[int, bytes] | None:
    """Run a git subprocess with a hard timeout. Returns (returncode,
    stderr) on completion, None on timeout / spawn failure. Caller
    treats None as 'auto-commit unavailable' and bails."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *argv,
            cwd=str(prose_repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess_creationflags(),
            startupinfo=subprocess_startupinfo(),
        )
    except FileNotFoundError:
        log.warning("prose_io: git not on PATH; skipped %s", label)
        return None
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_GIT_SUBPROCESS_TIMEOUT_SECONDS
        )
    except TimeoutError:
        # ck3_chronicler-v07y: only kill if the process is still running.
        # On Windows asyncio, calling proc.kill() after the proc has
        # already exited raises through _check_proc (smoke 2026-05-10:
        # reproduced twice on 64448 v5 + v6). Wrap in suppress as
        # belt-and-braces — the documented contract at
        # git_commit_biography is that errors here MUST NOT propagate.
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        log.warning(
            "prose_io: git %s timed out after %.0fs in %s",
            label,
            _GIT_SUBPROCESS_TIMEOUT_SECONDS,
            prose_repo,
        )
        return None
    return proc.returncode, stderr


# ck3_chronicler-27ov.44 (audit M-N6): the scheduler runs up to two
# generations concurrently; when both finished near-simultaneously
# their add/diff/commit sequences interleaved in the same prose repo,
# and the index.lock loser failed — swallowed by the best-effort
# contract below as silent commit loss. The whole body is ~100ms, so
# serializing costs nothing. Locks are keyed per running event loop
# rather than one module-level asyncio.Lock because a Lock binds to
# the loop that first awaits it and raises if reused from another
# (pytest-asyncio gives every test its own loop).
_commit_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _commit_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _commit_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _commit_locks[loop] = lock
    return lock


async def git_commit_biography(
    *,
    prose_repo: Path,
    rel_brief: str,
    rel_bio: str,
    character_id: str,
    campaign_uuid: str,
    version: int,
    model: str,
) -> None:
    """ck3_chronicler-cj2f: stage and commit the briefing + biography
    pair in the prose repo so each generation lands as a discrete
    versioned artifact (matching the tbrm design — the prose repo IS
    the cross-machine record).

    Best-effort: any git failure logs a warning and returns. The
    biography is already on disk; failing to commit is recoverable
    by hand. Errors here MUST NOT propagate, since callers treat any
    exception from ``generate`` as a generation failure that wipes
    the response.

    ck3_chronicler-sezy (2026-05-08): each subprocess call is bounded
    by ``_GIT_SUBPROCESS_TIMEOUT_SECONDS`` so a stuck index lock or
    interactive pre-commit hook can't block the pipeline forever.

    ck3_chronicler-27ov.44: the add/diff/commit body runs under a
    per-loop lock so concurrent generations can't race the git index.
    """
    try:
        async with _commit_lock():
            # Stage the two artifacts. ``--`` prevents a path that happens
            # to look like a flag from being parsed as one.
            result = await _run_git(
                prose_repo=prose_repo,
                argv=("add", "--", rel_brief, rel_bio),
                label="add",
            )
            if result is None:
                return
            add_rc, add_err = result
            if add_rc != 0:
                log.warning(
                    "prose_io: git add failed in %s (rc=%d): %s",
                    prose_repo,
                    add_rc,
                    add_err.decode("utf-8", errors="replace")[-500:],
                )
                return

            # Sentinel: skip commit if `git diff --cached --quiet` reports
            # no staged changes (idempotent re-runs against unchanged
            # content). Exit code 0 = clean, 1 = changes staged.
            result = await _run_git(
                prose_repo=prose_repo,
                argv=(
                    "diff",
                    "--cached",
                    "--quiet",
                    "--",
                    rel_brief,
                    rel_bio,
                ),
                label="diff",
            )
            if result is None:
                return
            diff_rc, _ = result
            if diff_rc == 0:
                log.info(
                    "prose_io: nothing to commit for character_id=%s v%d",
                    character_id,
                    version,
                )
                return

            message = (
                f"biography: {character_id} v{version} (campaign={campaign_uuid}, model={model})"
            )
            commit_argv: tuple[str, ...] = (
                "commit",
                "-m",
                message,
                "--only",
                "--",
                rel_brief,
                rel_bio,
            )
            probe = await _run_git(
                prose_repo=prose_repo,
                argv=("config", "--get", "user.email"),
                label="config",
            )
            if probe is not None and probe[0] != 0:
                commit_argv = (
                    "-c",
                    f"user.name={_SCAFFOLD_COMMITTER_NAME}",
                    "-c",
                    f"user.email={_SCAFFOLD_COMMITTER_EMAIL}",
                ) + commit_argv
            result = await _run_git(
                prose_repo=prose_repo,
                argv=commit_argv,
                label="commit",
            )
            if result is None:
                return
            commit_rc, commit_err = result
            if commit_rc != 0:
                log.warning(
                    "prose_io: git commit failed in %s (rc=%d): %s",
                    prose_repo,
                    commit_rc,
                    commit_err.decode("utf-8", errors="replace")[-500:],
                )
    except Exception:
        log.exception(
            "prose_io: auto-commit raised unexpectedly; the briefing "
            "and biography are still on disk (character_id=%s v%d)",
            character_id,
            version,
        )
