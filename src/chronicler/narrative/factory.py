"""ck3_chronicler-cs1o, issue #45: transport selection for the narrative backend.

One factory, three transports, **one per process**:

- ``claude-code`` (default) shells out to ``claude --print`` and bills the
  subscription's monthly programmatic credit pool (post-2026-06-15:
  $100/seat/mo on Team Premium, use-it-or-lose-it).
- ``anthropic`` calls the Messages API directly on ``ANTHROPIC_API_KEY`` —
  the fallback for pool exhaustion or an unclaimed credit.
- ``openai-compatible`` (#21) covers OpenAI, DeepSeek, Ollama, LM Studio and
  OpenRouter through one protocol, which is what makes the public release
  runnable by someone with no Claude subscription at all.

The model is resolved identically for all three
(``narrative.model_resolution``) and the system prompt is assembled
identically (``narrative.prose_io``), so flipping the backend changes who
gets billed, never the prose.

Issue #45 replaced the hardcoded 2-tuple with the registry below. The old
shape had a live bug: ``resolve_backend()`` would return
``"openai-compatible"`` and ``chronicler doctor`` would probe it, but this
factory had no branch for it and fell through to ``raise ValueError`` — so
#21's transport was unreachable through the only thing that constructs
providers.

Still deliberately absent: per-kind providers and capability-based
routing. Cost attribution and the DB ``provider`` tag (``<prefix>:<model>``)
partition on one-provider-per-process, so per-kind routing would corrupt
cost data rather than fail loudly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from chronicler.narrative.backend_config import (
    CHRONICLER_NARRATIVE_BACKEND_ENV,
    DEFAULT_BACKEND,
    NARRATIVE_BACKEND_SETTING,
    resolve_backend,
)
from chronicler.narrative.provider import NarrativeProvider

log = logging.getLogger(__name__)

__all__ = [
    "CHRONICLER_NARRATIVE_BACKEND_ENV",
    "DEFAULT_BACKEND",
    "NARRATIVE_BACKEND_SETTING",
    "known_backends",
    "make_narrative_provider",
    "resolve_backend",
]

# A transport constructor: keyword-only prose repo + model, returns a
# provider. Every entry in the registry must match this shape, which is
# what lets the registry be a plain dict rather than a switch.
BackendFactory = Callable[..., NarrativeProvider]


def _make_claude_code(*, prose_repo_path: Path | None, model: str | None) -> NarrativeProvider:
    from chronicler.narrative.claude_code import make_claude_code_provider

    return make_claude_code_provider(prose_repo_path=prose_repo_path, model=model)


def _make_anthropic(*, prose_repo_path: Path | None, model: str | None) -> NarrativeProvider:
    from chronicler.narrative.anthropic import make_anthropic_provider

    provider = make_anthropic_provider(prose_repo_path=prose_repo_path, model=model)
    log.info(
        "narrative backend: anthropic (direct Messages API — "
        "generations bill the ANTHROPIC_API_KEY account, not the "
        "subscription pool)"
    )
    return provider


def _make_openai_compatible(
    *, prose_repo_path: Path | None, model: str | None
) -> NarrativeProvider:
    from chronicler.narrative.openai_compatible import make_openai_compatible_provider

    provider = make_openai_compatible_provider(prose_repo_path=prose_repo_path, model=model)
    log.info(
        "narrative backend: openai-compatible (%s at %s — generations bill "
        "that endpoint's account, not the subscription pool)",
        provider.name,
        provider.base_url,
    )
    return provider


# The registry. Adding a backend means adding one entry here; the valid-set
# message, `known_backends()` and the settings API's accepted values all
# derive from these keys, so there is no second list to keep in sync.
_REGISTRY: dict[str, BackendFactory] = {
    "claude-code": _make_claude_code,
    "anthropic": _make_anthropic,
    "openai-compatible": _make_openai_compatible,
}


def known_backends() -> tuple[str, ...]:
    """Valid backend ids, sorted for stable messages and API responses."""
    return tuple(sorted(_REGISTRY))


def make_narrative_provider(
    *,
    prose_repo_path: Path | None = None,
    model: str | None = None,
) -> NarrativeProvider:
    """Construct the configured narrative transport.

    Fails loud (ValueError) on an unknown backend or unusable backend
    config — a missing API key, an unknown preset, no endpoint, no model —
    so a misconfigured chronicler refuses to start rather than failing
    silently on the first tracked death.
    """
    backend = resolve_backend()
    factory = _REGISTRY.get(backend)
    if factory is None:
        raise ValueError(
            f"unknown narrative backend {backend!r} "
            f"(from the {NARRATIVE_BACKEND_SETTING!r} setting or "
            f"{CHRONICLER_NARRATIVE_BACKEND_ENV}); "
            f"valid values: {', '.join(known_backends())}"
        )
    return factory(prose_repo_path=prose_repo_path, model=model)
