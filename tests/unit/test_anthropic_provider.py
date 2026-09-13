"""ck3_chronicler-cs1o: AnthropicProvider — streaming Messages API transport.

Revived (and substantially modernised) from the zcf-era provider deleted
in a908f9c. The HTTP layer is mocked via ``httpx.MockTransport`` (respx
was dropped in b11eb51); no real API is touched. The prose-repo side
effects run against tmp_path with the git auto-commit monkeypatched out
except in the dedicated commit test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from chronicler.narrative import model_resolution as mr
from chronicler.narrative.anthropic import (
    ANTHROPIC_API_KEY_ENV,
    CONTEXT_1M_BETA,
    DEFAULT_ANTHROPIC_MAX_TOKENS,
    AnthropicProvider,
    make_anthropic_provider,
)
from chronicler.narrative.provider import NarrativeRequest


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch):
    for var in mr.ALL_MODEL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    """Skip the real git auto-commit spawn in unit tests. The dedicated
    commit test re-patches with its own recorder, which wins because it
    is applied after this autouse fixture."""

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr("chronicler.narrative.anthropic.git_commit_biography", _noop)


def _sse(events: list[tuple[str, dict]]) -> bytes:
    out = []
    for name, payload in events:
        out.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n")
    return "".join(out).encode()


def _happy_stream(
    *,
    text_parts: tuple[str, ...] = ("Harold ", "was bold."),
    model: str = "claude-opus-4-7",
    input_tokens: int = 12,
    cache_creation: int = 100,
    cache_read: int = 900,
    output_tokens: int = 9,
    stop_reason: str = "end_turn",
) -> bytes:
    events: list[tuple[str, dict]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "model": model,
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": cache_creation,
                        "cache_read_input_tokens": cache_read,
                        "output_tokens": 1,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    for part in text_parts:
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": part},
                },
            )
        )
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return _sse(events)


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


def _provider_with(handler, prose_repo: Path, **kw) -> AnthropicProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return AnthropicProvider(
        api_key="test-key",
        client=client,
        prose_repo_path=prose_repo,
        retry_base_delay=0.0,
        **kw,
    )


def _stream_response(content: bytes = b"") -> httpx.Response:
    return httpx.Response(
        200,
        content=content or _happy_stream(),
        headers={"content-type": "text/event-stream"},
    )


# --- construction ---


def test_empty_key_raises():
    with pytest.raises(ValueError):
        AnthropicProvider(api_key="")


def test_make_provider_requires_env(monkeypatch, tmp_path):
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match=ANTHROPIC_API_KEY_ENV):
        make_anthropic_provider(prose_repo_path=tmp_path)


def test_make_provider_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "  sk-test  ")
    p = make_anthropic_provider(prose_repo_path=tmp_path)
    assert isinstance(p, AnthropicProvider)


def test_name_for_kind_uses_shared_resolution(monkeypatch, tmp_path):
    p = AnthropicProvider(api_key="k", prose_repo_path=tmp_path)
    assert p.name == "anthropic:claude-opus-4-7"
    assert p.name_for_kind("chronicle_export") == "anthropic:claude-opus-4-7"
    monkeypatch.setenv("CHRONICLER_NARRATIVE_CLOSING_MODEL", "claude-sonnet-4-6")
    assert p.name_for_kind("chronicle_export") == "anthropic:claude-sonnet-4-6"


def test_constructor_model_override_shadows_all_kinds(tmp_path):
    p = AnthropicProvider(api_key="k", prose_repo_path=tmp_path, model="claude-haiku-4-5")
    assert p.name == "anthropic:claude-haiku-4-5"
    assert p.name_for_kind("chronicle_export") == "anthropic:claude-haiku-4-5"


def test_resolved_models_snapshot(tmp_path):
    p = AnthropicProvider(api_key="k", prose_repo_path=tmp_path)
    snap = p.resolved_models()
    assert snap["biography"] == "claude-opus-4-7"
    assert snap["closing"] == "claude-opus-4-7"


# --- generate: request shape ---


@pytest.mark.asyncio
async def test_generate_accumulates_stream_and_usage(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return _stream_response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())
    assert resp.text == "Harold was bold."
    # input_tokens carries the SUMMED total (claude-code row parity).
    assert resp.input_tokens == 12 + 100 + 900
    assert resp.cache_write_tokens == 100
    assert resp.cache_read_tokens == 900
    assert resp.output_tokens == 9
    assert resp.model == "claude-opus-4-7"
    assert captured["url"].endswith("/v1/messages")
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "claude-opus-4-7"
    assert captured["body"]["max_tokens"] == DEFAULT_ANTHROPIC_MAX_TOKENS
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "anthropic-beta" not in captured["headers"]
    # single user turn carrying the wrapper + briefing
    (msg,) = captured["body"]["messages"]
    assert msg["role"] == "user"
    assert "briefing body" in msg["content"]


@pytest.mark.asyncio
async def test_generate_computes_local_cost_usd(tmp_path):
    """The raw API reports no cost figure; the provider computes one
    from the cost rate card so anthropic rows meter like claude-code
    rows (which carry the envelope's total_cost_usd)."""
    from chronicler.cost import compute_generation_cost

    p = _provider_with(lambda request: _stream_response(), tmp_path)
    resp = await p.generate(_request())
    expected = compute_generation_cost(
        "anthropic:claude-opus-4-7",
        input_tokens=1012,
        cache_read_tokens=900,
        cache_write_tokens=100,
        output_tokens=9,
    )
    assert resp.cost_usd == pytest.approx(expected)
    assert resp.cost_usd > 0


@pytest.mark.asyncio
async def test_system_block_carries_the_requests_system_prompt_with_cache_control(tmp_path):
    """Issue #19: the transport ships ``req.system_prompt`` verbatim in
    one cacheable system block. It used to read CLAUDE.md + the voice
    file off disk itself, which is how the two transports drifted."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _stream_response()

    p = _provider_with(handler, tmp_path)
    await p.generate(_request(system_prompt="(the register)\n\n(woven voice rules)"))
    system = captured["body"]["system"]
    assert [b["text"] for b in system] == ["(the register)\n\n(woven voice rules)"]
    # cache breakpoint on the static prefix — one cache entry per kind.
    assert system[-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_generate_refuses_an_empty_system_prompt(tmp_path):
    """The silent-generic-prose defect: this provider used to fall back
    to an empty system block and persist register-less output as a valid
    biography. It must refuse instead."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the API was called without a system prompt")

    p = _provider_with(handler, tmp_path)
    with pytest.raises(RuntimeError, match="system prompt"):
        await p.generate(_request(system_prompt=""))


@pytest.mark.asyncio
async def test_generate_fails_loud_when_the_prose_dir_is_missing(tmp_path):
    """The bogus-tree mkdir: with no existence check the provider used to
    materialise briefings/ and biographies/ under a nonexistent prose
    root and carry on."""
    absent = tmp_path / "absent"

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the API was called with no prose dir")

    p = _provider_with(handler, absent)
    with pytest.raises(RuntimeError, match="chronicler init-prose"):
        await p.generate(_request())

    assert not absent.exists()


@pytest.mark.asyncio
async def test_long_prompt_engages_1m_beta_header(tmp_path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return _stream_response()

    p = _provider_with(handler, tmp_path)
    # ~800k chars → ~200k estimated tokens, over the 180k threshold.
    await p.generate(_request(user_prompt="x" * 800_000))
    assert captured["headers"].get("anthropic-beta") == CONTEXT_1M_BETA


# --- generate: failure modes ---


@pytest.mark.asyncio
async def test_retries_on_529_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(529, json={"type": "error"})
        return _stream_response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())
    assert calls["n"] == 3
    assert resp.text == "Harold was bold."


@pytest.mark.asyncio
async def test_retries_exhausted_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"type": "error"})

    p = _provider_with(handler, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate(_request())


@pytest.mark.asyncio
async def test_transport_error_retried(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return _stream_response()

    p = _provider_with(handler, tmp_path)
    resp = await p.generate(_request())
    assert calls["n"] == 2
    assert resp.text == "Harold was bold."


@pytest.mark.asyncio
async def test_401_no_retry(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )

    p = _provider_with(handler, tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        await p.generate(_request())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_empty_prose_raises_runtime_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return _stream_response(_happy_stream(text_parts=("",)))

    p = _provider_with(handler, tmp_path)
    with pytest.raises(RuntimeError, match="no prose"):
        await p.generate(_request())


@pytest.mark.asyncio
async def test_max_tokens_truncation_raises_runtime_error(tmp_path):
    """ck3_chronicler-27ov.23 (audit M-N5): a stream that ends with
    stop_reason=max_tokens delivered non-empty text, so it used to pass the
    non-empty check, get a footer, get git-committed, and land in the DB as a
    finished biography — a mid-sentence truncation recorded as complete. It
    must raise so the pipeline's error net records a failed generation."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _stream_response(
            _happy_stream(
                text_parts=("A long biography cut off mid-",),
                stop_reason="max_tokens",
            )
        )

    p = _provider_with(handler, tmp_path)
    with pytest.raises(RuntimeError, match="max_tokens"):
        await p.generate(_request())


@pytest.mark.asyncio
async def test_wall_clock_timeout_raises_runtime_error(tmp_path, monkeypatch):
    p = _provider_with(lambda request: _stream_response(), tmp_path)

    async def slow_stream(body, headers):
        await asyncio.sleep(30)

    monkeypatch.setattr(p, "_stream_once", slow_stream)
    p._timeout = 0.05
    with pytest.raises(RuntimeError, match="timed out"):
        await p.generate(_request())


# --- prose-repo side effects ---


@pytest.mark.asyncio
async def test_writes_prose_repo_artifacts_and_footer(tmp_path):
    p = _provider_with(lambda request: _stream_response(), tmp_path)
    resp = await p.generate(_request())
    brief = tmp_path / "briefings" / "camp-1" / "42-v1.md"
    bio = tmp_path / "biographies" / "camp-1" / "42-v1.md"
    assert brief.is_file()
    assert bio.is_file()
    on_disk = bio.read_text(encoding="utf-8")
    assert on_disk.startswith("Harold was bold.")
    assert "Set down by Anthropic API" in on_disk
    assert "claude-opus-4-7" in on_disk
    # the response body stays bare prose — no footer double-stored in DB
    assert resp.text == "Harold was bold."
    # briefing carries the rendered header
    assert "# Briefing: character 42" in brief.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_versions_increment_across_calls(tmp_path):
    p = _provider_with(lambda request: _stream_response(), tmp_path)
    await p.generate(_request())
    await p.generate(_request())
    assert (tmp_path / "biographies" / "camp-1" / "42-v2.md").is_file()


@pytest.mark.asyncio
async def test_git_commit_invoked_with_rel_paths(tmp_path, monkeypatch):
    recorded: dict = {}

    async def recorder(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("chronicler.narrative.anthropic.git_commit_biography", recorder)
    p = _provider_with(lambda request: _stream_response(), tmp_path)
    await p.generate(_request())
    assert recorded["rel_brief"] == "briefings/camp-1/42-v1.md"
    assert recorded["rel_bio"] == "biographies/camp-1/42-v1.md"
    assert recorded["version"] == 1
    assert recorded["model"] == "claude-opus-4-7"


# --- lifecycle ---


@pytest.mark.asyncio
async def test_aclose_owned_vs_injected(tmp_path):
    injected = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _stream_response()))
    p = AnthropicProvider(api_key="k", client=injected, prose_repo_path=tmp_path)
    await p.aclose()
    assert not injected.is_closed  # injected client survives

    owned = AnthropicProvider(api_key="k", prose_repo_path=tmp_path)
    inner = owned._client
    await owned.aclose()
    assert inner.is_closed
