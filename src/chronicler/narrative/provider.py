from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from chronicler.narrative.model_resolution import LongContextPolicy

PromptKind = Literal[
    "biography",
    "biography_woven",
    "chronicle_export",
]


@dataclass(frozen=True)
class NarrativeRequest:
    kind: PromptKind
    prompt_version: str
    system_prompt: str
    user_prompt: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NarrativeResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    # ck3_chronicler-cs1o: prompt-cache breakdown + provider-reported
    # cost. ``input_tokens`` REMAINS the summed total (marginal + cache
    # write + cache read) for back-compat with the token meter; the two
    # cache fields are subsets of it. ``cost_usd`` is the transport's
    # own number when it has one (claude-code envelope total_cost_usd)
    # or locally computed from the config rate card (anthropic
    # transport). All None on doubles that don't care.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None


class NarrativeProvider(ABC):
    """The narrative-generation seam.

    Two production transports implement it (ck3_chronicler-cs1o,
    superseding the ihkv single-backend decision after Anthropic's
    2026-06-15 programmatic-credit split moved ``claude --print`` onto
    a metered monthly pool):

    - :class:`chronicler.narrative.claude_code.ClaudeCodeProvider` —
      shells out to ``claude --print``; billed against the
      subscription's monthly programmatic credit. The default.
    - :class:`chronicler.narrative.anthropic.AnthropicProvider` —
      direct Messages API on ``ANTHROPIC_API_KEY``; the fallback for
      pool exhaustion. Selected via
      ``CHRONICLER_NARRATIVE_BACKEND=anthropic``.

    Both are pure transports: model choice is shared
    (``narrative.model_resolution``) and both write identical prose-repo
    artifacts (``narrative.prose_io``) — switching backends changes who
    gets billed, never the prose. There is still no capability-based
    routing and no per-kind provider registry: the factory
    (``narrative.factory.make_narrative_provider``) picks ONE transport
    per process. The ABC also remains the test seam — the suite injects
    lightweight doubles via ``create_app(narrative_provider=...)``.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    def name_for_kind(self, kind: PromptKind) -> str:
        """Provider tag for a specific prompt kind, for accurate DB attribution.

        ck3_chronicler-ju7j (Phase 1): the legacy ``name`` property was
        biography-anchored on ClaudeCodeProvider. Persistence call sites
        should use this method so the persisted ``provider`` reflects
        what actually ran. Default implementation falls back to ``name``
        so test doubles that don't override it keep working.
        """
        return self.name

    @property
    def prose_repo_path(self) -> Path | None:
        """Prose directory this transport's register is assembled from.

        Issue #19: the request-building call sites read this to run the
        shared system-prompt assembly
        (:func:`chronicler.narrative.prose_io.assemble_system_prompt`)
        before handing the request over — providers no longer read
        prose-repo instruction files themselves. Both production
        transports override it with a real path; the default of None
        keeps test doubles (which generate nothing and need no
        register) working unchanged.
        """
        return None

    # --- issue #46: contract members the transports had in common but the
    # ABC never promised. Each is non-abstract with a safe default, the
    # pattern `prose_repo_path` and `name_for_kind` already set, because the
    # suite injects doubles that implement only `name` + `generate`.

    def resolved_models(self) -> dict[str, str | None]:
        """Per-kind resolved-model snapshot, for the settings surface.

        All three production transports override this to honour a
        constructor-pinned ``model=``. The default is the module-level
        resolution every transport shares, which is exactly what
        ``GET /api/settings/models`` used to fall back to via
        ``getattr(provider, "resolved_models", None)`` — promoting it here
        retires that duck-typed call site.
        """
        from chronicler.narrative.model_resolution import resolved_models

        return resolved_models()

    @property
    def max_concurrent(self) -> int:
        """How many generations this transport tolerates in flight.

        The scheduler reads this instead of hardcoding a width. Four is the
        historical default (ck3_chronicler-w2se: hosted ``claude --print``
        is remote-bound, within the documented 2-4 safe range) and the two
        Anthropic-family transports inherit it deliberately — both are
        remote calls with rate-limit headroom.

        ``OpenAICompatibleProvider`` overrides it per preset, and that is
        the case this exists for: a local Ollama or LM Studio server shares
        one GPU, so a 4-wide fan-out thrashes it. Its local presets declare
        1.
        """
        return 4

    @property
    def long_context_policy(self) -> LongContextPolicy:
        """How this transport asks for the 1M window (issue #46).

        Defaults to :attr:`LongContextPolicy.NONE` — the safe answer for
        any transport that has not thought about it, and the correct one
        for every OpenAI-compatible endpoint, whose model ids and context
        limits the server decides. The two Anthropic-family transports
        override it.
        """
        return LongContextPolicy.NONE

    async def aclose(self) -> None:
        """Release transport resources. Idempotent; safe on every path.

        Default is a no-op: ``ClaudeCodeProvider`` holds no client (it
        spawns a subprocess per call), and test doubles hold nothing. The
        two HTTP transports own an ``httpx.AsyncClient`` and override this.

        Callers must invoke it on **every** shutdown path — the app
        lifespan's ``finally`` and the CLI one-shots — including the
        exception path, which is when a leaked connection pool matters
        most.
        """
        return None

    @abstractmethod
    async def generate(self, req: NarrativeRequest) -> NarrativeResponse: ...
