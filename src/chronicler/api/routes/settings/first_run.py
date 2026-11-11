"""First-run wizard detection (ck3_chronicler-kze6 / f9w.3)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from chronicler.api.routes.heraldry import compute_heraldry_status_safe
from chronicler.api.routes.settings.prose_repo import _build_prose_repo_status
from chronicler.db.registry import list_campaigns
from chronicler.settings_store import load_settings, update_settings

router = APIRouter()


class FirstRunStatusResponse(BaseModel):
    """First-run detection for the FirstRunWizard (kze6 / f9w.3).

    ``needs_wizard`` is True when the wizard should auto-open: no
    campaigns adopted, no settings.json overrides, no heraldry assets,
    AND the user hasn't dismissed the wizard yet. Each underlying
    signal is exposed so the wizard can label which steps are already
    satisfied.

    Issue #23 added ``prose_repo_ready`` for the wizard's chronicle step
    (the scaffold + backend choice). It is deliberately NOT a term in
    ``needs_wizard``: the wizard opens on the *absence* of setup, and
    every other signal is already false on the install where the prose
    dir is missing, so adding it would only widen the auto-open
    condition for users who have finished setting up but keep their
    chronicle somewhere the resolver can't see.
    """

    needs_wizard: bool
    library_empty: bool
    save_dir_configured: bool
    ck3_install_dir_configured: bool
    heraldry_extracted: bool
    prose_repo_ready: bool
    wizard_dismissed_at: str | None


class FirstRunDismissResponse(BaseModel):
    """audit F-39 / ck3_chronicler-tjql: response model for
    POST /api/settings/first-run/dismiss. The endpoint returned a
    bare dict[str, str] with no Pydantic envelope, so OpenAPI lied
    about the shape and a typo in the FE consumer's contract would
    have gone uncaught."""

    wizard_dismissed_at: str


@router.get("/first-run", response_model=FirstRunStatusResponse)
def get_first_run_status() -> FirstRunStatusResponse:
    """Detect whether the FirstRunWizard should auto-open.

    Heuristic: the wizard opens once per install when none of the
    onboarding steps have been completed and the user hasn't dismissed
    it before. After dismissal (or first successful adopt), the
    ``wizard_dismissed_at`` flag suppresses re-prompting.
    """
    settings = load_settings()
    save_override = isinstance(settings.get("save_dir"), str) and bool(settings["save_dir"])
    install_override = isinstance(settings.get("ck3_install_dir"), str) and bool(
        settings["ck3_install_dir"]
    )
    wizard_dismissed_at = settings.get("wizard_dismissed_at")
    if not isinstance(wizard_dismissed_at, str):
        wizard_dismissed_at = None

    library_empty = len(list_campaigns(include_archived=True)) == 0
    heraldry_status = compute_heraldry_status_safe()
    # A chronicle directory counts as ready only with its role-reshape
    # file: an empty dir on the resolved path would run claude --print as
    # a generic assistant, which is the failure this step exists to stop.
    prose = _build_prose_repo_status()

    needs_wizard = (
        wizard_dismissed_at is None
        and library_empty
        and not heraldry_status.extracted
        and not save_override
        and not install_override
    )

    return FirstRunStatusResponse(
        needs_wizard=needs_wizard,
        library_empty=library_empty,
        save_dir_configured=save_override,
        ck3_install_dir_configured=install_override,
        heraldry_extracted=heraldry_status.extracted,
        prose_repo_ready=prose.exists and prose.claude_md_present,
        wizard_dismissed_at=wizard_dismissed_at,
    )


@router.post("/first-run/dismiss", response_model=FirstRunDismissResponse)
def dismiss_first_run_wizard() -> FirstRunDismissResponse:
    """Persist a wizard-dismissed-at timestamp so the wizard stops
    auto-opening on subsequent loads. audit F-39 / ck3_chronicler-tjql:
    response_model added so OpenAPI documents the actual shape."""
    now = datetime.now(UTC).isoformat()
    update_settings({"wizard_dismissed_at": now})
    return FirstRunDismissResponse(wizard_dismissed_at=now)
