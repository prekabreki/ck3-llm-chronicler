"""Issue #21: OpenAICompatibleProvider — chat-completions transport.

One transport for every vendor that speaks the OpenAI chat-completions
protocol (OpenAI, DeepSeek, OpenRouter, Ollama, LM Studio, vLLM): base
URL + optional key + model. Mirrors ``test_anthropic_provider.py`` — the
HTTP layer is mocked via ``httpx.MockTransport`` (respx was dropped in
b11eb51), no real endpoint is touched, and the prose-repo side effects
run against tmp_path with the git auto-commit monkeypatched out except
in the dedicated commit test.

v1 is non-streaming by deliberate choice (issue #21 constraints): one
POST, the wall-clock/read/connect timeouts do the work a stream's
per-chunk timeout would.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from chronicler.narrative import model_resolution as mr
from chronicler.narrative.openai_compatible import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    GENERIC_PRESET_ID,
    OPENAI_API_KEY_ENV,
    OPENAI_BASE_URL_ENV,
    OPENAI_MODEL_ENV,
    OPENAI_PRESET_ENV,
    PRESETS,
    OpenAICompatibleProvider,
    make_openai_compatible_provider,
)
from chronicler.narrative.prose_io import render_briefing_markdown
from chronicler.narrative.provider import NarrativeRequest

TEST_KEY = "sk-unmistakable-secret-9f3c1a"


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch):
    for var in mr.ALL_MODEL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clean_transport_env(monkeypatch):
    for var in (
        OPENAI_API_KEY_ENV,
        OPENAI_BASE_URL_ENV,
        OPENAI_MODEL_ENV,
        OPENAI_PRESET_ENV,
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    """Skip the real git auto-commit spawn. The dedicated commit test
    re-patches with its own recorder, which wins because it is applied
    after this autouse fixture."""

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr("chronicler.narrative.openai_compatible.git_commit_biography", _noop)


def _completion(
    *,
    text: str = "Harold was bold.",
    model: str = "gpt-5.6-luna",
    prompt_tokens: int = 1012,
    cached_tokens: int | None = 900,
    completion_tokens: int = 9,
    finish_reason: str = "stop",
) -> dict:
    usage: dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cached_tokens is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


def _response(payload: dict | None = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload if payload is not None else _completion())


def _request(
    user_prompt: str = "briefing body",
    *,
    system_prompt: str = "(assembled register + voice rules)",
) -> NarrativeRequest:
    return NarrativeRequest(
        kind="biography_woven",
        prompt_version="biography_v5",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        metadata={"campaign_uuid": "camp-1", "character_id": "42"},
    )


def _provider_with(handler, prose_repo: Path, **kw) -> OpenAICompatibleProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    kw.setdefault("model", "gpt-5.6-luna")
    kw.setdefault("preset", "openai")
    kw.setdefault("api_key", TEST_KEY)
    return OpenAICompatibleProvider(
        client=client,
        prose_repo_path=prose_repo,
        retry_base_delay=0.0,
        **kw,
    )


# --- construction ---


def test_model_is_required(tmp_path):
    """Unlike the Anthropic transport there is no sane default model —
    the endpoint decides what exists, so an empty model must fail loud at
    construction instead of 400ing at the first death."""
    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleProvider(model="", base_url="http://x/v1", prose_repo_path=tmp_path)


def test_base_url_or_preset_is_required(tmp_path):
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleProvider(model="m", prose_repo_path=tmp_path)


def test_preset_supplies_the_base_url(tmp_path):
    p = OpenAICompatibleProvider(
        model="deepseek-v4-pro", preset="deepseek", api_key="k", prose_repo_path=tmp_path
    )
    assert p.base_url == PRESETS["deepseek"].base_url


def test_explicit_base_url_overrides_the_preset(tmp_path):
    p = OpenAICompatibleProvider(
        model="m",
        preset="openai",
        api_key="k",
        base_url="http://localhost:8000/v1/",
        prose_repo_path=tmp_path,
    )
    # trailing slash normalised so URL joining can't double up
    assert p.base_url == "http://localhost:8000/v1"


def test_unknown_preset_raises(tmp_path):
    with pytest.raises(ValueError, match="preset"):
        OpenAICompatibleProvider(model="m", preset="not-a-vendor", prose_repo_path=tmp_path)


def test_paid_preset_requires_a_key(tmp_path):
    """A cloud vendor with no key 401s on the first generation; refuse at
    construction (the factory's fail-loud contract)."""
    with pytest.raises(ValueError, match="key"):
        OpenAICompatibleProvider(model="gpt-5.6-luna", preset="openai", prose_repo_path=tmp_path)


def test_local_preset_needs_no_key(tmp_path):
    p = OpenAICompatibleProvider(model="qwen3:14b", preset="ollama", prose_repo_path=tmp_path)
    assert p.name == "ollama:qwen3:14b"


def test_generic_transport_needs_no_key(tmp_path):
    """A bare base URL (vLLM, a proxy, anything self-hosted) is unpresetted
    and unauthenticated by default."""
    p = OpenAICompatibleProvider(
        model="my-model", base_url="http://localhost:8000/v1", prose_repo_path=tmp_path
    )
    assert p.name == f"{GENERIC_PRESET_ID}:my-model"


# --- provider tag: what cost attribution partitions on ---


def test_name_uses_the_preset_id_so_the_rate_card_matches(tmp_path):
    """cost.lookup_token_price partitions on the first ':' — the prefix
    must be the preset id, or an OpenAI generation is priced as an
    unknown provider."""
    from chronicler.cost import has_token_price

    p = OpenAICompatibleProvider(
        model="gpt-5.6-sol", preset="openai", api_key="k", prose_repo_path=tmp_path
    )
    assert p.name == "openai:gpt-5.6-sol"
    assert has_token_price(p.name)


def test_name_for_kind_is_the_configured_model_for_every_kind(tmp_path):
    """The shared per-kind model resolution is Anthropic-tag-shaped
    (claude-opus-4-7 defaults). This transport's model comes from its own
    config, so every kind reports the same one rather than inheriting a
    Claude tag that its endpoint has never heard of."""
    p = OpenAICompatibleProvider(
        model="gpt-5.6-luna", preset="openai", api_key="k", prose_repo_path=tmp_path
    )
    assert p.name_for_kind("biography") == "openai:gpt-5.6-luna"
    assert p.name_for_kind("chronicle_export") == "openai:gpt-5.6-luna"


def test_resolved_models_reports_the_configured_model(tmp_path):
    p = OpenAICompatibleProvider(
        model="gpt-5.6-luna", preset="openai", api_key="k", prose_repo_path=tmp_path
    )
    snap = p.resolved_models()
    assert snap["biography"] == "gpt-5.6-luna"
    assert snap["closing"] == "gpt-5.6-luna"
    assert snap["global_override"] == "gpt-5.6-luna"


def test_local_preset_defaults_to_one_concurrent_generation(tmp_path):
    """A single local GPU serialises anyway; 4 in flight just thrashes it
    (the Ollama era ran a semaphore of 1 for exactly this reason)."""
    local = OpenAICompatibleProvider(model="m", preset="ollama", prose_repo_path=tmp_path)
    cloud = OpenAICompatibleProvider(
        model="m", preset="openai", api_key="k", prose_repo_path=tmp_path
    )
    assert local.max_concurrent == 1
    assert cloud.max_concurrent == 4


def test_max_concurrent_override(tmp_path):
    p = OpenAICompatibleProvider(
        model="m", preset="ollama", max_concurrent=3, prose_repo_path=tmp_path
    )
    assert p.max_concurrent == 3


# --- env construction ---


def test_make_provider_requires_a_model(monkeypatch, tmp_path):
    monkeypatch.setenv(OPENAI_BASE_URL_ENV, "http://localhost:11434/v1")
    with pytest.raises(ValueError, match=OPENAI_MODEL_ENV):
        make_openai_compatible_provider(prose_repo_path=tmp_path)


def test_make_provider_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv(OPENAI_PRESET_ENV, "deepseek")
    monkeypatch.setenv(OPENAI_MODEL_ENV, "  deepseek-v4-pro  ")
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "  sk-test  ")
    p = make_openai_compatible_provider(prose_repo_path=tmp_path)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "deepseek:deepseek-v4-pro"
    assert p.base_url == PRESETS["deepseek"].base_url


def test_make_provider_falls_back_to_the_vendor_key_env(monkeypatch, tmp_path):
    """Users arrive with OPENAI_API_KEY already exported; honour it rather
    than making them re-export under a chronicler name."""
    monkeypatch.setenv(OPENAI_PRESET_ENV, "openai")
    monkeypatch.setenv(OPENAI_MODEL_ENV, "gpt-5.6-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-vendor")
    p = make_openai_compatible_provider(prose_repo_path=tmp_path)
    assert p.name == "openai:gpt-5.6-luna"


def test_chronicler_key_env_wins_over_the_vendor_one(monkeypatch, tmp_path):
    monkeypatch.setenv(OPENAI_PRESET_ENV, "openai")
    monkeypatch.setenv(OPENAI_MODEL_ENV, "gpt-5.6-luna")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-vendor")
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "sk-chronicler")
    p = make_openai_compatible_provider(prose_repo_path=tmp_path)
    assert p._api_key == "sk-chronicler"


# --- generate: request shape ---


@pytest.mark.asyncio
async def test_generate_posts_chat_completions_and_maps_usage(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return _response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())

    assert resp.text == "Harold was bold."
    assert resp.model == "gpt-5.6-luna"
    # OpenAI's prompt_tokens ALREADY includes cached input, so it maps
    # straight onto the summed-total semantics of input_tokens.
    assert resp.input_tokens == 1012
    assert resp.cache_read_tokens == 900
    # No OpenAI-compatible server reports cache WRITES separately.
    assert resp.cache_write_tokens is None
    assert resp.output_tokens == 9
    assert resp.latency_ms >= 0

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == f"Bearer {TEST_KEY}"
    assert captured["body"]["model"] == "gpt-5.6-luna"
    assert captured["body"]["stream"] is False


@pytest.mark.asyncio
async def test_system_prompt_becomes_the_system_message(tmp_path):
    """Issue #19's contract: the assembled register arrives on the request
    and the transport ships it verbatim. It must not read prose-repo
    files itself — that is how the two existing transports drifted."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _response()

    p = _provider_with(handler, tmp_path)
    await p.generate(_request(system_prompt="(the register)\n\n(woven voice rules)"))

    system, user = captured["body"]["messages"]
    assert system == {"role": "system", "content": "(the register)\n\n(woven voice rules)"}
    assert user["role"] == "user"
    assert "briefing body" in user["content"]


@pytest.mark.asyncio
async def test_user_turn_is_byte_identical_across_all_three_transports(tmp_path, monkeypatch):
    """The output contract must not drift between backends (issue #21).

    Compares what this transport actually sends against what the other
    two actually send, rather than against the shared constant — the
    constant only proves nobody rebuilt the string locally, while this
    catches a transport that assembles the turn differently (extra
    framing, briefing rendered another way, wrapper reordered). Prose
    written on one backend has to be comparable with prose written on
    another; a per-backend preamble is exactly the drift that produced
    the register bug issue #19 fixed.
    """
    from chronicler.narrative.anthropic import AnthropicProvider

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr("chronicler.narrative.anthropic.git_commit_biography", _noop)

    req = _request()
    captured: dict = {}

    def oai_handler(request: httpx.Request) -> httpx.Response:
        captured["oai"] = json.loads(request.content)
        return _response()

    await _provider_with(oai_handler, tmp_path).generate(req)
    oai_user = captured["oai"]["messages"][1]["content"]

    def anthropic_handler(request: httpx.Request) -> httpx.Response:
        captured["anthropic"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=(
                b'data: {"type":"message_start","message":{"model":"m","usage":{}}}\n\n'
                b'data: {"type":"content_block_delta","delta":'
                b'{"type":"text_delta","text":"x"}}\n\n'
                b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                b'"usage":{"output_tokens":1}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    anthropic = AnthropicProvider(
        api_key="k",
        client=httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler)),
        prose_repo_path=tmp_path,
        retry_base_delay=0.0,
    )
    await anthropic.generate(req)
    anthropic_user = captured["anthropic"]["messages"][0]["content"]

    assert oai_user == anthropic_user

    # claude-code assembles the same turn as stdin bytes.
    from chronicler.narrative.claude_code import ClaudeCodeProvider

    cc = ClaudeCodeProvider(prose_repo_path=tmp_path)
    _, stdin_bytes = cc._build_argv_and_stdin(
        model="m",
        briefing_text=render_briefing_markdown(req),
        system_prompt_file=tmp_path / "sys.md",
    )
    assert stdin_bytes.decode("utf-8") == oai_user


@pytest.mark.asyncio
async def test_openai_preset_uses_max_completion_tokens(tmp_path):
    """OpenAI's reasoning-model chat-completions endpoint rejects the
    legacy ``max_tokens`` field; every other vendor still wants it. Wrong
    field name here means every OpenAI generation 400s."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _response()

    p = _provider_with(handler, tmp_path, preset="openai")
    await p.generate(_request())
    assert captured["body"]["max_completion_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert "max_tokens" not in captured["body"]


@pytest.mark.asyncio
async def test_other_vendors_use_legacy_max_tokens(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _response()

    p = _provider_with(handler, tmp_path, preset="ollama", api_key=None, model="qwen3:14b")
    await p.generate(_request())
    assert captured["body"]["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert "max_completion_tokens" not in captured["body"]


@pytest.mark.asyncio
async def test_max_output_tokens_override(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _response()

    p = _provider_with(handler, tmp_path, max_output_tokens=1234)
    await p.generate(_request())
    assert captured["body"]["max_completion_tokens"] == 1234


@pytest.mark.asyncio
async def test_no_authorization_header_without_a_key(tmp_path):
    """Ollama rejects nothing, but sending "Bearer None" to a local
    server that does check is a confusing 401."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return _response()

    p = _provider_with(handler, tmp_path, preset="ollama", api_key=None, model="qwen3:14b")
    await p.generate(_request())
    assert "authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_deepseek_cache_hit_tokens_are_read_as_cache_reads(tmp_path):
    """DeepSeek reports prompt_cache_hit_tokens instead of OpenAI's
    prompt_tokens_details.cached_tokens. Missing this silently reports
    every DeepSeek generation as fully uncached."""
    payload = _completion(cached_tokens=None, model="deepseek-v4-pro")
    payload["usage"]["prompt_cache_hit_tokens"] = 700
    payload["usage"]["prompt_cache_miss_tokens"] = 312

    p = _provider_with(
        lambda request: _response(payload),
        tmp_path,
        preset="deepseek",
        model="deepseek-v4-pro",
    )
    resp = await p.generate(_request())
    assert resp.cache_read_tokens == 700
    assert resp.input_tokens == 1012


# --- cost: the None-vs-0.0 rule ---


@pytest.mark.asyncio
async def test_priced_model_computes_cost_from_the_rate_card(tmp_path):
    from chronicler.cost import compute_generation_cost

    p = _provider_with(lambda request: _response(), tmp_path)
    resp = await p.generate(_request())
    expected = compute_generation_cost(
        "openai:gpt-5.6-luna",
        input_tokens=1012,
        cache_read_tokens=900,
        cache_write_tokens=None,
        output_tokens=9,
    )
    assert resp.cost_usd == pytest.approx(expected)
    assert resp.cost_usd > 0


@pytest.mark.asyncio
async def test_local_model_persists_a_real_zero_not_none(tmp_path):
    """A local generation genuinely costs nothing. The cost aggregate
    treats NULL as "uncosted, price it from the card" and 0.0 as costed —
    so a local row must persist 0.0, or the UI prices free GPU time."""
    p = _provider_with(
        lambda request: _response(_completion(model="qwen3:14b")),
        tmp_path,
        preset="ollama",
        api_key=None,
        model="qwen3:14b",
    )
    resp = await p.generate(_request())
    assert resp.cost_usd == 0.0


@pytest.mark.asyncio
async def test_unpriced_paid_model_reports_none(tmp_path):
    """An OpenAI model the rate card has never heard of is UNKNOWN, not
    free: cost_usd=None so the row shows as uncosted."""
    p = _provider_with(
        lambda request: _response(_completion(model="gpt-9-unreleased")),
        tmp_path,
        model="gpt-9-unreleased",
    )
    resp = await p.generate(_request())
    assert resp.cost_usd is None


# --- generate: guards ---


@pytest.mark.asyncio
async def test_generate_refuses_an_empty_system_prompt(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the endpoint was called without a system prompt")

    p = _provider_with(handler, tmp_path)
    with pytest.raises(RuntimeError, match="system prompt"):
        await p.generate(_request(system_prompt=""))


@pytest.mark.asyncio
async def test_generate_fails_loud_when_the_prose_dir_is_missing(tmp_path):
    absent = tmp_path / "absent"

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the endpoint was called with no prose dir")

    p = _provider_with(handler, absent)
    with pytest.raises(RuntimeError, match="chronicler init-prose"):
        await p.generate(_request())

    assert not absent.exists()


@pytest.mark.asyncio
async def test_length_truncation_raises_runtime_error(tmp_path):
    """The audit M-N5 class of bug, ported: a length-truncated completion
    carries non-empty text, so without this guard it passes the non-empty
    check, gets a footer, gets git-committed, and lands in the DB as a
    finished biography — a mid-sentence cut recorded as complete."""
    payload = _completion(text="A long biography cut off mid-", finish_reason="length")

    p = _provider_with(lambda request: _response(payload), tmp_path)
    with pytest.raises(RuntimeError, match="truncated"):
        await p.generate(_request())

    # and nothing was persisted as a finished biography
    assert not (tmp_path / "biographies" / "camp-1" / "42-v1.md").exists()


@pytest.mark.asyncio
async def test_empty_prose_raises_runtime_error(tmp_path):
    p = _provider_with(lambda request: _response(_completion(text="   ")), tmp_path)
    with pytest.raises(RuntimeError, match="no prose"):
        await p.generate(_request())


@pytest.mark.asyncio
async def test_missing_choices_raises_runtime_error(tmp_path):
    """A proxy that returns 200 with an error body must not crash with a
    bare IndexError three layers down."""
    p = _provider_with(lambda request: _response({"error": {"message": "no capacity"}}), tmp_path)
    with pytest.raises(RuntimeError, match="no prose|malformed"):
        await p.generate(_request())


# --- generate: retries, cancellation, timeouts ---


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return _response({"error": "rate limited"}, status=429)
        return _response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())
    assert calls["n"] == 3
    assert resp.text == "Harold was bold."


@pytest.mark.asyncio
async def test_retries_on_503_then_succeeds(tmp_path):
    """A cold local server (Ollama pulling a model into VRAM) 503s before
    it is ready."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _response({"error": "loading"}, status=503)
        return _response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())
    assert calls["n"] == 2
    assert resp.text == "Harold was bold."


@pytest.mark.asyncio
async def test_retries_exhausted_raises(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _response({"error": "rate limited"}, status=429)

    p = _provider_with(handler, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate(_request())
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_transport_error_retried(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return _response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())
    assert calls["n"] == 2
    assert resp.text == "Harold was bold."


@pytest.mark.asyncio
async def test_400_is_not_retried(tmp_path):
    """A malformed-request 400 is deterministic — retrying burns the
    death event's time budget for nothing."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _response({"error": {"message": "unsupported parameter"}}, status=400)

    p = _provider_with(handler, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate(_request())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_401_is_not_retried(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _response({"error": {"message": "invalid api key"}}, status=401)

    p = _provider_with(handler, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate(_request())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_403_is_not_retried(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _response({"error": {"message": "forbidden"}}, status=403)

    p = _provider_with(handler, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate(_request())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_and_stops_retrying(tmp_path):
    """Queue cancellation must abort the generation, not get swallowed by
    the retry loop and turned into another attempt. CancelledError is a
    BaseException, so a broad ``except Exception`` in the retry ladder is
    the bug this guards."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise asyncio.CancelledError()

    p = _provider_with(handler, tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await p.generate(_request())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cancelling_the_awaiting_task_is_not_converted_to_a_timeout(tmp_path):
    """The wall-clock guard wraps the call in asyncio.wait_for; an outer
    cancel must surface as CancelledError, not as "timed out"."""
    started = asyncio.Event()

    p = _provider_with(lambda request: _response(), tmp_path)

    async def slow_post(body, headers):
        started.set()
        await asyncio.sleep(30)

    p._post_once = slow_post  # type: ignore[method-assign]

    task = asyncio.create_task(p.generate(_request()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_wall_clock_timeout_raises_runtime_error(tmp_path):
    p = _provider_with(lambda request: _response(), tmp_path)

    async def slow_post(body, headers):
        await asyncio.sleep(30)

    p._post_once = slow_post  # type: ignore[method-assign]
    p._timeout = 0.05
    with pytest.raises(RuntimeError, match="timed out"):
        await p.generate(_request())


# --- the key must never leak ---


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs(tmp_path, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return _response()

    p = _provider_with(handler, tmp_path)
    with caplog.at_level(logging.DEBUG):
        await p.generate(_request())
    assert TEST_KEY not in caplog.text


@pytest.mark.asyncio
async def test_api_key_never_appears_in_the_auth_error(tmp_path, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return _response({"error": {"message": "invalid api key"}}, status=401)

    p = _provider_with(handler, tmp_path)
    with caplog.at_level(logging.DEBUG), pytest.raises(httpx.HTTPStatusError) as excinfo:
        await p.generate(_request())
    assert TEST_KEY not in str(excinfo.value)
    assert TEST_KEY not in caplog.text
    # the message should still be actionable about WHERE the key comes from
    assert OPENAI_API_KEY_ENV in caplog.text


@pytest.mark.asyncio
async def test_api_key_never_reaches_the_briefing_or_biography(tmp_path):
    p = _provider_with(lambda request: _response(), tmp_path)
    await p.generate(_request())
    for path in tmp_path.rglob("*.md"):
        assert TEST_KEY not in path.read_text(encoding="utf-8"), path


# --- prose-repo side effects ---


@pytest.mark.asyncio
async def test_writes_prose_repo_artifacts_and_footer(tmp_path):
    p = _provider_with(lambda request: _response(), tmp_path)
    resp = await p.generate(_request())
    brief = tmp_path / "briefings" / "camp-1" / "42-v1.md"
    bio = tmp_path / "biographies" / "camp-1" / "42-v1.md"
    assert brief.is_file()
    assert bio.is_file()
    on_disk = bio.read_text(encoding="utf-8")
    assert on_disk.startswith("Harold was bold.")
    assert "gpt-5.6-luna" in on_disk
    # the response body stays bare prose — no footer double-stored in DB
    assert resp.text == "Harold was bold."
    assert "# Briefing: character 42" in brief.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_footer_names_the_endpoint_that_wrote_it(tmp_path):
    """Attribution has to distinguish an OpenAI generation from a local
    one months later, when the only record is the file on disk."""
    p = _provider_with(lambda request: _response(), tmp_path)
    await p.generate(_request())
    on_disk = (tmp_path / "biographies" / "camp-1" / "42-v1.md").read_text(encoding="utf-8")
    assert "openai" in on_disk.lower()


@pytest.mark.asyncio
async def test_versions_increment_across_calls(tmp_path):
    p = _provider_with(lambda request: _response(), tmp_path)
    await p.generate(_request())
    await p.generate(_request())
    assert (tmp_path / "biographies" / "camp-1" / "42-v2.md").is_file()


@pytest.mark.asyncio
async def test_git_commit_invoked_with_rel_paths(tmp_path, monkeypatch):
    recorded: dict = {}

    async def recorder(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("chronicler.narrative.openai_compatible.git_commit_biography", recorder)
    p = _provider_with(lambda request: _response(), tmp_path)
    await p.generate(_request())
    assert recorded["rel_brief"] == "briefings/camp-1/42-v1.md"
    assert recorded["rel_bio"] == "biographies/camp-1/42-v1.md"
    assert recorded["version"] == 1
    assert recorded["model"] == "gpt-5.6-luna"


# --- lifecycle ---


@pytest.mark.asyncio
async def test_aclose_owned_vs_injected(tmp_path):
    injected = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _response()))
    p = OpenAICompatibleProvider(
        model="m",
        base_url="http://x/v1",
        client=injected,
        prose_repo_path=tmp_path,
    )
    await p.aclose()
    assert not injected.is_closed  # injected client survives

    owned = OpenAICompatibleProvider(model="m", base_url="http://x/v1", prose_repo_path=tmp_path)
    inner = owned._client
    await owned.aclose()
    assert inner.is_closed


@pytest.mark.asyncio
async def test_aclose_is_idempotent(tmp_path):
    p = OpenAICompatibleProvider(model="m", base_url="http://x/v1", prose_repo_path=tmp_path)
    await p.aclose()
    await p.aclose()
    assert p._client.is_closed
