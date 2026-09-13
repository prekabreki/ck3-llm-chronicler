"""Contract tests the NarrativeProvider implementation must pass.

Issue #21 activated this module. It sat dormant for the whole
single-backend era: the ``provider`` fixture returned None and
``test_generate_returns_response_shape`` skipped itself, so the one test
that actually exercises the seam never ran against anything. With a third
transport landing, "every backend returns the same response shape" stops
being theoretical — the response feeds one persistence path and one cost
aggregate regardless of which backend produced it.

The fixture parametrises over the two HTTP transports, which can be
constructed hermetically (``httpx.MockTransport`` + a tmp prose dir).
``ClaudeCodeProvider`` needs subprocess mocking to reach ``generate``;
adding it here belongs with the ABC/contract hardening in issue #22,
which owns promoting the duck-typed members onto the base class.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
    PromptKind,
)

_ANTHROPIC_STREAM = (
    b'data: {"type":"message_start","message":{"model":"claude-opus-4-7",'
    b'"usage":{"input_tokens":10,"output_tokens":1}}}\n\n'
    b'data: {"type":"content_block_delta","delta":{"type":"text_delta",'
    b'"text":"Harold was bold."}}\n\n'
    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":4}}\n\n'
)

_CHAT_COMPLETION = {
    "model": "gpt-5.6-luna",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Harold was bold."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
}


def _mock_client(response_factory) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_factory()))


@pytest.fixture(params=["openai-compatible", "anthropic"])
def provider(request, tmp_path, monkeypatch) -> NarrativeProvider:
    """Every production transport that can be constructed hermetically."""

    async def _noop(**kwargs):
        return None

    if request.param == "openai-compatible":
        from chronicler.narrative.openai_compatible import OpenAICompatibleProvider

        monkeypatch.setattr("chronicler.narrative.openai_compatible.git_commit_biography", _noop)
        return OpenAICompatibleProvider(
            model="gpt-5.6-luna",
            preset="openai",
            api_key="test-key",
            prose_repo_path=tmp_path,
            retry_base_delay=0.0,
            client=_mock_client(lambda: httpx.Response(200, json=_CHAT_COMPLETION)),
        )

    from chronicler.narrative.anthropic import AnthropicProvider

    monkeypatch.setattr("chronicler.narrative.anthropic.git_commit_biography", _noop)
    return AnthropicProvider(
        api_key="test-key",
        prose_repo_path=tmp_path,
        retry_base_delay=0.0,
        client=_mock_client(
            lambda: httpx.Response(
                200,
                content=_ANTHROPIC_STREAM,
                headers={"content-type": "text/event-stream"},
            )
        ),
    )


@pytest.mark.asyncio
async def test_generate_returns_response_shape(provider: NarrativeProvider) -> None:
    req = NarrativeRequest(
        kind="biography",
        prompt_version="biography_v1",
        system_prompt="You are a chronicler.",
        user_prompt="Write a short biography.",
    )
    resp = await provider.generate(req)
    assert isinstance(resp, NarrativeResponse)
    assert resp.text
    assert resp.model
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
async def test_generate_refuses_a_register_less_request(provider: NarrativeProvider) -> None:
    """No transport may generate without an assembled system prompt.

    Issue #19 fixed this on two transports independently; making it a
    contract test is what stops the third from shipping the same defect —
    register-less output is a plausible-looking biography written by a
    generic assistant, and it persists as valid.
    """
    req = NarrativeRequest(
        kind="biography",
        prompt_version="biography_v1",
        system_prompt="   ",
        user_prompt="Write a short biography.",
    )
    with pytest.raises(RuntimeError, match="system prompt"):
        await provider.generate(req)


@pytest.mark.asyncio
async def test_provider_reports_a_tag_and_a_prose_dir(provider: NarrativeProvider) -> None:
    """``name_for_kind`` is what lands in the DB's provider column and what
    the cost rate card partitions on; ``prose_repo_path`` is what the
    shared system-prompt assembly reads before the request is built."""
    assert ":" in provider.name_for_kind("biography")
    assert provider.prose_repo_path is not None


@pytest.mark.asyncio
async def test_aclose_is_idempotent(provider: NarrativeProvider) -> None:
    """App/CLI shutdown calls this; a second call must not explode."""
    await provider.aclose()
    await provider.aclose()


def test_request_response_are_frozen() -> None:
    req = NarrativeRequest(
        kind="biography",
        prompt_version="v1",
        system_prompt="s",
        user_prompt="u",
    )
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        req.kind = "biography_woven"  # type: ignore[misc]


def test_prompt_kind_values() -> None:
    valid: tuple[PromptKind, ...] = ("biography", "biography_woven", "chronicle_export")
    assert len(valid) == 3


def test_the_mock_payloads_stay_in_sync_with_the_real_shapes() -> None:
    """Guard the fixtures themselves: a contract test built on a payload
    shape no vendor returns proves nothing. Both fixtures must carry the
    fields the transports actually read."""
    assert _CHAT_COMPLETION["choices"][0]["message"]["content"]
    assert _CHAT_COMPLETION["choices"][0]["finish_reason"] == "stop"
    assert "usage" in _CHAT_COMPLETION
    events = [
        json.loads(line[len("data:") :])
        for line in _ANTHROPIC_STREAM.decode().splitlines()
        if line.startswith("data:")
    ]
    assert [e["type"] for e in events] == [
        "message_start",
        "content_block_delta",
        "message_delta",
    ]


# --- issue #46: the ABC's promoted members, checked against every transport ---


def test_every_registry_backend_satisfies_the_full_abc() -> None:
    """The registry and the ABC must not drift apart.

    Constructs nothing — inspects the classes the registry's factories
    return, so a backend added without the contract members fails here
    rather than at the first character death.
    """
    from chronicler.narrative.anthropic import AnthropicProvider
    from chronicler.narrative.claude_code import ClaudeCodeProvider
    from chronicler.narrative.factory import known_backends
    from chronicler.narrative.openai_compatible import OpenAICompatibleProvider

    classes = {
        "claude-code": ClaudeCodeProvider,
        "anthropic": AnthropicProvider,
        "openai-compatible": OpenAICompatibleProvider,
    }
    assert set(classes) == set(known_backends()), (
        "a backend was added to the registry without a class here — "
        "the contract check would silently skip it"
    )
    for name, cls in classes.items():
        assert issubclass(cls, NarrativeProvider), name
        for member in (
            "name",
            "name_for_kind",
            "prose_repo_path",
            "resolved_models",
            "max_concurrent",
            "long_context_policy",
            "aclose",
            "generate",
        ):
            assert hasattr(cls, member), f"{name} is missing {member}"


class _MinimalDouble(NarrativeProvider):
    """The shape the suite actually injects: name + generate, nothing else.

    Every member issue #46 promoted onto the ABC has to have a usable default
    or this class stops being constructible — which is the contract those
    defaults exist to keep.
    """

    @property
    def name(self) -> str:
        return "double:v1"

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:  # pragma: no cover
        raise AssertionError("not called")


def test_abc_defaults_keep_a_minimal_double_working() -> None:
    from chronicler.narrative.model_resolution import LongContextPolicy

    double = _MinimalDouble()
    assert double.max_concurrent == 4
    assert double.long_context_policy is LongContextPolicy.NONE
    assert double.prose_repo_path is None
    assert set(double.resolved_models()) == {"biography", "closing", "global_override"}


@pytest.mark.asyncio
async def test_minimal_double_aclose_is_a_noop() -> None:
    await _MinimalDouble().aclose()  # must not raise


def test_resolved_models_is_declared_not_duck_typed(provider: NarrativeProvider) -> None:
    """GET /api/settings/models calls this directly now (the getattr +
    callable() probe is gone), so it has to be there on every transport."""
    snap = provider.resolved_models()
    assert set(snap) == {"biography", "closing", "global_override"}


def test_long_context_policy_never_leaks_across_families() -> None:
    """The #46 danger zone: the ``[1m]`` bracket is a claude-code model-tag
    convention and ``anthropic-beta`` is an Anthropic API header. Either one
    applied to an OpenAI-compatible endpoint is wrong in a way that still
    looks plausible in a diff."""
    from chronicler.narrative.anthropic import AnthropicProvider
    from chronicler.narrative.claude_code import ClaudeCodeProvider
    from chronicler.narrative.model_resolution import LongContextPolicy
    from chronicler.narrative.openai_compatible import OpenAICompatibleProvider

    assert ClaudeCodeProvider.long_context_policy.fget(None) is LongContextPolicy.MODEL_BRACKET  # type: ignore[attr-defined]
    assert AnthropicProvider.long_context_policy.fget(None) is LongContextPolicy.BETA_HEADER  # type: ignore[attr-defined]
    # The openai-compatible transport declares nothing, so it inherits the
    # ABC's NONE — the endpoint's own window applies.
    assert not hasattr(OpenAICompatibleProvider, "_long_context_policy_override")
    assert NarrativeProvider.long_context_policy.fget(None) is LongContextPolicy.NONE  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_openai_compatible_never_sends_a_1m_marker_however_big_the_prompt(
    tmp_path, monkeypatch
) -> None:
    """Behavioural counterpart to the policy test: drive a prompt far past
    the 180k long-context threshold and assert the wire carries neither
    marker."""
    from chronicler.narrative.model_resolution import LONG_CONTEXT_THRESHOLD_TOKENS
    from chronicler.narrative.openai_compatible import OpenAICompatibleProvider

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr("chronicler.narrative.openai_compatible.git_commit_biography", _noop)

    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_CHAT_COMPLETION)

    provider = OpenAICompatibleProvider(
        model="gpt-5.6-luna",
        preset="openai",
        api_key="test-key",
        prose_repo_path=tmp_path,
        retry_base_delay=0.0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    huge = "word " * (LONG_CONTEXT_THRESHOLD_TOKENS // 2)  # ~4x the threshold in chars
    await provider.generate(
        NarrativeRequest(
            kind="biography",
            prompt_version="biography_v1",
            system_prompt="register",
            user_prompt=huge,
            metadata={"campaign_uuid": "c", "character_id": "1"},
        )
    )
    await provider.aclose()

    assert "anthropic-beta" not in {k.lower() for k in captured["headers"]}
    assert "[1m]" not in json.dumps(captured["body"])
    assert captured["body"]["model"] == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_cli_one_shot_wrapper_closes_on_success_and_on_failure() -> None:
    """``chronicler tail``/``dev``/``save-tail`` own the provider they build.
    The wrapper closes it inside the loop — and in a finally, so Ctrl-C and a
    crashing ingest loop close it too. Those are the paths that actually
    happen in daily use, which is why a success-only check is not enough."""
    import asyncio as _asyncio

    from chronicler.cli.ingest import _run_closing_provider

    class _Recorder:
        def __init__(self) -> None:
            self.closes = 0

        @property
        def name(self) -> str:
            return "recording:v1"

        async def aclose(self) -> None:
            self.closes += 1

    async def _ok():
        return "done"

    async def _boom():
        raise RuntimeError("ingest loop died")

    def _drive(coro_factory, provider):
        # _run_closing_provider calls asyncio.run itself, so it cannot be
        # awaited from inside a running loop — hand it to a worker thread.
        # It takes a factory, so nothing is constructed outside that loop.
        return _asyncio.get_running_loop().run_in_executor(
            None, lambda: _run_closing_provider(coro_factory, provider)
        )

    happy = _Recorder()
    assert await _drive(_ok, happy) == "done"
    assert happy.closes == 1

    sad = _Recorder()
    with pytest.raises(RuntimeError):
        await _drive(_boom, sad)
    assert sad.closes == 1, "aclose() was skipped when the ingest loop raised"

    # No provider (chronicler tail --no-biography) must not explode.
    assert await _drive(_ok, None) == "done"
