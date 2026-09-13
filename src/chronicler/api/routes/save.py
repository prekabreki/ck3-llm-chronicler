"""Save-directory listing endpoint (ck3_chronicler-mb6q).

Powers the ImportModal's "Recent saves in <save_dir>" picker so the
user doesn't have to hand-type a Windows path. The endpoint enumerates
.ck3 files in the resolved save_dir, sorted newest-first, and returns
a small metadata blob per row (filename + abs path + size + mtime).

Deliberately cheap — no save parsing here. Click-to-select on the
frontend just fills the existing path input + submits the existing
import flow, so the import endpoint still does the actual work.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chronicler.config import resolve_save_dir

router = APIRouter(prefix="/api/save", tags=["save"])

log = logging.getLogger(__name__)

# Cap the listing to prevent runaway responses on a save_dir with
# hundreds of .ck3 files (some users keep every manual save). 50 is a
# generous ceiling; the FE asks for 10 by default.
_MAX_LIMIT = 50


class SaveFileInfo(BaseModel):
    """One row in the save-picker list."""

    filename: str
    abs_path: str
    size_bytes: int
    mtime_iso: str


class SaveListResponse(BaseModel):
    save_dir: str
    save_dir_exists: bool
    save_dir_source: str  # 'override' | 'env' | 'default'
    saves: list[SaveFileInfo]


@router.get("/list", response_model=SaveListResponse)
def list_saves(limit: int = 10) -> SaveListResponse:
    """List .ck3 files in the configured save_dir, mtime-desc.

    Returns 200 with an empty list when save_dir resolves but doesn't
    exist on disk (typical fresh install) — the FE renders an
    informative empty state in that case rather than an error.
    """
    if limit < 1:
        limit = 1
    if limit > _MAX_LIMIT:
        limit = _MAX_LIMIT

    resolved = resolve_save_dir()
    save_dir = resolved.value
    if save_dir is None:
        # resolve_save_dir always returns a path today, but type allows
        # None — surface as a 500 with a clear message rather than a
        # confusing AttributeError.
        raise HTTPException(
            status_code=500,
            detail="save_dir could not be resolved",
        )

    if not resolved.exists:
        return SaveListResponse(
            save_dir=str(save_dir),
            save_dir_exists=False,
            save_dir_source=resolved.source,
            saves=[],
        )

    rows: list[tuple[float, SaveFileInfo]] = []
    try:
        for path in save_dir.glob("*.ck3"):
            if not path.is_file():
                continue
            stat = path.stat()
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
            rows.append(
                (
                    stat.st_mtime,
                    SaveFileInfo(
                        filename=path.name,
                        abs_path=str(path),
                        size_bytes=stat.st_size,
                        mtime_iso=mtime_iso,
                    ),
                )
            )
    except OSError as exc:
        # save_dir.exists was True but a race or permission flip means
        # we can't read it — surface as a 500 so the UI fallback shows
        # the typed-path input.
        log.warning("save listing failed for %s: %s", save_dir, exc)
        raise HTTPException(
            status_code=500,
            detail=f"could not read save_dir: {save_dir}",
        ) from exc

    rows.sort(key=lambda pair: pair[0], reverse=True)
    saves = [info for _mtime, info in rows[:limit]]

    return SaveListResponse(
        save_dir=str(save_dir),
        save_dir_exists=True,
        save_dir_source=resolved.source,
        saves=saves,
    )
