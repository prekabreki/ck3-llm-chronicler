"""OpenAICompatibleProvider — chat-completions transport (issue #21).

The third and last narrative transport. OpenAI, DeepSeek, OpenRouter,
Ollama, LM Studio and vLLM all speak the same ``POST
/chat/completions`` protocol, so they are ONE transport parameterised by
base URL + optional key + model rather than a provider per vendor. That
is the whole reason the public tool needs no LiteLLM and no per-vendor
SDKs (public-release design spec §2.2).

Modelled on :mod:`chronicler.narrative.anthropic` — bare httpx (the
chronicler's HTTP layer is bare httpx throughout), the same retry set,
the same wall-clock/read/connect timeout split, the same truncation
guard, the same prose-repo parity. Deliberate deltas:

- **Non-streaming.** v1 issues one POST. The Anthropic transport streams
  because non-streaming long requests hit a hard API limit there; the
  chat-completions endpoints have no equivalent restriction, and a local
  model behind Ollama returns one JSON body anyway. The 600 s wall-clock
  ceiling plus a 120 s read timeout is what catches a hung endpoint.
- **The model is required, with no default.** Anthropic's transport can
  fall back to a shared default tag because it knows its vendor's model
  names. Here the endpoint decides what exists (``qwen3:14b``,
  ``gpt-5.6-luna``, whatever a proxy exposes), so a missing model is a
  construction error rather than a first-death 404.
- **Per-vendor concurrency.** A cloud endpoint tolerates the scheduler's
  fan-out of 4; a single local GPU serialises regardless and just
  collects queue overhead, which is why the Ollama era ran a semaphore of
  1. :attr:`max_concurrent` carries the right number per preset for the
  scheduler to honour.
- **The provider tag carries the PRESET, not the transport.** Cost
  attribution partitions on the first ``:`` of the tag
  (:func:`chronicler.cost.lookup_token_price`), so an OpenAI generation
  must report ``openai:<model>`` to reach the OpenAI rate card. Only an
  unpresetted endpoint reports ``openai-compatible:<model>``.

Backend selection, settings persistence and the UI are deliberately NOT
here (issues #22/#23): this module is the transport plus its env-var
constructor, mirroring ``make_anthropic_provider``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from chronicler.narrative.prose_io import (
    BRIEFING_WRAPPER_INSTRUCTION,
    _missing_prose_message,
    briefing_paths,
    format_attribution_footer,
    get_prose_repo_path,
    git_commit_biography,
    render_briefing_markdown,
)
from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
    PromptKind,
)

log = logging.getLogger(__name__)

# Parity with the Anthropic transport: real biographies average ~9.5k
# output tokens, p95 19.7k, max observed 22k.
DEFAULT_MAX_OUTPUT_TOKENS = 32_000
# Wall-clock ceiling for one generation INCLUDING retries — parity with
# both existing transports. Local models on modest hardware are the slow
# case this has to accommodate.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0
_READ_TIMEOUT_SECONDS = 120.0
_CONNECT_TIMEOUT_SECONDS = 15.0

_RETRY_ATTEMPTS = 3
# 529 is Anthropic-specific ("overloaded"); the rest are the shared
# transient set. 400/401/403/404/422 are deterministic — retrying them
# burns the death event's time budget for nothing.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

OPENAI_PRESET_ENV = "CHRONICLER_OPENAI_PRESET"
OPENAI_BASE_URL_ENV = "CHRONICLER_OPENAI_BASE_URL"
OPENAI_API_KEY_ENV = "CHRONICLER_OPENAI_API_KEY"
OPENAI_MODEL_ENV = "CHRONICLER_OPENAI_MODEL"
# Users arrive with this already exported; honoured as a fallback so they
# don't have to re-export the same secret under a chronicler name.
VENDOR_API_KEY_ENV = "OPENAI_API_KEY"

# Tag for an endpoint with no preset (self-hosted vLLM, a proxy). It has
# no rate-card entry by design: an unknown paid model must read as
# uncosted, never as free.
GENERIC_PRESET_ID = "openai-compatible"


@dataclass(frozen=True)
class Preset:
    """A vendor's defaults. ``id`` doubles as the cost-attribution prefix,
    so it MUST match a key in :data:`chronicler.cost._PROVIDER_COSTS`
    (or be deliberately absent, as with ``openrouter``, whose per-model
    prices can't be pinned)."""

    id: str
    base_url: str
    requires_key: bool
    max_concurrent: int
    # OpenAI's chat-completions endpoint rejects the legacy ``max_tokens``
    # field for its current (reasoning) models and wants
    # ``max_completion_tokens``; every other implementation of the
    # protocol still expects ``max_tokens``. Getting this wrong 400s every
    # single generation on that vendor.
    max_tokens_field: str = "max_tokens"


PRESETS: dict[str, Preset] = {
    "openai": Preset(
        id="openai",
        base_url="https://api.openai.com/v1",
        requires_key=True,
        max_concurrent=4,
        max_tokens_field="max_completion_tokens",
    ),
    "deepseek": Preset(
        id="deepseek",
        base_url="https://api.deepseek.com/v1",
        requires_key=True,
        max_concurrent=4,
    ),
    "openrouter": Preset(
        id="openrouter",
        base_url="https://openrouter.ai/api/v1",
        requires_key=True,
        max_concurrent=4,
    ),
    "ollama": Preset(
        id="ollama",
        base_url="http://localhost:11434/v1",
        requires_key=False,
        max_concurrent=1,
    ),
    "lmstudio": Preset(
        id="lmstudio",
        base_url="http://localhost:1234/v1",
        requires_key=False,
        max_concurrent=1,
    ),
}

_GENERIC_PRESET = Preset(
    id=GENERIC_PRESET_ID,
    base_url="",
    requires_key=False,
    max_concurrent=1,
)


class OpenAICompatibleProvider(NarrativeProvider):
    """Chat-completions client implementing :class:`NarrativeProvider`.

    Constructed once per chronicler process. ``generate`` is safe to call
    concurrently — each call materialises its own briefing file with a
    unique version number; the scheduler serialises per character via its
    own locks and bounds total fan-out with :attr:`max_concurrent`.
    """

    def __init__(
        self,
        *,
        model: str,
        preset: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        prose_repo_path: Path | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_concurrent: int | None = None,
        client: httpx.AsyncClient | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        retry_base_delay: float = 2.0,
    ) -> None:
        if not model or not model.strip():
            raise ValueError(
                "OpenAICompatibleProvider requires a non-empty model — the "
                "endpoint decides which models exist, so there is no "
                "sensible default to fall back to"
            )
        if preset is not None:
            resolved = PRESETS.get(preset.strip().lower())
            if resolved is None:
                raise ValueError(
                    f"unknown preset {preset!r}; valid presets: "
                    f"{', '.join(sorted(PRESETS))} (or pass base_url for any "
                    "other OpenAI-compatible endpoint)"
                )
        else:
            resolved = _GENERIC_PRESET
        url = (base_url or resolved.base_url).strip()
        if not url:
            raise ValueError(
                "OpenAICompatibleProvider requires a base_url (or a preset "
                f"that supplies one); valid presets: {', '.join(sorted(PRESETS))}"
            )
        key = (api_key or "").strip() or None
        if resolved.requires_key and key is None:
            raise ValueError(
                f"the {resolved.id!r} preset requires an API key; set "
                f"{OPENAI_API_KEY_ENV} (or {VENDOR_API_KEY_ENV}) — refusing to "
                "start a transport that would 401 on the first death"
            )
        self._model = model.strip()
        self._preset = resolved
        self._base_url = url.rstrip("/")
        self._api_key = key
        self._prose_repo = prose_repo_path or get_prose_repo_path()
        self._max_output_tokens = max_output_tokens
        self._max_concurrent = (
            max_concurrent if max_concurrent is not None else resolved.max_concurrent
        )
        self._timeout = request_timeout
        self._retry_base_delay = retry_base_delay
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                request_timeout,
                read=_READ_TIMEOUT_SECONDS,
                connect=_CONNECT_TIMEOUT_SECONDS,
            )
        )

    # --- identity ---

    @property
    def name(self) -> str:
        return f"{self._preset.id}:{self._model}"

    def name_for_kind(self, kind: PromptKind) -> str:
        # One configured model for every kind. The shared per-kind
        # resolution (narrative.model_resolution) defaults to Anthropic
        # tags, which this endpoint has never heard of — inheriting them
        # would send `claude-opus-4-7` to Ollama.
        return self.name

    def resolved_models(self) -> dict[str, str | None]:
        """Per-kind resolved-model snapshot (settings endpoint surface)."""
        return {
            "biography": self._model,
            "closing": self._model,
            "global_override": self._model,
        }

    @property
    def prose_repo_path(self) -> Path:
        return self._prose_repo

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def preset_id(self) -> str:
        return self._preset.id

    @property
    def max_concurrent(self) -> int:
        """Generations this endpoint can serve at once.

        One local GPU can only run one; a cloud endpoint is happy with the
        scheduler's default fan-out. The scheduler reads this (issue #22).
        """
        return self._max_concurrent

    # --- generation ---

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        # Fail loud before touching the filesystem — parity with both
        # existing transports. Without these two checks a misconfigured
        # install silently persists register-less generic-assistant prose
        # as a valid biography (the defect issue #19 fixed on the
        # Anthropic path).
        if not self._prose_repo.is_dir():
            raise RuntimeError(
                _missing_prose_message(f"prose repo not found at {self._prose_repo}")
            )
        if not req.system_prompt.strip():
            raise RuntimeError(
                "refusing to call the chat-completions endpoint with an empty "
                "system prompt: the chronicler register must be assembled from "
                "the prose dir before the request reaches a transport (see "
                "chronicler.narrative.prose_io.assemble_system_prompt)"
            )

        metadata = req.metadata or {}
        campaign_uuid = metadata.get("campaign_uuid") or "_unscoped"
        character_id = metadata.get("character_id") or "_unknown"

        brief_path, bio_path, version = briefing_paths(
            prose_repo=self._prose_repo,
            campaign_uuid=campaign_uuid,
            character_id=character_id,
        )
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        bio_path.parent.mkdir(parents=True, exist_ok=True)

        briefing_text = render_briefing_markdown(req)
        brief_path.write_text(briefing_text, encoding="utf-8")
        rel_brief = brief_path.relative_to(self._prose_repo).as_posix()
        rel_bio = bio_path.relative_to(self._prose_repo).as_posix()

        body: dict = {
            "model": self._model,
            "stream": False,
            "messages": [
                {"role": "system", "content": req.system_prompt},
                {"role": "user", "content": BRIEFING_WRAPPER_INSTRUCTION + briefing_text},
            ],
            self._preset.max_tokens_field: self._max_output_tokens,
        }
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        log.info(
            "%s: invoking %s for character_id=%s campaign=%s version=%d kind=%s",
            self._preset.id,
            self._model,
            character_id,
            campaign_uuid,
            version,
            req.kind,
        )

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._post_with_retries(body, headers),
                timeout=self._timeout,
            )
        except TimeoutError:
            # Note: only asyncio's own TimeoutError is converted. A
            # CancelledError from the queue must propagate untouched, so
            # it is deliberately not caught here.
            raise RuntimeError(
                f"{self._preset.id} generate timed out after {self._timeout}s for "
                f"character_id={character_id} campaign={campaign_uuid}"
            ) from None
        latency_ms = int((time.monotonic() - start) * 1000)

        text = result["text"]
        if not text:
            raise RuntimeError(
                f"{self._preset.id} returned no prose for "
                f"character_id={character_id} campaign={campaign_uuid}"
            )
        model_actual = result["model"]
        input_tokens = result["input_tokens"]
        cache_read_tokens = result["cache_read_tokens"]
        output_tokens = result["output_tokens"]

        cost_usd = self._compute_cost_usd(
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            output_tokens=output_tokens,
        )

        # Prose-repo parity: bio file + attribution footer + git commit.
        bio_path.write_text(text + "\n", encoding="utf-8")
        footer = format_attribution_footer(
            model=model_actual,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            agent=self._agent_label(),
        )
        try:
            with bio_path.open("a", encoding="utf-8") as fh:
                fh.write(footer)
        except OSError:
            log.exception(
                "%s: failed to append attribution footer to %s; biography body is unaffected",
                self._preset.id,
                bio_path,
            )
        await git_commit_biography(
            prose_repo=self._prose_repo,
            rel_brief=rel_brief,
            rel_bio=rel_bio,
            character_id=character_id,
            campaign_uuid=campaign_uuid,
            version=version,
            model=model_actual,
        )

        return NarrativeResponse(
            text=text,
            model=model_actual,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cache_read_tokens=cache_read_tokens,
            # No implementation of this protocol reports cache WRITES
            # separately, so the field stays unknown rather than a fake 0.
            cache_write_tokens=None,
            cost_usd=cost_usd,
        )

    def _agent_label(self) -> str:
        """Footer attribution: who set this text down, readable months
        later when the file on disk is the only record."""
        return f"{self._preset.id} ({self._base_url})"

    def _compute_cost_usd(
        self,
        *,
        input_tokens: int | None,
        cache_read_tokens: int | None,
        output_tokens: int | None,
    ) -> float | None:
        """USD for the row, or None when the model isn't on the rate card.

        The None-vs-0.0 distinction is load-bearing and is why
        :func:`chronicler.cost.has_token_price` exists: a local
        generation costs a real 0.0 (the aggregate treats NULL as
        "uncosted, price it from the card" and 0.0 as costed), while an
        unrecognised paid model is unknown and must NOT be billed as free.
        """
        try:
            from chronicler.cost import compute_generation_cost, has_token_price
        except ImportError:  # pragma: no cover — pricing layer always present
            return None
        tag = self.name
        if not has_token_price(tag):
            return None
        return compute_generation_cost(
            tag,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=None,
            output_tokens=output_tokens,
        )

    async def _post_with_retries(self, body: dict, headers: dict) -> dict:
        """Retry wrapper: transport errors + retryable statuses get
        ``_RETRY_ATTEMPTS`` tries with exponential backoff; auth and
        client errors raise immediately.

        Only ``httpx.HTTPStatusError`` and ``httpx.TransportError`` are
        caught. ``asyncio.CancelledError`` is a BaseException and must
        propagate — a broad ``except Exception`` here would turn a queue
        cancellation into two more attempts against the endpoint.
        """
        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await self._post_once(body, headers)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    # The key itself is never logged — only where it came
                    # from, which is the actionable part.
                    log.error(
                        "%s: authentication failed (%d) — check %s (or %s)",
                        self._preset.id,
                        status,
                        OPENAI_API_KEY_ENV,
                        VENDOR_API_KEY_ENV,
                    )
                    raise
                if status not in _RETRYABLE_STATUS:
                    raise
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                delay = self._retry_base_delay * (4**attempt)
                log.warning(
                    "%s: attempt %d/%d failed (%s); retrying in %.1fs",
                    self._preset.id,
                    attempt + 1,
                    _RETRY_ATTEMPTS,
                    type(last_exc).__name__,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _post_once(self, body: dict, headers: dict) -> dict:
        """One POST to ``/chat/completions``.

        Returns ``{"text", "model", "input_tokens", "cache_read_tokens",
        "output_tokens"}``. Raises :class:`httpx.HTTPStatusError` on a
        non-2xx status and :class:`RuntimeError` on a truncated or
        malformed completion.
        """
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"{self._preset.id}: malformed (non-JSON) response from "
                f"{self._base_url}/chat/completions"
            ) from exc

        choices = payload.get("choices") or []
        if not choices:
            # A proxy that answers 200 with an error body, rather than
            # crashing with an IndexError three layers down.
            raise RuntimeError(
                f"{self._preset.id}: malformed response — no choices returned "
                f"(payload keys: {sorted(payload)})"
            )
        choice = choices[0] or {}
        finish_reason = choice.get("finish_reason")
        # Ported audit M-N5: a length-truncated completion carries
        # non-empty text, so without this it passes the non-empty check,
        # gets a footer, gets git-committed and lands in the DB as a
        # finished biography — a mid-sentence cut recorded as complete.
        # Raise so the pipeline's error net records a failed generation.
        if finish_reason == "length":
            text_len = len(str((choice.get("message") or {}).get("content") or ""))
            raise RuntimeError(
                f"{self._preset.id}: response truncated at the output limit "
                f"(model={payload.get('model')}, finish_reason=length, "
                f"chars={text_len}) — raise max_output_tokens"
            )
        if finish_reason not in (None, "stop", "eos", "end_turn"):
            log.warning("%s: unexpected finish_reason %r", self._preset.id, finish_reason)

        text = str((choice.get("message") or {}).get("content") or "").strip()
        usage = payload.get("usage") or {}
        return {
            "text": text,
            "model": str(payload.get("model") or self._model),
            # OpenAI's prompt_tokens already INCLUDES cached input, which
            # is exactly the summed-total semantics NarrativeResponse
            # documents for input_tokens.
            "input_tokens": _int_or_none(usage.get("prompt_tokens")),
            "cache_read_tokens": _cache_read_tokens(usage),
            "output_tokens": _int_or_none(usage.get("completion_tokens")),
        }

    async def aclose(self) -> None:
        """Close the HTTP client iff this provider owns it (F-54 ownership
        pattern: injected clients survive, owned clients are closed
        exactly once; idempotent)."""
        if self._owned_client:
            await self._client.aclose()


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cache_read_tokens(usage: dict) -> int | None:
    """Cached-input tokens, under either spelling.

    OpenAI nests them at ``prompt_tokens_details.cached_tokens``; DeepSeek
    reports a flat ``prompt_cache_hit_tokens``. Reading only the first
    spelling silently reports every DeepSeek generation as fully uncached.
    """
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _int_or_none(details.get("cached_tokens"))
        if cached:
            return cached
    cached = _int_or_none(usage.get("prompt_cache_hit_tokens"))
    return cached or None


def get_openai_api_key() -> str | None:
    """Key from the environment: the chronicler-scoped var wins over the
    vendor's conventional one. None when both are unset/blank."""
    for var in (OPENAI_API_KEY_ENV, VENDOR_API_KEY_ENV):
        raw = os.environ.get(var)
        if raw and raw.strip():
            return raw.strip()
    return None


def make_openai_compatible_provider(
    *,
    prose_repo_path: Path | None = None,
    model: str | None = None,
) -> OpenAICompatibleProvider:
    """Construct the transport from settings, then env vars.

    Mirrors :func:`chronicler.narrative.anthropic.make_anthropic_provider`,
    including its fail-loud contract: a misconfigured chronicler refuses
    to start rather than failing on the first tracked death.

    Issue #45: resolution and validation moved to
    :func:`chronicler.narrative.backend_config.resolve_openai_config` so
    the settings file wins over the env (the repo-wide precedence) and so
    the four failure messages are worded once, shared with
    ``chronicler doctor``. An explicit ``model`` argument still overrides
    everything — that is the test/pinned-deployment seam.

    Imported inside the function because ``backend_config`` imports the
    presets and env names from this module; a module-level import would
    close the cycle.
    """
    from chronicler.narrative.backend_config import resolve_openai_config

    config, error = resolve_openai_config()
    if config is None:
        raise ValueError(error)
    return OpenAICompatibleProvider(
        model=(model or "").strip() or config.model,
        preset=config.preset_name,
        base_url=config.base_url,
        api_key=config.api_key,
        prose_repo_path=prose_repo_path,
    )
