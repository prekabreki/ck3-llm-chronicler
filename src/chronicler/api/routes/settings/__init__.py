"""Settings endpoints for the v0.7 portal, split by concern.

This package owns:

- ``/api/settings/llm-pause`` — global LLM pause toggle (ck3_chronicler-gx7b).
- ``/api/settings/models`` + ``/api/settings/narrative-backend`` — resolved
  and writable model/backend configuration (issues #23, #45).
- ``/api/settings/provider-status`` — live state of the configured
  NarrativeProvider (ck3_chronicler-ak6).
- ``/api/settings/debug-log-*`` — CK3 debug.log size + rotation
  (ck3_chronicler-z6jm).
- ``/api/settings/paths`` — resolved save / install / archive dirs
  (ck3_chronicler-f9w.1).
- ``/api/settings/prose-repo*`` — chronicle repo status, override and
  scaffold (ck3_chronicler-tbrm.4, issue #23).
- ``/api/settings/first-run*`` — first-run wizard detection
  (ck3_chronicler-kze6 / f9w.3).
- ``/api/settings/desktop-shortcut`` — desktop shortcut creation (issue #10).

The package exposes ``router`` (also aliased as ``settings_router``), which
the app includes as the process-wide settings router.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    debug_log,
    desktop_shortcut,
    first_run,
    llm_pause,
    models_backend,
    paths,
    prose_repo,
    provider_status,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
router.include_router(llm_pause.router)
router.include_router(models_backend.router)
router.include_router(provider_status.router)
router.include_router(debug_log.router)
router.include_router(paths.router)
router.include_router(prose_repo.router)
router.include_router(first_run.router)
router.include_router(desktop_shortcut.router)

# Backwards-compatible name for the router this module used to export.
settings_router = router
