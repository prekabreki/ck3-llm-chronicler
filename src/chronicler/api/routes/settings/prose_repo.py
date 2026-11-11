"""Prose repo settings and scaffold (ck3_chronicler-tbrm.4, issue #23)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from chronicler.config import resolve_prose_repo_path
from chronicler.settings_store import load_settings, update_settings

router = APIRouter()


class ProseRepoStatusResponse(BaseModel):
    """ck3_chronicler-tbrm.4: status of the sibling ck3_chronicler_prose
    repo that the ClaudeCodeProvider invokes ``claude --print`` against.

    Three checks the Settings UI surfaces as red/green pips:
    - ``exists``: directory is on disk.
    - ``git_initialized``: ``.git/`` subdirectory exists, so commits land.
    - ``claude_md_present``: ``CLAUDE.md`` exists at the root, which is
      the role-reshape that turns Claude Code into a chronicler.

    All three must be true for biography generation to behave correctly.
    """

    path: str
    source: str  # "override" | "env" | "default"
    override: str | None
    exists: bool
    git_initialized: bool
    claude_md_present: bool


class ProseRepoUpdate(BaseModel):
    """PUT body for setting the prose repo override. ``None`` (or empty
    string) clears the override; any other string sets it. Mirrors the
    ergonomics of PathsSettingsUpdate."""

    path: str | None = None


class ProseRepoInitRequest(BaseModel):
    """POST body for ``/prose-repo/init``. ``path`` omitted (or empty)
    scaffolds the default target — the same one ``chronicler init-prose``
    picks with no argument."""

    path: str | None = None


class ProseRepoInitResponse(BaseModel):
    """Issue #23: outcome of the Settings 'Initialize' button.

    Carries the post-scaffold ``status`` so the card can repaint its
    readiness pips from one round trip, plus the scaffold's own report:
    ``created`` false with ``already_initialised`` true is the idempotent
    re-run (the user's edited craft rules were left alone), and ``notes``
    are the same human-facing lines the CLI prints — including the ones
    that explain a *partial* success, like git being unavailable.
    """

    status: ProseRepoStatusResponse
    created: bool
    already_initialised: bool
    git_initialised: bool
    notes: list[str]


def _build_prose_repo_status() -> ProseRepoStatusResponse:
    """Resolve the prose repo path + run the three on-disk readiness
    checks the Settings card surfaces."""
    settings = load_settings()
    override = settings.get("prose_repo_path")
    if not isinstance(override, str) or not override:
        override = None

    resolved = resolve_prose_repo_path()
    path_value = resolved.value
    exists = path_value is not None and path_value.is_dir()
    git_initialized = exists and (path_value / ".git").is_dir()
    claude_md_present = exists and (path_value / "CLAUDE.md").is_file()
    return ProseRepoStatusResponse(
        path=str(path_value) if path_value is not None else "",
        source=resolved.source,
        override=override,
        exists=exists,
        git_initialized=bool(git_initialized),
        claude_md_present=bool(claude_md_present),
    )


@router.get("/prose-repo", response_model=ProseRepoStatusResponse)
def get_prose_repo_status() -> ProseRepoStatusResponse:
    """Status of the sibling ck3_chronicler_prose repo.

    The Settings ProseRepoCard reads this on mount + after every PUT.
    All three pips must be green for biography generation to work; the
    Card maps each to a red/green badge so the user can spot a missing
    CLAUDE.md or unset override at a glance.
    """
    return _build_prose_repo_status()


@router.put("/prose-repo", response_model=ProseRepoStatusResponse)
def put_prose_repo_settings(body: ProseRepoUpdate) -> ProseRepoStatusResponse:
    """Persist the prose repo path override and return the new status.

    ``path=null`` (or empty) clears the override so resolution falls back
    to env / default precedence. Any other string is taken verbatim — the
    backend doesn't normalise; the on-disk readiness pips will flag a
    typo via ``exists=false``.
    """
    payload = body.model_dump(exclude_unset=True)
    if "path" in payload:
        v = payload["path"]
        update_settings({"prose_repo_path": v if isinstance(v, str) and v else None})
    return _build_prose_repo_status()


@router.post("/prose-repo/init", response_model=ProseRepoInitResponse)
async def post_prose_repo_init(body: ProseRepoInitRequest) -> ProseRepoInitResponse:
    """Issue #23: scaffold a chronicle directory from the shipped template.

    The GUI counterpart of ``chronicler init-prose``, calling the same
    :func:`scaffold_prose_dir` rather than reimplementing the copy — the
    scaffold is the definition of a valid prose directory and there is
    one of it (see the note at the top of ``prose_scaffold``).

    With no ``path``, scaffolds wherever the prose-repo resolver
    currently points, so the button does what the path shown right above
    it says it will. The scaffold records the path in settings itself, so
    a default-target init also makes the choice durable.

    A refused target (a file, or a non-empty directory that isn't already
    a scaffold) is the user's mistake to fix and comes back as 400 with
    the scaffold's own message; a missing bundled template is ours and
    comes back as 500. Both carry text — the card renders ``detail``, so
    a failure never leaves a spinner with nothing to read.
    """
    from fastapi import HTTPException

    from chronicler.narrative.prose_scaffold import scaffold_prose_dir

    requested = (body.path or "").strip()
    if requested:
        target = Path(requested)
    else:
        current = _build_prose_repo_status().path
        if current:
            target = Path(current)
        else:
            from chronicler.narrative.prose_scaffold import default_prose_path

            target = default_prose_path()

    # git init + commit shell out; off the event loop like debug-log rotate.
    try:
        result = await asyncio.to_thread(scaffold_prose_dir, target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ProseRepoInitResponse(
        status=_build_prose_repo_status(),
        created=result.created,
        already_initialised=result.already_initialised,
        git_initialised=result.git_initialised,
        notes=list(result.notes),
    )
