"""Resolved model and narrative-backend settings (issues #45, ck3_chronicler-5d9o)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chronicler.settings_store import load_settings, update_settings

router = APIRouter()


class ResolvedModelsResponse(BaseModel):
    """ck3_chronicler-5d9o: per-kind Claude Code model snapshot for the
    Settings 'Models' panel. Read-only — the user tunes via env vars
    (CHRONICLER_CLAUDE_CODE_{BIO,CLOSING}_MODEL), this surface just
    shows the resolved values so they can sanity-check what's about to run.

    ``global_override`` carries the value of CHRONICLER_CLAUDE_CODE_MODEL
    when set, since that legacy env wins over every per-kind tag and the
    UI should call out when it's active.
    """

    biography: str
    closing: str
    global_override: str | None


class ModelsUpdateRequest(BaseModel):
    """Issue #45: writable counterpart to ``GET /models``.

    Every field is optional and only the fields the client actually sends
    are applied (``exclude_unset``), so a UI that saves one row cannot
    blank the others. Send an **empty string** to clear a setting and fall
    back to env-then-default; omit the field to leave it untouched. That
    distinction is the whole contract — ``None`` would be ambiguous
    between the two.
    """

    global_override: str | None = None
    biography: str | None = None
    closing: str | None = None


class BackendPresetInfo(BaseModel):
    """Issue #23: one openai-compatible preset, as the picker needs it.

    ``valid_presets`` (a bare list of ids) is enough to validate a choice
    but not to *offer* one: the UI prefills the base-URL field from the
    preset the user picks, and greys the key field for the two local
    presets that need none. Serving the preset table rather than
    duplicating those constants in TypeScript is what keeps a sixth
    preset a one-file change.
    """

    id: str
    base_url: str
    requires_key: bool


class BackendKeyState(BaseModel):
    """Whether a key is stored — never the key itself.

    ``doctor`` output and API responses both get pasted into bug reports,
    so the key never leaves the process once written.
    """

    present: bool
    source: str | None = None


class NarrativeBackendResponse(BaseModel):
    """Issue #45: the configured backend and what it is pointed at."""

    backend: str
    source: str
    valid_backends: list[str]
    valid_presets: list[str]
    presets: list[BackendPresetInfo]
    openai_preset: str | None
    openai_base_url: str | None
    openai_model: str | None
    openai_key: BackendKeyState
    anthropic_key: BackendKeyState
    # Whether the current combination would actually construct. The UI
    # shows `error` verbatim; it is the same string `chronicler doctor`
    # prints, by construction (backend_config owns both).
    usable: bool
    error: str | None


class NarrativeBackendUpdateRequest(BaseModel):
    """Partial update of backend selection + its target.

    Same omit-vs-empty-string contract as :class:`ModelsUpdateRequest`:
    an omitted field is untouched, an empty string clears the setting.
    This is what keeps a "save the base URL" request from wiping a stored
    API key.
    """

    backend: str | None = None
    openai_preset: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None


@router.get("/models", response_model=ResolvedModelsResponse)
def get_resolved_models(request: Request) -> ResolvedModelsResponse:
    """ck3_chronicler-5d9o: per-kind resolved Claude Code model snapshot.

    Read-only — exists to power the Settings 'Models' panel so the user
    can see what model each kind routes to right now. Defers to the
    configured provider's :meth:`resolved_models` when one is wired, so
    a constructor-injected ``model=`` kwarg (e.g. in tests or pinned
    deployments) reflects correctly. Falls back to the module-level
    resolver when no provider is configured.
    """
    from chronicler.narrative.model_resolution import resolved_models

    # Issue #46: the ABC now guarantees resolved_models(), so this is a
    # direct call. It used to be getattr + callable() — a duck-typed probe
    # that silently fell back to the module resolver for any provider that
    # happened not to implement it.
    provider = getattr(request.app.state, "narrative_provider", None)
    snap = provider.resolved_models() if provider is not None else resolved_models()
    return ResolvedModelsResponse(
        biography=snap["biography"],
        closing=snap["closing"],
        global_override=snap["global_override"],
    )


# --- issue #45: writable backend + model settings ---
#
# The GET routes above are read-only snapshots of resolved state; these two
# write the durable settings the resolvers read first. Both take partial
# payloads and apply only the fields the client sent, so a card that saves
# one row cannot blank its neighbours (and, specifically, cannot wipe a
# stored API key).


def _apply_settings_updates(sent: dict[str, object | None], key_map: dict[str, str]) -> None:
    """Persist the sent fields, translating request names to setting keys.

    An empty (or whitespace-only) string clears the setting, which
    ``update_settings`` expresses as ``None`` — the consumer then falls
    back to env-then-default. Fields absent from *sent* are never touched.
    """
    updates: dict[str, object | None] = {}
    for field, setting_key in key_map.items():
        if field not in sent:
            continue
        value = sent[field]
        text = value.strip() if isinstance(value, str) else value
        updates[setting_key] = text or None
    if updates:
        update_settings(updates)


def _narrative_backend_state() -> NarrativeBackendResponse:
    """Resolve the current backend configuration for the GET response."""
    import os as _os

    from chronicler.narrative.backend_config import (
        ANTHROPIC_API_KEY_SETTING,
        CHRONICLER_NARRATIVE_BACKEND_ENV,
        NARRATIVE_BACKEND_SETTING,
        OPENAI_API_KEY_SETTING,
        OPENAI_BASE_URL_SETTING,
        OPENAI_MODEL_SETTING,
        OPENAI_PRESET_SETTING,
        resolve_anthropic_api_key,
        resolve_backend,
        resolve_openai_api_key,
        resolve_openai_config,
    )
    from chronicler.narrative.factory import known_backends
    from chronicler.narrative.openai_compatible import PRESETS

    stored = load_settings()
    if stored.get(NARRATIVE_BACKEND_SETTING):
        source = "settings"
    elif (_os.environ.get(CHRONICLER_NARRATIVE_BACKEND_ENV) or "").strip():
        source = "env"
    else:
        source = "default"

    backend = resolve_backend()
    # Only report the openai-compatible validity when that backend is the
    # one selected: a red "no endpoint configured" on a claude-code install
    # would be noise, not a finding.
    if backend == "openai-compatible":
        config, error = resolve_openai_config()
        usable, error_text = config is not None, error
    else:
        usable, error_text = backend in known_backends(), None
        if not usable:
            error_text = (
                f"unknown narrative backend {backend!r}; "
                f"valid values: {', '.join(known_backends())}"
            )

    def _key_state(stored_key: str, resolver: object) -> BackendKeyState:
        resolved = resolver() if callable(resolver) else None
        if not resolved:
            return BackendKeyState(present=False, source=None)
        return BackendKeyState(
            present=True,
            source="settings" if stored.get(stored_key) else "env",
        )

    return NarrativeBackendResponse(
        backend=backend,
        source=source,
        valid_backends=list(known_backends()),
        valid_presets=sorted(PRESETS),
        presets=[
            BackendPresetInfo(
                id=preset.id,
                base_url=preset.base_url,
                requires_key=preset.requires_key,
            )
            for _, preset in sorted(PRESETS.items())
        ],
        openai_preset=stored.get(OPENAI_PRESET_SETTING) or None,
        openai_base_url=stored.get(OPENAI_BASE_URL_SETTING) or None,
        openai_model=stored.get(OPENAI_MODEL_SETTING) or None,
        openai_key=_key_state(OPENAI_API_KEY_SETTING, resolve_openai_api_key),
        anthropic_key=_key_state(ANTHROPIC_API_KEY_SETTING, resolve_anthropic_api_key),
        usable=usable,
        error=error_text,
    )


@router.get("/narrative-backend", response_model=NarrativeBackendResponse)
def get_narrative_backend() -> NarrativeBackendResponse:
    """The configured backend, its provenance, and whether it would work.

    Never returns a stored API key — only ``present`` plus which tier it
    came from.
    """
    return _narrative_backend_state()


@router.put("/narrative-backend", response_model=NarrativeBackendResponse)
def put_narrative_backend(payload: NarrativeBackendUpdateRequest) -> NarrativeBackendResponse:
    """Persist backend selection and its target, then report the new state.

    Rejects an unknown backend id with 400 rather than storing it: a typo
    persisted here would be read on every subsequent start, and the
    factory's fail-loud would then greet the user at the first death
    instead of at the moment they made the mistake.
    """
    from fastapi import HTTPException

    from chronicler.narrative.backend_config import (
        ANTHROPIC_API_KEY_SETTING,
        NARRATIVE_BACKEND_SETTING,
        OPENAI_API_KEY_SETTING,
        OPENAI_BASE_URL_SETTING,
        OPENAI_MODEL_SETTING,
        OPENAI_PRESET_SETTING,
    )
    from chronicler.narrative.factory import known_backends
    from chronicler.narrative.openai_compatible import PRESETS

    sent = payload.model_dump(exclude_unset=True)

    requested = sent.get("backend")
    if isinstance(requested, str) and requested.strip():
        if requested.strip().lower() not in known_backends():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown narrative backend {requested!r}; "
                    f"valid values: {', '.join(known_backends())}"
                ),
            )
        sent["backend"] = requested.strip().lower()

    preset = sent.get("openai_preset")
    if isinstance(preset, str) and preset.strip():
        if preset.strip().lower() not in PRESETS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown preset {preset!r}; valid presets: "
                    f"{', '.join(sorted(PRESETS))} (or clear the preset and "
                    "set a base URL for any other OpenAI-compatible endpoint)"
                ),
            )
        sent["openai_preset"] = preset.strip().lower()

    _apply_settings_updates(
        sent,
        {
            "backend": NARRATIVE_BACKEND_SETTING,
            "openai_preset": OPENAI_PRESET_SETTING,
            "openai_base_url": OPENAI_BASE_URL_SETTING,
            "openai_model": OPENAI_MODEL_SETTING,
            "openai_api_key": OPENAI_API_KEY_SETTING,
            "anthropic_api_key": ANTHROPIC_API_KEY_SETTING,
        },
    )
    return _narrative_backend_state()


@router.put("/models", response_model=ResolvedModelsResponse)
def put_resolved_models(request: Request, payload: ModelsUpdateRequest) -> ResolvedModelsResponse:
    """Persist per-kind model overrides, then return the resolved snapshot.

    The response comes from the same resolver ``GET /models`` uses, so the
    UI sees what will actually run — including the case where a global
    override still shadows the per-kind value just saved.
    """
    from chronicler.narrative.model_resolution import (
        NARRATIVE_BIO_MODEL_SETTING,
        NARRATIVE_CLOSING_MODEL_SETTING,
        NARRATIVE_MODEL_SETTING,
    )

    _apply_settings_updates(
        payload.model_dump(exclude_unset=True),
        {
            "global_override": NARRATIVE_MODEL_SETTING,
            "biography": NARRATIVE_BIO_MODEL_SETTING,
            "closing": NARRATIVE_CLOSING_MODEL_SETTING,
        },
    )
    return get_resolved_models(request)
