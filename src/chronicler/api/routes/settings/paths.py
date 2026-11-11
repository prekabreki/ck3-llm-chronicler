"""Settings paths panel (ck3_chronicler-f9w.1, issue #51)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from chronicler.config import (
    ResolvedPath,
    resolve_archive_dir,
    resolve_ck3_install_dir,
    resolve_save_dir,
)
from chronicler.settings_store import load_settings, update_settings
from chronicler.sync import resolve_archive_git_root

router = APIRouter()


class PathInfo(BaseModel):
    """One resolved path plus provenance for the Settings paths panel.

    ck3_chronicler-f9w.1. ``resolved`` is the absolute path the resolver
    currently returns — empty string only when nothing resolves (CK3
    install dir, no override, Steam probe miss). ``source`` reports where
    the resolution came from. ``exists`` is whether the resolved path is
    a directory on disk *now*. ``override`` carries the user-set value
    from ``settings.json`` (null when unset).
    """

    resolved: str
    source: str  # "override" | "env" | "default" | "probe"
    exists: bool
    override: str | None


class PathsSettingsResponse(BaseModel):
    """All path overrides exposed in the Settings paths panel.

    Issue #51 added ``archive_dir`` — sealed-campaign snapshots, made
    configurable by #24 but reachable only by hand-editing settings.json
    until now. ``archive_git_root`` is the checkout containing it, or
    null: being inside a repo silently changes behaviour (chronicler
    commits and pushes the snapshots there), so the panel says which it
    is rather than leaving the user to find out at seal time.
    """

    save_dir: PathInfo
    ck3_install_dir: PathInfo
    archive_dir: PathInfo
    archive_git_root: str | None = None


class PathsSettingsUpdate(BaseModel):
    """PUT body for /api/settings/paths.

    Each field is ``Optional[str]``; ``null`` clears the override so the
    resolver falls back to env/default. Empty strings are treated the
    same as null. Any other string sets the override verbatim.
    """

    save_dir: str | None = None
    ck3_install_dir: str | None = None
    archive_dir: str | None = None


def _to_path_info(resolved: ResolvedPath, override: str | None) -> PathInfo:
    return PathInfo(
        resolved=str(resolved.value) if resolved.value is not None else "",
        source=resolved.source,
        exists=resolved.exists,
        override=override,
    )


@router.get("/paths", response_model=PathsSettingsResponse)
def get_paths_settings() -> PathsSettingsResponse:
    """Return the resolved save / CK3 install / archive dirs with provenance.

    ck3_chronicler-f9w.1. The Settings paths panel reads this on mount
    and re-fetches after any PUT to reflect the new resolution.
    """
    settings = load_settings()
    save_override = settings.get("save_dir")
    if not isinstance(save_override, str) or not save_override:
        save_override = None
    install_override = settings.get("ck3_install_dir")
    if not isinstance(install_override, str) or not install_override:
        install_override = None
    archive_override = settings.get("archive_dir")
    if not isinstance(archive_override, str) or not archive_override:
        archive_override = None

    archive_resolved = resolve_archive_dir()
    git_root = (
        resolve_archive_git_root(archive_resolved.value)
        if archive_resolved.value is not None
        else None
    )

    return PathsSettingsResponse(
        save_dir=_to_path_info(resolve_save_dir(), save_override),
        ck3_install_dir=_to_path_info(resolve_ck3_install_dir(), install_override),
        archive_dir=_to_path_info(archive_resolved, archive_override),
        archive_git_root=str(git_root) if git_root is not None else None,
    )


@router.put("/paths", response_model=PathsSettingsResponse)
def put_paths_settings(body: PathsSettingsUpdate) -> PathsSettingsResponse:
    """Persist save / CK3 install / archive dir overrides to the settings file.

    Each field is optional in the body; missing fields are left
    unchanged, ``null`` (or empty string) clears the override, any other
    string sets it. Returns the post-update resolution so the UI doesn't
    have to issue a follow-up GET.
    """
    updates: dict[str, str | None] = {}
    payload = body.model_dump(exclude_unset=True)
    if "save_dir" in payload:
        v = payload["save_dir"]
        updates["save_dir"] = v if isinstance(v, str) and v else None
    if "ck3_install_dir" in payload:
        v = payload["ck3_install_dir"]
        updates["ck3_install_dir"] = v if isinstance(v, str) and v else None
    if "archive_dir" in payload:
        # Repointing this moves nothing: #24 chose warn-never-move, because
        # those snapshots can be the only copy of a finished campaign. The
        # old directory keeps its files and the Sealed shelf re-homes on the
        # next startup (the stale-projection prune in sync/bootstrap.py).
        v = payload["archive_dir"]
        updates["archive_dir"] = v if isinstance(v, str) and v else None

    if updates:
        update_settings(updates)

    return get_paths_settings()
