"""Issue #45: settings-first configuration for the three narrative backends.

One place owns *what the backend is* and *what it is pointed at*, so the
factory (which constructs) and ``chronicler doctor`` (which diagnoses)
cannot drift into two different vocabularies for the same four failures.
Before this module, ``doctor`` read the ``CHRONICLER_OPENAI_*`` env vars
directly and worded the failures well, while the factory read them again
and worded them differently — and neither looked at settings.json.

Precedence is the repo-wide convention (``config._resolve``,
``model_resolution.model_for_kind``): **the durable setting wins over the
env var**, which wins over the default. An env-only knob dies with the
shell that launched the app, so a backend chosen in the UI would silently
revert on the next start; a stale ``export`` in a shell profile must not
be able to quietly downgrade a persisted choice.

Settings are best-effort (``settings_store``'s own contract: "an
enhancement, not a load-bearing dependency"). A missing, corrupt or
unreadable settings file falls through to env-then-default rather than
refusing to construct a provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from chronicler.narrative.openai_compatible import (
    GENERIC_PRESET_ID,
    OPENAI_API_KEY_ENV,
    OPENAI_BASE_URL_ENV,
    OPENAI_MODEL_ENV,
    OPENAI_PRESET_ENV,
    PRESETS,
    VENDOR_API_KEY_ENV,
    Preset,
)

CHRONICLER_NARRATIVE_BACKEND_ENV = "CHRONICLER_NARRATIVE_BACKEND"
DEFAULT_BACKEND = "claude-code"

# Durable settings keys. Flat namespace, one owner per key
# (settings_store's documented schema convention).
NARRATIVE_BACKEND_SETTING = "narrative_backend"
OPENAI_PRESET_SETTING = "openai_preset"
OPENAI_BASE_URL_SETTING = "openai_base_url"
OPENAI_MODEL_SETTING = "openai_model"
OPENAI_API_KEY_SETTING = "openai_api_key"
ANTHROPIC_API_KEY_SETTING = "anthropic_api_key"

# Every settings key this module owns. The settings API uses it to scrub
# secrets out of GET responses without hand-maintaining a second list.
SECRET_SETTING_KEYS = frozenset({OPENAI_API_KEY_SETTING, ANTHROPIC_API_KEY_SETTING})

ALL_BACKEND_SETTING_KEYS = (
    NARRATIVE_BACKEND_SETTING,
    OPENAI_PRESET_SETTING,
    OPENAI_BASE_URL_SETTING,
    OPENAI_MODEL_SETTING,
    OPENAI_API_KEY_SETTING,
    ANTHROPIC_API_KEY_SETTING,
)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def setting_str(key: str) -> str:
    """Read a string setting, or ``""`` when absent/unusable.

    Deliberately swallows everything: see the module docstring on
    settings being best-effort. Imported lazily so this module stays
    usable for callers that don't want the store (mirrors
    ``model_resolution._setting``, which this generalises).
    """
    try:
        from chronicler.settings_store import load_settings

        value = load_settings().get(key)
    except Exception:  # noqa: BLE001 — settings are best-effort, never fatal
        return ""
    return value.strip() if isinstance(value, str) else ""


def _first(setting_key: str, *env_names: str) -> str:
    """Settings, then each env var in order, then ``""``."""
    value = setting_str(setting_key)
    if value:
        return value
    for name in env_names:
        value = _env(name)
        if value:
            return value
    return ""


def resolve_backend() -> str:
    """The configured backend id: settings, then env, then the default.

    An *unset* backend resolves to :data:`DEFAULT_BACKEND`. An *unknown*
    one is returned as configured — naming the valid set is the caller's
    job (the factory raises, ``doctor`` reports a red row). Silently
    resolving a typo to the default would be worse than useless: it would
    bill the wrong account while looking healthy.
    """
    return _first(NARRATIVE_BACKEND_SETTING, CHRONICLER_NARRATIVE_BACKEND_ENV).lower() or (
        DEFAULT_BACKEND
    )


def resolve_anthropic_api_key() -> str | None:
    """Anthropic key from settings, then ``ANTHROPIC_API_KEY``."""
    from chronicler.narrative.anthropic import ANTHROPIC_API_KEY_ENV

    return _first(ANTHROPIC_API_KEY_SETTING, ANTHROPIC_API_KEY_ENV) or None


def resolve_openai_api_key() -> str | None:
    """openai-compatible key: settings, then the chronicler-specific env,
    then the vendor's conventional ``OPENAI_API_KEY``."""
    return _first(OPENAI_API_KEY_SETTING, OPENAI_API_KEY_ENV, VENDOR_API_KEY_ENV) or None


def backend_for_provider_name(provider_name: str) -> str:
    """Map a provider tag (``"<prefix>:<model>"``) back to its backend id.

    Issue #45: ``/provider-status`` used to derive its ``mode`` from two
    hardcoded ``startswith`` branches, so **every openai-compatible
    transport reported ``"other"``** — its tag prefix is the *preset* id
    (``openai:``, ``deepseek:``, ``ollama:``…), which is what the cost
    table partitions on, not the backend id.

    Returns ``"other"`` for anything unrecognised, which is the existing
    contract for test doubles and keeps the UI's three-way switch honest.
    """
    # Lazy: factory imports this module, so a module-level import would
    # close the cycle. Reading the registry rather than restating its keys
    # is the point — a fourth backend must not need an edit here.
    from chronicler.narrative.factory import known_backends

    prefix = provider_name.partition(":")[0].strip().lower()
    if not prefix:
        return "other"
    if prefix in known_backends():
        return prefix
    if prefix in PRESETS:
        return "openai-compatible"
    return "other"


@dataclass(frozen=True)
class OpenAIBackendConfig:
    """A validated openai-compatible target.

    Only ever produced by :func:`resolve_openai_config` on the success
    path, so holding one means the four things that can be missing are
    not missing.
    """

    preset: Preset | None
    preset_name: str | None
    base_url: str
    model: str
    api_key: str | None

    @property
    def label(self) -> str:
        """Human-facing name for this target, for diagnostics."""
        return self.preset.id if self.preset else f"{GENERIC_PRESET_ID} (no preset)"

    @property
    def key_state(self) -> str:
        return "key present" if self.api_key else "no key"


def resolve_openai_config() -> tuple[OpenAIBackendConfig | None, str | None]:
    """Resolve and validate the openai-compatible target.

    Returns ``(config, None)`` or ``(None, message)`` — never both, never
    neither. A tuple rather than an exception because the two callers need
    opposite things from the same check: the factory raises ``ValueError``
    to refuse construction, while ``doctor`` renders the message as a red
    row and carries on with its remaining probes.

    The four failure modes are the ones ``doctor`` has always worded, kept
    verbatim so the message a user pastes into a bug report is the same
    one whichever surface produced it.
    """
    preset_name = _first(OPENAI_PRESET_SETTING, OPENAI_PRESET_ENV).lower() or None
    base_url = _first(OPENAI_BASE_URL_SETTING, OPENAI_BASE_URL_ENV)
    model = _first(OPENAI_MODEL_SETTING, OPENAI_MODEL_ENV)

    preset: Preset | None = None
    if preset_name:
        preset = PRESETS.get(preset_name)
        if preset is None:
            return None, (
                f"backend=openai-compatible but {OPENAI_PRESET_ENV}="
                f"{preset_name!r} is not a known preset "
                f"(valid: {', '.join(sorted(PRESETS))})"
            )

    endpoint = base_url or (preset.base_url if preset else "")
    if not endpoint:
        return None, (
            "backend=openai-compatible but no endpoint is configured — set "
            f"{OPENAI_BASE_URL_ENV} or pick a preset via {OPENAI_PRESET_ENV} "
            f"({', '.join(sorted(PRESETS))})"
        )
    if not model:
        return None, (
            f"backend=openai-compatible but {OPENAI_MODEL_ENV} is not set — "
            "the endpoint decides which models exist, so there is no default "
            "to fall back to"
        )

    api_key = resolve_openai_api_key()
    if preset is not None and preset.requires_key and api_key is None:
        return None, (
            f"backend=openai-compatible preset={preset.id} requires an API "
            f"key — set {OPENAI_API_KEY_ENV} (or {VENDOR_API_KEY_ENV}); "
            "generation will refuse to start"
        )

    return (
        OpenAIBackendConfig(
            preset=preset,
            preset_name=preset_name,
            base_url=endpoint,
            model=model,
            api_key=api_key,
        ),
        None,
    )
