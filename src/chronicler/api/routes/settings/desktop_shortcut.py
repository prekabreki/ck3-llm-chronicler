"""Desktop shortcut creation (issue #10)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class DesktopShortcutResponse(BaseModel):
    """Issue #10: result of ``POST /api/settings/desktop-shortcut``.

    ``path`` is the absolute path of the created shortcut (the ``.desktop``
    file on Linux, the ``.lnk`` on Windows); ``platform`` is ``"linux"`` or
    ``"windows"``. On failure the route returns an HTTP error instead
    (501 unsupported platform, 409 no CK3 install for the icon)."""

    path: str
    platform: str


@router.post("/desktop-shortcut", response_model=DesktopShortcutResponse)
async def post_desktop_shortcut() -> DesktopShortcutResponse:
    """Issue #10: create a desktop shortcut that runs ``chronicler launch``.

    501 on unsupported platforms (macOS), 409 when no CK3 install resolves
    (there is no ``exe_icon.bmp`` to draw the icon) — in that case nothing
    is written; see :mod:`chronicler.desktop_shortcut` for the chosen
    behaviour. The work (Pillow conversion + subprocesses) runs off the
    event loop like debug-log rotate.
    """
    from fastapi import HTTPException

    from chronicler.desktop_shortcut import (
        NoCk3InstallError,
        UnsupportedPlatformError,
        create_desktop_shortcut,
    )

    try:
        result = await asyncio.to_thread(create_desktop_shortcut)
    except UnsupportedPlatformError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except NoCk3InstallError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return DesktopShortcutResponse(path=result.path, platform=result.platform)
