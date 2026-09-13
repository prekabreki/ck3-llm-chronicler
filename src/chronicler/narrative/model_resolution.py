"""ck3_chronicler-cs1o: backend-neutral per-kind model resolution.

The narrative backend is dual-transport (claude-code subprocess |
direct Anthropic Messages API) but the MODEL is one shared decision —
switching transports changes who gets billed, never the prose quality.
Default Opus for every kind. Long-context (the 1M window, 2x input
price above 200k) is NOT part of the resolved tag; it is applied
per-call by the transport when the estimated prompt actually needs it.
Only 1 of 27 real bios ever exceeded 200k input — the old flat
``claude-opus-4-7[1m]`` default paid the 2x surcharge on ~96% of calls
for nothing (see the cs1o spec for the grounded numbers).
"""

from __future__ import annotations

import os
from enum import StrEnum

# Backend-neutral envs (cs1o). Win over the legacy CHRONICLER_CLAUDE_CODE_*
# spellings, which remain as deprecated aliases for one release.
NARRATIVE_MODEL_ENV = "CHRONICLER_NARRATIVE_MODEL"
NARRATIVE_BIO_MODEL_ENV = "CHRONICLER_NARRATIVE_BIO_MODEL"
NARRATIVE_CLOSING_MODEL_ENV = "CHRONICLER_NARRATIVE_CLOSING_MODEL"
# Legacy aliases (ck3_chronicler-5d9o era). Still honoured; the neutral
# spelling wins when both are set.
LEGACY_MODEL_ENV = "CHRONICLER_CLAUDE_CODE_MODEL"
LEGACY_BIO_MODEL_ENV = "CHRONICLER_CLAUDE_CODE_BIO_MODEL"
LEGACY_CLOSING_MODEL_ENV = "CHRONICLER_CLAUDE_CODE_CLOSING_MODEL"

ALL_MODEL_ENV_VARS = (
    NARRATIVE_MODEL_ENV,
    NARRATIVE_BIO_MODEL_ENV,
    NARRATIVE_CLOSING_MODEL_ENV,
    LEGACY_MODEL_ENV,
    LEGACY_BIO_MODEL_ENV,
    LEGACY_CLOSING_MODEL_ENV,
)

# Durable settings keys, read from settings.json in the chronicler data dir.
# An env-only knob dies with the shell that launched the app, so a model
# picked for a campaign silently reverted to the default on the next start
# (or after a terminal crash) with nothing in the logs to say the choice was
# lost. Precedence within a tier follows the config._resolve convention —
# settings beat env — so a persisted choice can't be quietly downgraded by a
# stale export. This selects a MODEL for the permanent claude-code backend;
# it is NOT provider selection (ihkv: provider swapping stays removed).
NARRATIVE_MODEL_SETTING = "narrative_model"
NARRATIVE_BIO_MODEL_SETTING = "narrative_bio_model"
NARRATIVE_CLOSING_MODEL_SETTING = "narrative_closing_model"

ALL_MODEL_SETTING_KEYS = (
    NARRATIVE_MODEL_SETTING,
    NARRATIVE_BIO_MODEL_SETTING,
    NARRATIVE_CLOSING_MODEL_SETTING,
)

# Base model — no [1m] bracket. cs1o: was claude-opus-4-7[1m] flat.
DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_BIO_MODEL = DEFAULT_MODEL
DEFAULT_CLOSING_MODEL = DEFAULT_MODEL

# Engage the 1M window above this estimated prompt size. 180k leaves
# ~10% headroom under the 200k standard window for the chars/4
# heuristic's error bars.
LONG_CONTEXT_THRESHOLD_TOKENS = 180_000


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _setting(key: str) -> str:
    """Read a model tag from the settings store, or ``""`` if absent.

    Best-effort by design: a missing, malformed or unreadable settings file
    must never break narrative generation, it just falls through to the env
    chain and then the default. Imported lazily so this module keeps working
    for callers that don't want the store (mirrors config._resolve).
    """
    try:
        from chronicler.settings_store import load_settings

        value = load_settings().get(key)
    except Exception:  # noqa: BLE001 — settings are best-effort, never fatal
        return ""
    return value.strip() if isinstance(value, str) else ""


def _global_override() -> str:
    """The resolved cross-kind model override, or ``""`` when unset."""
    return _setting(NARRATIVE_MODEL_SETTING) or _env(NARRATIVE_MODEL_ENV) or _env(LEGACY_MODEL_ENV)


def model_for_kind(kind: str) -> str:
    """Resolve the BASE model tag for a prompt kind.

    Two tiers, global then per-kind; within each tier the durable setting
    wins over the neutral env, which wins over the legacy env. Globals
    override per-kind so a single flip (e.g. "everything on Sonnet") needs
    one knob — preserving the pre-5d9o single-model shape callers expected.

    Full order: settings global > neutral global env > legacy global env >
    settings per-kind > neutral per-kind env > legacy per-kind env >
    per-kind default.
    """
    global_value = _global_override()
    if global_value:
        return global_value
    if kind == "chronicle_export":
        per_kind_setting = NARRATIVE_CLOSING_MODEL_SETTING
        per_kind = (NARRATIVE_CLOSING_MODEL_ENV, LEGACY_CLOSING_MODEL_ENV)
        default = DEFAULT_CLOSING_MODEL
    else:
        # biography / biography_woven / any other kind → biography defaults
        per_kind_setting = NARRATIVE_BIO_MODEL_SETTING
        per_kind = (NARRATIVE_BIO_MODEL_ENV, LEGACY_BIO_MODEL_ENV)
        default = DEFAULT_BIO_MODEL
    value = _setting(per_kind_setting)
    if value:
        return value
    for var in per_kind:
        value = _env(var)
        if value:
            return value
    return default


def resolved_models() -> dict[str, str | None]:
    """Per-kind snapshot of resolved model tags + the global-override state.

    Powers ``GET /api/settings/models`` (claude_code.py re-exports this
    so the route keeps working unchanged). Env and settings tweaks both
    take effect on the next call; no chronicler restart needed.
    """
    global_override = _global_override() or None
    return {
        "biography": model_for_kind("biography"),
        "closing": model_for_kind("chronicle_export"),
        "global_override": global_override,
    }


def estimate_prompt_tokens(text: str) -> int:
    """chars/4 heuristic. Good to ~±20% on English prose + markdown,
    which is all the briefing pipeline emits — accurate enough for a
    threshold with 20k tokens of headroom built in."""
    return len(text) // 4


def needs_long_context(estimated_tokens: int) -> bool:
    return estimated_tokens > LONG_CONTEXT_THRESHOLD_TOKENS


class LongContextPolicy(StrEnum):
    """How a transport asks for the 1M window, if it can at all.

    Issue #46: this used to be implicit — each transport reached for its
    own mechanism and the shared helper was *named* after one of them
    (``apply_long_context_claude_code``). With three backends that is the
    shape mistakes hide in: the ``[1m]`` bracket is a claude-code model-tag
    convention and ``anthropic-beta`` is an Anthropic API header, so
    applying either to an OpenAI-compatible endpoint would be silently
    wrong — a 400 at best, a model id the server has never heard of at
    worst. Making the policy a declared property means a transport that
    forgets to opt in gets ``NONE`` (correct, just no long window) instead
    of inheriting somebody else's spelling.
    """

    # No long-context mechanism. The endpoint's own default window
    # applies — right for every openai-compatible target, whose models
    # and limits the server decides.
    NONE = "none"
    # claude-code: append ``[1m]`` to the model tag.
    MODEL_BRACKET = "model-bracket"
    # Anthropic Messages API: send the 1M beta header.
    BETA_HEADER = "beta-header"


def apply_long_context_bracket(model: str, estimated_tokens: int) -> str:
    """Append the ``[1m]`` bracket variant when the prompt needs the 1M
    window. No-op when the tag already carries a bracket — an explicit
    user override is never rewritten.

    Issue #46 renamed this from ``apply_long_context_claude_code``: the
    bracket is the claude-code *spelling*, but which transports use it is
    now declared by :class:`LongContextPolicy`, not encoded in a function
    name that invites the wrong caller.
    """
    if "[" in model:
        return model
    if needs_long_context(estimated_tokens):
        return f"{model}[1m]"
    return model
