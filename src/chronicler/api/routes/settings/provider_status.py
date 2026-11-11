"""Live NarrativeProvider status (ck3_chronicler-ak6)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ProviderStatusResponse(BaseModel):
    """Live state of the configured NarrativeProvider (ck3_chronicler-ak6).

    ck3_chronicler-tbrm.3 dropped the VRAM probe + cpu_only fields when
    the in-process providers were ripped out. ``mode`` is now
    ``claude-code``, ``unconfigured``, or ``other`` — derived from the
    provider's name string. ``model`` is the bare model identifier
    without the provider prefix.

    The ``recent_biographies`` and ``avg_biography_ms`` fields cover the
    rolling window (default last 50 biography generations process-wide).
    """

    mode: str
    model: str | None
    recent_biographies: int
    avg_biography_ms: int | None


def _provider_mode(provider_name: str) -> str:
    """Derive the high-level mode from the provider's name string.

    Issue #45: delegates to the registry-derived
    :func:`chronicler.narrative.backend_config.backend_for_provider_name`.
    The two hardcoded ``startswith`` branches this replaced reported
    ``"other"`` for every openai-compatible transport, because that
    transport's tag prefix is the *preset* id (``openai:``, ``ollama:``…),
    not the backend id.

    ``other`` still covers test fakes and anything unrecognised.
    """
    from chronicler.narrative.backend_config import backend_for_provider_name

    return backend_for_provider_name(provider_name)


def _provider_model(provider_name: str) -> str | None:
    """Pull the model id out of ``"<provider>:<model>"`` (or None)."""
    if ":" not in provider_name:
        return None
    _, _, model = provider_name.partition(":")
    return model or None


@router.get("/provider-status", response_model=ProviderStatusResponse)
def get_provider_status(request: Request) -> ProviderStatusResponse:
    """Live snapshot of the active NarrativeProvider for the settings UI.

    Returns ``mode='unconfigured'`` rather than 503 when no provider is
    set — the UI then prompts the user to configure one.
    """
    provider = getattr(request.app.state, "narrative_provider", None)
    # ck3_chronicler-27ov.41 (audit M-N1): latency/count come from the live
    # NarrativeQueueState (which actually records completions), not the dead
    # GenerationStats ring that had no writer and always reported 0/None.
    queue = getattr(request.app.state, "narrative_queue", None)
    recent_biographies = 0
    avg_biography_ms: int | None = None
    if queue is not None:
        snap = queue.snapshot()
        recent_biographies = snap.completed_count
        avg_biography_ms = snap.avg_duration_ms

    if provider is None:
        return ProviderStatusResponse(
            mode="unconfigured",
            model=None,
            recent_biographies=recent_biographies,
            avg_biography_ms=avg_biography_ms,
        )

    name = getattr(provider, "name", "")
    return ProviderStatusResponse(
        mode=_provider_mode(name),
        model=_provider_model(name),
        recent_biographies=recent_biographies,
        avg_biography_ms=avg_biography_ms,
    )
