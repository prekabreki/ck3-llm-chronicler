"""AnthropicProvider — direct Messages API transport (ck3_chronicler-cs1o).

Revived from the zcf-era provider (deleted in a908f9c, tbrm.3) after
Anthropic's 2026-06-15 programmatic-credit split moved ``claude
--print`` onto a metered monthly pool: this transport is the fallback
for pool exhaustion, selected via ``CHRONICLER_NARRATIVE_BACKEND=
anthropic`` and billed against the user's ``ANTHROPIC_API_KEY``.

Same deliberate choice of bare httpx over the official anthropic SDK as
the original (the chronicler's HTTP layer is bare httpx throughout).
Deltas from the zcf provider, all driven by real usage data (27 bios,
avg 9.5k output tokens, p95 19.7k, 2-3.5 min generations):

- **Streaming always.** The old ``max_tokens=4096`` would truncate
  every modern bio and the old 120s timeout would kill every call.
  Streaming sidesteps the non-streaming long-request limits and lets
  the read timeout apply between chunks instead of across the call.
- **Prompt caching.** ``cache_control`` on the static system prefix —
  since issue #19 that is ``req.system_prompt`` verbatim (the shared
  assembly's register + per-kind voice rules) rather than files this
  transport reads itself. Static per kind, so the ma96 slice-3 prefix
  stability holds; observed cache_read_ratio ~0.73.
- **Bounded retries.** 3 attempts with backoff on transport errors,
  429/529/5xx. A dropped call at a death event is a lost generation.
- **Conditional 1M-context beta** above the shared 180k estimate
  threshold (2x input price applies above 200k — engage only when the
  prompt actually needs the window).
- **Prose-repo parity** via :mod:`chronicler.narrative.prose_io`:
  briefing + biography files, attribution footer, git auto-commit —
  identical on-disk record regardless of transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from chronicler.narrative.model_resolution import (
    LongContextPolicy,
    estimate_prompt_tokens,
    model_for_kind,
    needs_long_context,
)
from chronicler.narrative.model_resolution import (
    resolved_models as _resolved_models_snapshot,
)
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

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
# Real outputs: avg 9.5k tokens, p95 19.7k, max observed 22k. 32k gives
# ~45% headroom over the worst real call without inviting runaways.
DEFAULT_ANTHROPIC_MAX_TOKENS = 32_000
# Wall-clock ceiling for one generation INCLUDING retries — parity with
# ClaudeCodeProvider's 600s. The per-chunk read timeout below is what
# catches a stalled stream early.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0
_READ_TIMEOUT_SECONDS = 120.0
_CONNECT_TIMEOUT_SECONDS = 15.0

ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_VERSION = "2023-06-01"
# The 1M-context beta gate. Header name is the Sonnet-4-era beta tag;
# if a given model rejects it the API error surfaces verbatim in the
# queue UI (and the claude-code transport, the default, is unaffected).
CONTEXT_1M_BETA = "context-1m-2025-08-07"

_RETRY_ATTEMPTS = 3
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})


class AnthropicProvider(NarrativeProvider):
    """Streaming Messages API client implementing :class:`NarrativeProvider`.

    Constructed once per chronicler process. ``generate`` is safe to
    call concurrently — each call materialises its own briefing file
    with a unique version number; the scheduler serialises per
    character via its own locks.
    """

    def __init__(
        self,
        *,
        api_key: str,
        prose_repo_path: Path | None = None,
        model: str | None = None,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        max_tokens: int = DEFAULT_ANTHROPIC_MAX_TOKENS,
        client: httpx.AsyncClient | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        retry_base_delay: float = 2.0,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicProvider requires a non-empty api_key")
        self._api_key = api_key
        self._prose_repo = prose_repo_path or get_prose_repo_path()
        # Explicit "use this for ALL kinds" override, mirroring
        # ClaudeCodeProvider's model kwarg. When None, each generate()
        # resolves per-kind via the shared model_resolution module.
        self._model_override = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
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

    def _resolve_model(self, kind: str) -> str:
        if self._model_override:
            return self._model_override
        return model_for_kind(kind)

    @property
    def name(self) -> str:
        # Biography-anchored tag, same convention as ClaudeCodeProvider.
        return f"anthropic:{self._resolve_model('biography')}"

    def name_for_kind(self, kind: PromptKind) -> str:
        return f"anthropic:{self._resolve_model(kind)}"

    def resolved_models(self) -> dict[str, str | None]:
        """Per-kind resolved-model snapshot (settings endpoint surface).

        Honours the constructor's ``model`` kwarg; otherwise delegates
        to the shared module-level snapshot.
        """
        if self._model_override:
            return {
                "biography": self._model_override,
                "closing": self._model_override,
                "global_override": self._model_override,
            }
        return _resolved_models_snapshot()

    @property
    def prose_repo_path(self) -> Path:
        return self._prose_repo

    @property
    def long_context_policy(self) -> LongContextPolicy:
        """Issue #46: this transport asks for the 1M window with the
        Anthropic beta header. Never the ``[1m]`` model-tag bracket — that
        is claude-code's spelling and the Messages API would read it as
        part of the model id."""
        return LongContextPolicy.BETA_HEADER

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        # Issue #19: fail loud before touching the filesystem. Without
        # this check the mkdirs below created briefings/ + biographies/
        # under a nonexistent prose root, and the old
        # _build_system_blocks fell back to an empty system block — so a
        # misconfigured install silently persisted generic-assistant
        # prose as a valid biography. Parity with ClaudeCodeProvider.
        if not self._prose_repo.is_dir():
            raise RuntimeError(
                _missing_prose_message(f"prose repo not found at {self._prose_repo}")
            )
        if not req.system_prompt.strip():
            raise RuntimeError(
                "refusing to call the Messages API with an empty system prompt: "
                "the chronicler register must be assembled from the prose dir "
                "before the request reaches a transport (see "
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

        model = self._resolve_model(req.kind)
        # Issue #19: one block carrying the assembly's output verbatim.
        # It is static per kind, so the cache_control breakpoint still
        # makes the whole prefix one prompt-cache entry (ma96 slice-3
        # locality preserved).
        system_blocks = [
            {
                "type": "text",
                "text": req.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        user_content = BRIEFING_WRAPPER_INSTRUCTION + briefing_text
        body = {
            "model": model,
            "max_tokens": self._max_tokens,
            "stream": True,
            "system": system_blocks,
            "messages": [{"role": "user", "content": user_content}],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        est_tokens = estimate_prompt_tokens(
            "".join(b["text"] for b in system_blocks) + user_content
        )
        if self.long_context_policy is LongContextPolicy.BETA_HEADER and needs_long_context(
            est_tokens
        ):
            headers["anthropic-beta"] = CONTEXT_1M_BETA
            log.info(
                "anthropic: 1M-context beta engaged (est=%d tokens) for "
                "kind=%s character_id=%s — premium input pricing applies "
                "above 200k",
                est_tokens,
                req.kind,
                character_id,
            )

        log.info(
            "anthropic: invoking %s for character_id=%s campaign=%s "
            "version=%d kind=%s (est=%d prompt tokens)",
            model,
            character_id,
            campaign_uuid,
            version,
            req.kind,
            est_tokens,
        )

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._generate_with_retries(body, headers),
                timeout=self._timeout,
            )
        except TimeoutError:
            raise RuntimeError(
                f"anthropic generate timed out after {self._timeout}s for "
                f"character_id={character_id} campaign={campaign_uuid}"
            ) from None
        latency_ms = int((time.monotonic() - start) * 1000)

        text = result["text"]
        if not text:
            raise RuntimeError(
                "anthropic stream completed but returned no prose for "
                f"character_id={character_id} campaign={campaign_uuid}"
            )
        usage = result["usage"]
        model_actual = result["model"]

        in_marginal = usage.get("input_tokens")
        cache_write = usage.get("cache_creation_input_tokens")
        cache_read = usage.get("cache_read_input_tokens")
        out_tokens = usage.get("output_tokens")
        # input_tokens carries the SUMMED total — same semantics as
        # claude-code rows so the token meter aggregates uniformly. The
        # breakdown fields let the cost layer bill each bucket at its
        # real rate.
        total_in = sum(v for v in (in_marginal, cache_write, cache_read) if isinstance(v, int))
        input_tokens = total_in if total_in > 0 else None
        cache_write_tokens = (
            cache_write if isinstance(cache_write, int) and cache_write > 0 else None
        )
        cache_read_tokens = cache_read if isinstance(cache_read, int) and cache_read > 0 else None
        output_tokens = out_tokens if isinstance(out_tokens, int) else None

        cacheable = (cache_write or 0) + (cache_read or 0)
        cache_read_ratio = ((cache_read or 0) / cacheable) if cacheable > 0 else 0.0
        log.info(
            "anthropic: cache_hit kind=%s actual=%s cache_read=%d "
            "cache_create=%d input_marginal=%d cache_read_ratio=%.3f",
            req.kind,
            model_actual,
            cache_read or 0,
            cache_write or 0,
            in_marginal or 0,
            cache_read_ratio,
        )

        cost_usd = self._compute_cost_usd(
            kind=req.kind,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
        )

        # Prose-repo parity: bio file + attribution footer + git commit.
        bio_path.write_text(text + "\n", encoding="utf-8")
        footer = format_attribution_footer(
            model=model_actual,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            agent="Anthropic API",
        )
        try:
            with bio_path.open("a", encoding="utf-8") as fh:
                fh.write(footer)
        except OSError:
            log.exception(
                "anthropic: failed to append attribution footer to %s; "
                "biography body is unaffected",
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
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd,
        )

    def _compute_cost_usd(
        self,
        *,
        kind: str,
        input_tokens: int | None,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        output_tokens: int | None,
    ) -> float | None:
        """Local USD figure for the row (the raw API reports no cost).

        Delegates to the cost rate card; returns None when pricing is
        unavailable so the row records "unknown" rather than a fake 0.
        """
        try:
            from chronicler.cost import compute_generation_cost
        except ImportError:  # pricing layer not present (cs1o Task 8 gate)
            return None
        usd = compute_generation_cost(
            self.name_for_kind(kind),
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
        )
        return usd if usd > 0 else None

    async def _generate_with_retries(self, body: dict, headers: dict) -> dict:
        """Retry wrapper: transport errors + retryable statuses get
        ``_RETRY_ATTEMPTS`` tries with exponential backoff; auth/client
        errors raise immediately with an actionable message."""
        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await self._stream_once(body, headers)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    log.error(
                        "anthropic: authentication failed (%d) — check the %s env var",
                        status,
                        ANTHROPIC_API_KEY_ENV,
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
                    "anthropic: attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt + 1,
                    _RETRY_ATTEMPTS,
                    type(last_exc).__name__,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _stream_once(self, body: dict, headers: dict) -> dict:
        """One streaming POST to /v1/messages.

        Returns ``{"text", "model", "usage"}`` accumulated from the SSE
        events: ``message_start`` carries the model + input/cache usage,
        ``content_block_delta`` the text, ``message_delta`` the final
        output_tokens. Raises :class:`httpx.HTTPStatusError` on a
        non-200 status (body read first so the error carries it).
        """
        text_parts: list[str] = []
        usage: dict = {}
        stop_reason: str | None = None
        model = str(body["model"])
        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            json=body,
            headers=headers,
        ) as response:
            if response.status_code != 200:
                await response.aread()
                response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # malformed keep-alive / partial line
                ptype = payload.get("type")
                if ptype == "message_start":
                    msg = payload.get("message") or {}
                    usage.update(msg.get("usage") or {})
                    model = str(msg.get("model") or model)
                elif ptype == "content_block_delta":
                    delta = payload.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text_parts.append(delta.get("text", ""))
                elif ptype == "message_delta":
                    delta = payload.get("delta") or {}
                    if delta.get("stop_reason") is not None:
                        stop_reason = str(delta.get("stop_reason"))
                    usage.update(payload.get("usage") or {})
                elif ptype == "error":
                    # In-stream error event (overloaded mid-stream etc.)
                    err = payload.get("error") or {}
                    raise RuntimeError(
                        f"anthropic stream error: {err.get('type')}: {err.get('message')}"
                    )
        # ck3_chronicler-27ov.23 (audit M-N5): a max_tokens-truncated biography
        # is worse than a failed one — non-empty text passes the downstream
        # non-empty check, gets a footer, is git-committed, and lands in the DB
        # as finished. Raise so the pipeline's error net records a failed
        # generation instead of permanently recording a mid-sentence cut-off.
        if stop_reason == "max_tokens":
            raise RuntimeError(
                "anthropic: response truncated at max_tokens "
                f"(model={model}, chars={len(''.join(text_parts))})"
            )
        if stop_reason not in (None, "end_turn", "stop_sequence"):
            log.warning("anthropic: unexpected stop_reason %r", stop_reason)
        return {"text": "".join(text_parts).strip(), "model": model, "usage": usage}

    async def aclose(self) -> None:
        """Close the HTTP client iff this provider owns it (F-54
        ownership pattern: injected clients survive, owned clients are
        closed exactly once; idempotent)."""
        if self._owned_client:
            await self._client.aclose()


def get_anthropic_api_key() -> str | None:
    """The Anthropic API key: settings first, then the environment.

    Issue #45 put the durable setting in front of the env var, matching
    the repo-wide precedence (``config._resolve``): a key entered in the
    Settings UI must not be shadowed by a stale ``export`` in whatever
    shell happened to launch the app. Imported inside the function to
    avoid a module cycle (``backend_config`` imports this module's env
    name).
    """
    from chronicler.narrative.backend_config import resolve_anthropic_api_key

    return resolve_anthropic_api_key()


def make_anthropic_provider(
    *,
    api_key: str | None = None,
    prose_repo_path: Path | None = None,
    model: str | None = None,
) -> AnthropicProvider:
    """Construct AnthropicProvider from env vars. Pass ``api_key`` to
    override the env-derived value (tests). Raises :class:`ValueError`
    when no key resolves — the factory's fail-loud contract: a
    misconfigured chronicler refuses to start rather than failing on
    the first death."""
    key = api_key or get_anthropic_api_key()
    if not key:
        raise ValueError(
            f"{ANTHROPIC_API_KEY_ENV} env var not set; cannot construct "
            "AnthropicProvider (CHRONICLER_NARRATIVE_BACKEND=anthropic "
            "requires it)"
        )
    return AnthropicProvider(api_key=key, prose_repo_path=prose_repo_path, model=model)
