"""Settings + cost endpoints for the v0.7 portal.

This module owns:

- ``GET /api/campaigns/{name}/cost-summary`` — aggregates token spend
  from biographies + memories into ``this_campaign`` + ``this_month``
  buckets, attributing dollar amounts via the per-provider price table
  in :mod:`chronicler.config` (ck3_chronicler-j7z).
- ``GET /api/settings/provider-status`` — live state of the configured
  NarrativeProvider: mode, model, VRAM, rolling biography/memory
  latency stats (ck3_chronicler-ak6).

The provider-status route is process-wide (no campaign in path) so it
gets its own router.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chronicler.api.routes.heraldry import compute_heraldry_status_safe
from chronicler.config import (
    ResolvedPath,
    resolve_archive_dir,
    resolve_ck3_install_dir,
    resolve_prose_repo_path,
    resolve_save_dir,
)
from chronicler.db.registry import list_campaigns
from chronicler.settings_store import load_settings, update_settings
from chronicler.sync import resolve_archive_git_root
from chronicler.tailer.log_rotate import (
    archive_and_truncate,
    reset_tail_offsets,
    stat_debug_log,
)

log = logging.getLogger(__name__)

# Process-wide settings router (no campaign in path).
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
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


class LLMPauseDrainReport(BaseModel):
    """ck3_chronicler-gx7b: counts the unpause handler scheduled across
    all open campaigns when transitioning paused → unpaused."""

    biographies_scheduled: int


class LLMPauseResponse(BaseModel):
    """ck3_chronicler-gx7b: global LLM pause state. While ``paused``,
    save-tail keeps ingesting but no biographies auto-fire from event
    triggers. ``paused_at`` is the ISO timestamp the
    user flipped the toggle ON (None when not paused) so the FE can render
    'paused for 2h 14m'. ``last_drain`` carries the counts from the most
    recent unpause-drain — None if there hasn't been one this session.
    """

    paused: bool
    paused_at: str | None
    last_drain: LLMPauseDrainReport | None = None


class LLMPauseUpdate(BaseModel):
    """PUT body for toggling pause. ``paused=true`` sets the flag (and
    writes paused_at = now). ``paused=false`` clears both and triggers
    the drain across every open campaign before returning."""

    paused: bool


class DebugLogStatusResponse(BaseModel):
    """ck3_chronicler-z6jm slice 1: snapshot of the CK3 debug.log size +
    rotation threshold. The Settings page polls this once a minute and
    raises a UI alert when ``exceeded`` is true. ``path`` is exposed so
    the alert can name the actual file the user (or chronicler) would
    rotate."""

    exists: bool
    size_bytes: int
    threshold_bytes: int
    exceeded: bool
    path: str


class DebugLogRotateResponse(BaseModel):
    """ck3_chronicler-z6jm slice 1: result of a manual rotate request.

    On success: ``rotated=True``, ``archive_path`` names the gzip
    archive, ``offsets_reset`` is the count of campaign tail_offset
    rows reset to 0.

    On the no-op path (log missing or empty): ``rotated=False``,
    ``message`` explains. Status code is still 200 — the user clicked
    a button and got a defined outcome, not an error.

    The endpoint deliberately does NOT distinguish "CK3 is open" from
    other rotation failures here; the helper logs that to stderr but
    still returns success-with-archive-but-original-not-truncated. The
    UI's responsibility is to remind the user 'close CK3 first'."""

    rotated: bool
    archive_path: str | None
    offsets_reset: int
    message: str


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


def _llm_pause_state() -> tuple[bool, str | None]:
    """Read the current pause state from settings.json."""
    settings = load_settings()
    paused = bool(settings.get("llm_paused", False))
    paused_at = settings.get("llm_paused_at") if paused else None
    return paused, paused_at if isinstance(paused_at, str) else None


@settings_router.get("/llm-pause", response_model=LLMPauseResponse)
def get_llm_pause(request: Request) -> LLMPauseResponse:
    """ck3_chronicler-gx7b: current global LLM pause state.

    Returns ``paused=True`` when the user has flipped the Settings toggle
    ON; in that state, save-tail keeps ingesting but no biographies
    auto-fire. ``paused_at`` is the ISO timestamp of the toggle-ON moment
    so the FE can render "paused for 2h 14m".
    """
    paused, paused_at = _llm_pause_state()
    return LLMPauseResponse(
        paused=paused,
        paused_at=paused_at,
        last_drain=request.app.state.last_drain_report,
    )


@settings_router.put("/llm-pause", response_model=LLMPauseResponse)
async def put_llm_pause(body: LLMPauseUpdate, request: Request) -> LLMPauseResponse:
    """ck3_chronicler-gx7b: flip the global LLM pause toggle.

    paused=True: persist ``llm_paused: true`` + ``llm_paused_at: <now>``;
    invalidate the ingest cache so the next event-tick sees the new state.

    paused=False: clear both keys; invalidate the cache; then iterate
    every non-archived campaign in the registry and call
    :func:`drain_for_campaign` against each. The scheduler's gates
    (tracked / paused-per-char) plus the per-character lock and the
    DB-side dedup keep the drain safe under any races with a
    still-running save-tail tick. (27ov.43 fixed this list — it used
    to name a cooldown gate, a has_active check, and a
    scheduler.resume_for_drain method that never existed.)

    Returns the post-flip state including the drain counts when
    transitioning paused → unpaused.
    """
    from chronicler.api.dependencies import resolve_or_build_scheduler
    from chronicler.db.registry import list_campaigns
    from chronicler.narrative.pause import (
        _reset_llm_paused_cache,
        drain_for_campaign,
    )

    prior_paused, _ = _llm_pause_state()

    if body.paused:
        # Pause: write flag + paused_at. Drain not relevant.
        update_settings(
            {
                "llm_paused": True,
                "llm_paused_at": datetime.now(UTC).isoformat(),
            }
        )
        _reset_llm_paused_cache()
        # Clear the previous drain report so the FE doesn't show stale
        # counts after a re-pause.
        request.app.state.last_drain_report = None
        paused, paused_at = _llm_pause_state()
        return LLMPauseResponse(
            paused=paused,
            paused_at=paused_at,
            last_drain=None,
        )

    # Unpause: clear both keys, invalidate cache, drain across campaigns.
    update_settings({"llm_paused": None, "llm_paused_at": None})
    _reset_llm_paused_cache()

    if not prior_paused:
        # Already unpaused — no drain needed, just confirm state.
        return LLMPauseResponse(paused=False, paused_at=None, last_drain=None)

    # Drain across every open campaign. Skip silently when a campaign
    # can't be scheduler-resolved (no provider configured for that
    # campaign at this moment) — its work will be picked up by the next
    # save-tail tick that picks up the biography work. Best-effort by
    # design.
    cache = request.app.state.engine_cache
    registry_path = getattr(cache, "registry_path", None)
    aggregate_bio = 0
    campaigns = list_campaigns(include_archived=False, registry=registry_path)
    for campaign in campaigns:
        try:
            scheduler = resolve_or_build_scheduler(request, campaign)
        except Exception:
            log.warning(
                "llm-pause drain: skipping campaign %s — could not resolve a scheduler",
                campaign.id,
            )
            continue
        factory = scheduler.factory
        try:
            report = drain_for_campaign(
                factory=factory,
                scheduler=scheduler,
                campaign_id=campaign.id,
                registry_path=registry_path,
            )
        except Exception:
            log.exception(
                "llm-pause drain: campaign %s failed mid-drain; continuing",
                campaign.id,
            )
            continue
        aggregate_bio += report.biographies_scheduled
        log.info(
            "llm-pause drain: campaign %s scheduled %d biographies",
            campaign.id,
            report.biographies_scheduled,
        )

    request.app.state.last_drain_report = LLMPauseDrainReport(
        biographies_scheduled=aggregate_bio,
    )
    return LLMPauseResponse(
        paused=False,
        paused_at=None,
        last_drain=request.app.state.last_drain_report,
    )


@settings_router.get("/models", response_model=ResolvedModelsResponse)
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


@settings_router.get("/narrative-backend", response_model=NarrativeBackendResponse)
def get_narrative_backend() -> NarrativeBackendResponse:
    """The configured backend, its provenance, and whether it would work.

    Never returns a stored API key — only ``present`` plus which tier it
    came from.
    """
    return _narrative_backend_state()


@settings_router.put("/narrative-backend", response_model=NarrativeBackendResponse)
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


@settings_router.put("/models", response_model=ResolvedModelsResponse)
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


@settings_router.get("/provider-status", response_model=ProviderStatusResponse)
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


@settings_router.get(
    "/debug-log-status",
    response_model=DebugLogStatusResponse,
)
def get_debug_log_status() -> DebugLogStatusResponse:
    """ck3_chronicler-z6jm slice 1: read-only snapshot of CK3's
    debug.log size + rotation threshold. The Settings page polls this
    once a minute and raises a UI alert when ``exceeded`` is true."""
    status = stat_debug_log()
    return DebugLogStatusResponse(
        exists=status.exists,
        size_bytes=status.size_bytes,
        threshold_bytes=status.threshold_bytes,
        exceeded=status.exceeded,
        path=status.path,
    )


@settings_router.post(
    "/debug-log-rotate",
    response_model=DebugLogRotateResponse,
)
async def post_debug_log_rotate() -> DebugLogRotateResponse:
    """ck3_chronicler-z6jm slice 1: manual rotate.

    Archives debug.log to ``<logs>/archives/debug-YYYYMMDD-HHMM.log.gz``
    and truncates the original to 0 bytes. Resets every campaign's
    persisted ``tail_offset`` to 0 so the next chronicler tick picks
    up from the empty file.

    Best-effort on Windows: if CK3 holds the file open, the truncate
    phase fails silently (logged); the archive still gets written.
    The UI's job is to remind the user to close CK3 first.

    audit F-04 / ck3_chronicler-shen: gzip on a 200 MB log used to run
    inline — that pinned a threadpool worker for the duration. Now
    async + offloaded via asyncio.to_thread."""
    from chronicler.config import get_ck3_debug_log

    log_path = get_ck3_debug_log()
    archive_path = await asyncio.to_thread(archive_and_truncate, log_path)
    if archive_path is None:
        return DebugLogRotateResponse(
            rotated=False,
            archive_path=None,
            offsets_reset=0,
            message="no log to rotate (missing or already empty)",
        )
    offsets_reset = await asyncio.to_thread(reset_tail_offsets, [])
    return DebugLogRotateResponse(
        rotated=True,
        archive_path=str(archive_path),
        offsets_reset=offsets_reset,
        message=f"archived to {archive_path}",
    )


# --- ck3_chronicler-f9w.1: paths panel ---


def _to_path_info(resolved: ResolvedPath, override: str | None) -> PathInfo:
    return PathInfo(
        resolved=str(resolved.value) if resolved.value is not None else "",
        source=resolved.source,
        exists=resolved.exists,
        override=override,
    )


@settings_router.get("/paths", response_model=PathsSettingsResponse)
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


@settings_router.put("/paths", response_model=PathsSettingsResponse)
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


# --- ck3_chronicler-tbrm.4: prose repo settings ---


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


@settings_router.get("/prose-repo", response_model=ProseRepoStatusResponse)
def get_prose_repo_status() -> ProseRepoStatusResponse:
    """Status of the sibling ck3_chronicler_prose repo.

    The Settings ProseRepoCard reads this on mount + after every PUT.
    All three pips must be green for biography generation to work; the
    Card maps each to a red/green badge so the user can spot a missing
    CLAUDE.md or unset override at a glance.
    """
    return _build_prose_repo_status()


@settings_router.put("/prose-repo", response_model=ProseRepoStatusResponse)
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


@settings_router.post("/prose-repo/init", response_model=ProseRepoInitResponse)
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


# --- ck3_chronicler-kze6 (f9w.3): first-run wizard detection ---


@settings_router.get("/first-run", response_model=FirstRunStatusResponse)
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


@settings_router.post("/first-run/dismiss", response_model=FirstRunDismissResponse)
def dismiss_first_run_wizard() -> FirstRunDismissResponse:
    """Persist a wizard-dismissed-at timestamp so the wizard stops
    auto-opening on subsequent loads. audit F-39 / ck3_chronicler-tjql:
    response_model added so OpenAPI documents the actual shape."""
    now = datetime.now(UTC).isoformat()
    update_settings({"wizard_dismissed_at": now})
    return FirstRunDismissResponse(wizard_dismissed_at=now)
