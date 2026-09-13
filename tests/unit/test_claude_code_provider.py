"""Tests for ClaudeCodeProvider — the headless ``claude --print`` provider
introduced by ck3_chronicler-tbrm, lean-shaped since ck3_chronicler-l7rk
(and lean-ONLY since ck3_chronicler-27ov.14 deleted the legacy agentic
fallback). The subprocess is mocked end-to-end via
:func:`asyncio.create_subprocess_exec`; no real ``claude`` binary is invoked.
The mock simulates the lean handoff: the CLI writes nothing on disk and
returns a JSON envelope whose ``result`` carries the prose; the provider
itself writes the biography file before returning.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile as _tempfile_for_spy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from chronicler.narrative.claude_code import (
    _API_MODE_ENV_VARS_TO_STRIP,
    ClaudeCodeProvider,
    _build_subprocess_env,
    _parse_json_envelope,
    _subprocess_creationflags,
)
from chronicler.narrative.prose_io import next_version as _next_version
from chronicler.narrative.prose_io import render_briefing_markdown
from chronicler.narrative.provider import NarrativeRequest


def _make_prose_repo(tmp_path: Path) -> Path:
    """Build a minimal scaffolding the provider expects."""
    (tmp_path / "briefings").mkdir()
    (tmp_path / "biographies").mkdir()
    (tmp_path / "CLAUDE.md").write_text("(role override)", encoding="utf-8")
    return tmp_path


def _request(
    *,
    user_prompt: str = "Recorded events: (none)",
    metadata: dict[str, str] | None = None,
    system_prompt: str = "(assembled register + voice rules)",
) -> NarrativeRequest:
    return NarrativeRequest(
        kind="biography_woven",
        prompt_version="biography_v5",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        metadata=metadata or {"character_id": "12345", "campaign_uuid": "abc-123"},
    )


def _result_envelope(result: str = "body", **extra) -> bytes:
    """Canonical lean-mode success envelope: prose in ``result``."""
    payload = {"type": "result", "is_error": False, "result": result}
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _git_ok_proc(argv: tuple) -> AsyncMock:
    """A no-op success proc for the best-effort git auto-commit, which
    shares the patched create_subprocess_exec with the claude call.
    ``git diff --cached --quiet`` returns 1 (= changes staged) so the
    commit step downstream actually fires; add + commit return 0."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 1 if "diff" in argv else 0
    proc.kill = lambda: None
    proc.wait = AsyncMock(return_value=0)
    return proc


# --- Helpers (pure functions) ---


def test_next_version_returns_one_for_empty_dir(tmp_path: Path) -> None:
    target = tmp_path / "biographies" / "abc"
    target.mkdir(parents=True)
    assert _next_version(target, "12345") == 1


def test_next_version_returns_one_when_dir_missing(tmp_path: Path) -> None:
    """No directory == first generation. Provider mkdirs after we
    decide the version, so we need the version answer first."""
    assert _next_version(tmp_path / "no-such-dir", "12345") == 1


def test_next_version_picks_max_plus_one(tmp_path: Path) -> None:
    target = tmp_path / "biographies" / "abc"
    target.mkdir(parents=True)
    (target / "12345-v1.md").write_text("v1")
    (target / "12345-v2.md").write_text("v2")
    (target / "12345-v5.md").write_text("v5")
    assert _next_version(target, "12345") == 6


def test_next_version_ignores_other_characters(tmp_path: Path) -> None:
    """Only files matching the requested basename should bump the
    version — a peer character's v9 must not push our character to v10."""
    target = tmp_path / "biographies" / "abc"
    target.mkdir(parents=True)
    (target / "99999-v9.md").write_text("peer v9")
    (target / "12345-v1.md").write_text("ours v1")
    assert _next_version(target, "12345") == 2


def test_next_version_ignores_non_canonical_filenames(tmp_path: Path) -> None:
    """``-v3-old.md`` and ``-v3-backup.md`` must not contribute to the
    version count — only the canonical ``-v<N>.md`` form is the truth."""
    target = tmp_path / "biographies" / "abc"
    target.mkdir(parents=True)
    (target / "12345-v1.md").write_text("v1")
    (target / "12345-v3-old.md").write_text("noise")
    assert _next_version(target, "12345") == 2


def test_render_briefing_markdown_embeds_user_prompt_and_metadata() -> None:
    req = _request(
        user_prompt="Header line\n\nGlossary\n\nEvents: ...",
        metadata={"character_id": "42", "campaign_uuid": "uuid-77"},
    )
    out = render_briefing_markdown(req)
    # User prompt body comes through verbatim.
    assert "Header line" in out
    assert "Events: ..." in out
    # Metadata is recorded in the header for traceability.
    assert "character 42" in out
    assert "campaign=uuid-77" in out
    assert "kind=biography_woven" in out
    assert "prompt_version=biography_v5" in out
    # ck3_chronicler-0224: per-call instructions are NO LONGER embedded
    # in the briefing file. They live at <prose_repo>/voice/<kind>.md so
    # the prompt cache can hit on the byte-identical static prefix
    # across calls within a 5-min window.
    assert "Per-call instructions" not in out
    assert "legacy system prompt" not in out


def test_render_briefing_markdown_handles_missing_metadata() -> None:
    req = NarrativeRequest(
        kind="biography",
        prompt_version="biography_v3",
        system_prompt="s",
        user_prompt="body",
        metadata={},
    )
    out = render_briefing_markdown(req)
    assert "(unknown)" in out  # both character + campaign placeholders
    assert "body" in out


def test_render_briefing_markdown_omits_override_block_regardless_of_system_prompt() -> None:
    """ck3_chronicler-0224: with the cache restructure, the per-call
    instructions ALWAYS live in the static voice file, never inside the
    briefing. The system_prompt field on NarrativeRequest is retained for
    backward compatibility with callers that don't know about the new
    voice path but is no longer embedded — defensive: nobody gets a
    "Per-call instructions" block anymore, empty system_prompt or not."""
    req = NarrativeRequest(
        kind="biography",
        prompt_version="biography_v3",
        system_prompt="",
        user_prompt="body",
        metadata={"character_id": "42", "campaign_uuid": "u"},
    )
    out = render_briefing_markdown(req)
    assert "Per-call instructions" not in out
    assert "body" in out

    # Even with a populated system_prompt, the briefing still excludes it.
    req_with_sysprompt = NarrativeRequest(
        kind="chronicle_export",
        prompt_version="chronicle_export_v1",
        system_prompt="Synthesise all biographies.",
        user_prompt="Campaign biographies: (none)\n\nEvents: ...",
        metadata={"character_id": "1234", "campaign_uuid": "u"},
    )
    out2 = render_briefing_markdown(req_with_sysprompt)
    assert "Per-call instructions" not in out2
    assert "Synthesise all biographies" not in out2


def test_voice_file_for_kind_maps_each_active_kind() -> None:
    """ck3_chronicler-0224: every active prompt kind has a static voice
    file under <prose_repo>/voice/. The claude-code lean path no longer
    @-references these (27ov.14 removed the agentic wrapper prompt), but
    the anthropic transport still inlines their CONTENT into its system
    blocks — the mapping lives on in prose_io."""
    from chronicler.narrative.prose_io import voice_file_for_kind

    assert voice_file_for_kind("biography") == "voice/biography.md"
    assert voice_file_for_kind("biography_woven") == "voice/biography-woven.md"
    assert voice_file_for_kind("chronicle_export") == "voice/chronicle-export.md"
    # Unknown kinds gracefully return "" — consumers fall back to no
    # static voice reference.
    assert voice_file_for_kind("unknown_kind") == ""


# --- JSON envelope parsing ---


def test_parse_json_envelope_simple_object() -> None:
    raw = json.dumps({"result": "ok", "usage": {"input_tokens": 10}}).encode()
    parsed = _parse_json_envelope(raw)
    assert parsed is not None
    assert parsed["result"] == "ok"
    assert parsed["usage"]["input_tokens"] == 10


def test_parse_json_envelope_finds_trailing_object_after_log_text() -> None:
    """Defence in depth: if a debug line leaks onto stdout before the
    envelope, the parser still extracts the trailing JSON."""
    raw = b'[debug] some chatter\n{"result":"text","usage":{"input_tokens":5,"output_tokens":7}}'
    parsed = _parse_json_envelope(raw)
    assert parsed is not None
    assert parsed["usage"]["output_tokens"] == 7


def test_parse_json_envelope_returns_none_on_empty_or_malformed() -> None:
    assert _parse_json_envelope(b"") is None
    assert _parse_json_envelope(b"not json at all") is None


# --- Cache-aware usage extraction (2026-05-08 smoke schema) ---


@pytest.mark.asyncio
async def test_generate_sums_cache_tokens_into_input_for_real_envelope_shape(
    tmp_path: Path, monkeypatch
) -> None:
    """The actual ``claude --print --output-format json`` envelope splits
    prompt-side tokens across input_tokens (marginal, post-cache),
    cache_creation_input_tokens, and cache_read_input_tokens. The
    provider sums those into a single ``input_tokens`` figure on
    NarrativeResponse so the chronicler's cost dashboard reflects total
    prompt-side load — observed schema captured in the 2026-05-08
    synthetic-jarl smoke."""
    prose = _make_prose_repo(tmp_path)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        proc = AsyncMock()
        # Schema mirrors the 2026-05-08 synthetic-jarl smoke: 7 marginal
        # input tokens, 24445 cache-creation, 60383 cache-read, 6126 out.
        # total_cost_usd at the top level (NOT inside usage).
        proc.communicate = AsyncMock(
            return_value=(
                json.dumps(
                    {
                        "type": "result",
                        "result": "The jarl's full biography.",
                        "total_cost_usd": 0.336,
                        "usage": {
                            "input_tokens": 7,
                            "output_tokens": 6126,
                            "cache_creation_input_tokens": 24445,
                            "cache_read_input_tokens": 60383,
                        },
                        "modelUsage": {
                            "claude-opus-4-7[1m]": {
                                "inputTokens": 7,
                                "outputTokens": 6126,
                                "costUSD": 0.336,
                            }
                        },
                    }
                ).encode(),
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    resp = await provider.generate(_request())

    # input_tokens is the SUM of marginal + cache_creation + cache_read.
    # Without this, the dashboard would report 7 prompt tokens for an
    # 84,835-token call.
    assert resp.input_tokens == 7 + 24445 + 60383
    assert resp.output_tokens == 6126
    # model is read from the modelUsage key (the envelope has no
    # top-level "model" field in this schema).
    assert resp.model == "claude-opus-4-7[1m]"
    # ck3_chronicler-cs1o: the cache breakdown + the CLI's own cost
    # figure survive onto the response so the cost layer can bill each
    # bucket at its real rate instead of the summed-input approximation.
    assert resp.cache_write_tokens == 24445
    assert resp.cache_read_tokens == 60383
    assert resp.cost_usd == 0.336


@pytest.mark.asyncio
async def test_generate_lean_path_reads_prose_from_result_and_writes_file(
    tmp_path: Path, monkeypatch
) -> None:
    """ck3_chronicler-l7rk: generate() runs a scoped completion — tools
    disallowed, the register via --system-prompt-file, a neutral cwd (NOT the
    prose repo) — and reads the prose from the envelope `result`, writing the
    bio file itself (the agent writes nothing)."""
    prose = _make_prose_repo(tmp_path)
    expected_bio_path = prose / "biographies" / "abc-123" / "12345-v1.md"
    calls: list[tuple[list, object]] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        calls.append((list(argv), kwargs.get("cwd")))
        # Lean mode: the agent writes no file; prose comes back in `result`.
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(
                json.dumps(
                    {
                        "type": "result",
                        "result": "The earl endured one last winter.",
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 1200,
                            "cache_creation_input_tokens": 20000,
                            "cache_read_input_tokens": 0,
                        },
                        "modelUsage": {"claude-opus-4-7[1m]": {"inputTokens": 5}},
                    }
                ).encode(),
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    resp = await provider.generate(_request())

    # Prose came from `result`; we persisted the file ourselves.
    assert resp.text == "The earl endured one last winter."
    assert expected_bio_path.is_file()
    assert "endured one last winter" in expected_bio_path.read_text(encoding="utf-8")

    # Inspect the claude invocation (filter out the best-effort git commit,
    # which shares the patched create_subprocess_exec).
    claude_calls = [(a, c) for a, c in calls if "--print" in a]
    assert len(claude_calls) == 1
    argv, cwd = claude_calls[0]
    assert "--disallowedTools" in argv
    assert "--system-prompt-file" in argv  # CLAUDE.md exists in the fake repo
    assert "--permission-mode" not in argv  # no agentic edit permissions
    assert cwd != str(prose)  # neutral cwd — no repo autoload/indexing
    # ck3_chronicler-wpx4: the inlined briefing is piped via stdin, not a
    # -p CLI arg (which blew the Windows ~32KB command-line limit for
    # event-rich characters). The stdin content is asserted in
    # test_lean_prompt_passed_via_stdin_not_command_line.
    assert "-p" not in argv


@pytest.mark.asyncio
async def test_lean_prompt_passed_via_stdin_not_command_line(tmp_path: Path, monkeypatch) -> None:
    """ck3_chronicler-wpx4: the lean shape inlines the full briefing into the
    prompt. Passing it as a -p CLI argument blows the Windows ~32KB
    command-line limit for event-rich characters and the closing chronicle
    (FileNotFoundError [WinError 206] the filename or extension is too long).
    The prompt must be piped via stdin, not placed on the command line."""
    prose = _make_prose_repo(tmp_path)
    # Collect every spawn; the best-effort git commit shares this patch, so we
    # filter for the claude invocation (the one with --print) below.
    calls: list[dict] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        rec: dict = {"argv": list(argv), "stdin": kwargs.get("stdin"), "input": None}
        calls.append(rec)

        async def fake_communicate(input=None):
            rec["input"] = input
            return (
                json.dumps(
                    {
                        "type": "result",
                        "result": "Prose body.",
                        "usage": {"input_tokens": 5, "output_tokens": 100},
                    }
                ).encode(),
                b"",
            )

        proc = AsyncMock()
        proc.communicate = fake_communicate
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose, model="claude-opus-4-7[1m]")
    await provider.generate(_request())

    claude_calls = [c for c in calls if "--print" in c["argv"]]
    assert len(claude_calls) == 1
    captured = claude_calls[0]
    argv = captured["argv"]
    assert "--print" in argv  # still non-interactive print mode
    assert "-p" not in argv, "prompt must not be a -p CLI arg (WinError 206)"
    assert not any("--- BRIEFING ---" in a for a in argv), (
        "the briefing must not leak onto the command line"
    )
    # The prompt is piped via stdin instead — no command-line length ceiling.
    assert captured["stdin"] is not None, "stdin must be a pipe"
    assert captured["input"] is not None, "prompt must be written to stdin"
    body = (
        captured["input"].decode("utf-8")
        if isinstance(captured["input"], (bytes, bytearray))
        else captured["input"]
    )
    assert "--- BRIEFING ---" in body


# --- ck3_chronicler-591u: cache-hit ratio observability log ---


@pytest.mark.asyncio
async def test_generate_logs_cache_hit_ratio_when_cacheable_tokens_present(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Per-call INFO line surfaces cache_read / (cache_read + cache_create)
    so a session log can be grepped to confirm slice-3's prompt-cache
    restructure is actually producing hits. Cacheable=84_828 (24,445
    + 60,383) → ratio ≈ 0.7117 from the 2026-05-08 smoke schema."""
    prose = _make_prose_repo(tmp_path)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(
                json.dumps(
                    {
                        "type": "result",
                        "result": "ok",
                        "total_cost_usd": 0.336,
                        "usage": {
                            "input_tokens": 7,
                            "output_tokens": 6126,
                            "cache_creation_input_tokens": 24445,
                            "cache_read_input_tokens": 60383,
                        },
                        "modelUsage": {"claude-opus-4-7[1m]": {"inputTokens": 7}},
                    }
                ).encode(),
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    with caplog.at_level("INFO", logger="chronicler.narrative.claude_code"):
        await provider.generate(_request())

    cache_hit_lines = [r for r in caplog.records if "cache_hit" in r.getMessage()]
    assert len(cache_hit_lines) == 1, (
        f"expected exactly one cache_hit INFO line per generate(); "
        f"got {len(cache_hit_lines)}: {[r.getMessage() for r in cache_hit_lines]}"
    )
    msg = cache_hit_lines[0].getMessage()
    assert "cache_read=60383" in msg
    assert "cache_create=24445" in msg
    assert "input_marginal=7" in msg
    # 60383 / (60383 + 24445) == 0.71185… → log uses %.3f.
    assert "cache_read_ratio=0.712" in msg


@pytest.mark.asyncio
async def test_generate_logs_cache_hit_ratio_zero_when_no_cacheable_tokens(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Calls with no cacheable content (no @-references in the prompt,
    e.g. an internal smoke call) report ratio=0.000 by convention;
    the raw counts in the same line disambiguate "no hits" from "no
    cache lookups attempted". Guards against a div-by-zero when
    cacheable=0."""
    prose = _make_prose_repo(tmp_path)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(
                json.dumps(
                    {
                        "type": "result",
                        "result": "ok",
                        "usage": {"input_tokens": 4500, "output_tokens": 800},
                        "modelUsage": {"claude-opus-4-7[1m]": {"inputTokens": 4500}},
                    }
                ).encode(),
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    with caplog.at_level("INFO", logger="chronicler.narrative.claude_code"):
        await provider.generate(_request())

    cache_hit_lines = [r for r in caplog.records if "cache_hit" in r.getMessage()]
    assert len(cache_hit_lines) == 1
    msg = cache_hit_lines[0].getMessage()
    assert "cache_read=0" in msg
    assert "cache_create=0" in msg
    assert "input_marginal=4500" in msg
    assert "cache_read_ratio=0.000" in msg


# --- generate() end-to-end with mocked subprocess ---


@pytest.mark.asyncio
async def test_generate_writes_briefing_runs_subprocess_and_returns_response(
    tmp_path: Path, monkeypatch
) -> None:
    """Happy path: provider materialises briefing, runs claude in a
    neutral cwd, reads the prose from the envelope ``result``, writes
    the biography file itself, returns NarrativeResponse with parsed
    token counts."""
    prose = _make_prose_repo(tmp_path)
    expected_bio_path = prose / "biographies" / "abc-123" / "12345-v1.md"
    captured_cwds: list[object] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        captured_cwds.append(kwargs.get("cwd"))
        # Return a mock process whose communicate() yields a JSON envelope
        # carrying the prose — the lean CLI writes no files.
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(
                json.dumps(
                    {
                        "type": "result",
                        "result": "Sweyn fell at Aalborg in his fifty-sixth year.",
                        "usage": {"input_tokens": 4500, "output_tokens": 800},
                        "total_cost_usd": 0.123,
                        "modelUsage": {"claude-opus-4-7[1m]": {"inputTokens": 4500}},
                    }
                ).encode(),
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose, model="claude-opus-4-7[1m]")
    req = _request()
    resp = await provider.generate(req)

    # Briefing was written.
    brief_path = prose / "briefings" / "abc-123" / "12345-v1.md"
    assert brief_path.is_file()
    brief = brief_path.read_text(encoding="utf-8")
    assert "character 12345" in brief
    assert "Recorded events: (none)" in brief

    # claude ran in a neutral temp dir, NOT the prose repo (l7rk).
    assert len(captured_cwds) == 1
    assert captured_cwds[0] != str(prose)

    # The provider persisted the biography file itself.
    assert expected_bio_path.is_file()
    assert "Sweyn fell at Aalborg" in expected_bio_path.read_text(encoding="utf-8")

    # Response carries the biography body and parsed token counts.
    assert resp.text == "Sweyn fell at Aalborg in his fifty-sixth year."
    assert resp.input_tokens == 4500
    assert resp.output_tokens == 800
    assert resp.model == "claude-opus-4-7[1m]"
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
async def test_generate_keeps_biography_prompt_for_biography_kinds(
    tmp_path: Path, monkeypatch
) -> None:
    """The biography wrapper must stay biography-shaped for kind=
    biography and kind=biography_woven so the existing prose pipeline
    is unchanged. Regression guard against future kind-aware refactors
    accidentally breaking the marquee biography path."""
    prose = _make_prose_repo(tmp_path)
    calls: list[dict] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        rec: dict = {"argv": list(argv), "input": None}
        calls.append(rec)

        async def fake_communicate(input=None):
            rec["input"] = input
            return (_result_envelope("body"), b"")

        proc = AsyncMock()
        proc.communicate = fake_communicate
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    await provider.generate(_request())  # kind="biography_woven"

    assert len(calls) == 1
    # The prompt arrives via stdin (wpx4), not a -p arg.
    assert "-p" not in calls[0]["argv"]
    prompt = calls[0]["input"].decode("utf-8")
    assert "Write their biography in full" in prompt
    # The briefing rides inline below the instruction line.
    assert "--- BRIEFING ---" in prompt
    # JSON wrapper must NOT leak into the biography prompt.
    assert "JSON" not in prompt


def test_build_subprocess_env_strips_api_mode_triggers(monkeypatch) -> None:
    """ck3_chronicler-tbrm pivot intent: chronicler subprocess must not
    inherit ANTHROPIC_API_KEY (or sibling vars) from the dev shell, or
    the Claude Code CLI silently switches into API mode and bills the
    Anthropic Messages API per call. Live signal: 2026-05-09 e95t —
    user's API credits drained because ANTHROPIC_API_KEY was set in
    the dev shell."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "another-secret")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # preserve unrelated var
    monkeypatch.setenv("HOME", "/home/test")

    env = _build_subprocess_env()

    for var in _API_MODE_ENV_VARS_TO_STRIP:
        assert var not in env, f"{var} must be stripped from subprocess env"
    # Unrelated vars survive — the CLI needs PATH at minimum.
    assert env.get("PATH") == "/usr/bin:/bin"
    assert env.get("HOME") == "/home/test"


@pytest.mark.asyncio
async def test_generate_subprocess_env_excludes_api_key_even_when_set(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end check that the env dict actually passed to
    create_subprocess_exec doesn't carry ANTHROPIC_API_KEY when the
    parent process has one set. Without this guarantee, the CLI uses
    the API key (pay-per-call) instead of the user's subscription
    auth — the 2026-05-09 e95t failure mode."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    prose = _make_prose_repo(tmp_path)
    captured_env: dict[str, str] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        # Capture the env the provider passed so the test can assert
        # on it. Then simulate a successful claude run.
        captured_env.update(kwargs.get("env") or {})
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    await provider.generate(_request())

    assert "ANTHROPIC_API_KEY" not in captured_env
    # Confirm the env was passed at all (regression guard against an
    # accidental env=None / env-arg-removed refactor).
    assert captured_env, "subprocess must receive an explicit env dict"


def test_build_subprocess_env_forces_utf8_on_windows() -> None:
    """ck3_chronicler-iaf0: subprocess must run with PYTHONIOENCODING=utf-8
    so high-Unicode glyphs (em-dashes, accented chars) survive on Windows
    where the platform default is cp1252. Without this, biography +
    memory files written by the subprocess come back as mojibake when
    chronicler reads them as UTF-8 — concrete signal 2026-05-10 smoke:
    every memory body had ``â€"`` where em-dashes belonged."""
    env = _build_subprocess_env()
    assert env.get("PYTHONIOENCODING") == "utf-8"
    # Belt-and-braces — Python 3.7+ UTF-8 mode covers any code path that
    # ignores PYTHONIOENCODING (writes to disk via a tool that uses
    # locale.getpreferredencoding()).
    assert env.get("PYTHONUTF8") == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only console-flag behaviour")
def test_subprocess_creationflags_drops_detached_process() -> None:
    """ck3_chronicler-2vhv: DETACHED_PROCESS gives the claude subtree NO
    console, so its node grandchildren allocate their own fresh *visible*
    consoles (a window flashes per biography — reliably reproduced by the
    SPA 'Regenerate biography' button). CREATE_NO_WINDOW alone gives the
    process a hidden, *inheritable* console, so grandchildren inherit it
    instead of popping their own. Keep CREATE_NO_WINDOW; drop
    DETACHED_PROCESS."""
    flags = _subprocess_creationflags()
    assert flags & subprocess.CREATE_NO_WINDOW, "must keep CREATE_NO_WINDOW"
    assert not (flags & subprocess.DETACHED_PROCESS), (
        "DETACHED_PROCESS must be dropped — it causes grandchild console pops"
    )


@pytest.mark.asyncio
async def test_generate_raises_when_subprocess_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    prose = _make_prose_repo(tmp_path)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b"Error: API authentication failed"))
        proc.returncode = 1
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=1)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    with pytest.raises(RuntimeError, match="claude --print exited 1"):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_generate_surfaces_stdout_envelope_error_when_subprocess_exits_nonzero(
    tmp_path: Path, monkeypatch
) -> None:
    """ck3_chronicler-e95t: claude --print emits Anthropic API errors
    (credit exhaustion, rate limits, request validation) as a JSON
    envelope on STDOUT with ``is_error=true``, while stderr stays
    empty. The provider must extract the envelope's ``result`` and
    ``api_error_status`` so the queue UI shows the real reason instead
    of a useless ``exited 1:`` with nothing after the colon. Live
    signal: 2026-05-09 user saw 16+ consecutive failures with empty
    error messages; manual repro surfaced 'Credit balance is too low'
    on stdout."""
    prose = _make_prose_repo(tmp_path)

    envelope = {
        "type": "result",
        "is_error": True,
        "api_error_status": 400,
        "result": "Credit balance is too low",
    }

    async def fake_create_subprocess_exec(*argv, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(json.dumps(envelope).encode("utf-8"), b""))
        proc.returncode = 1
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=1)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    with pytest.raises(RuntimeError) as excinfo:
        await provider.generate(_request())
    msg = str(excinfo.value)
    assert "exited 1" in msg
    assert "Credit balance is too low" in msg
    assert "api_error_status=400" in msg


@pytest.mark.asyncio
async def test_generate_raises_when_envelope_has_no_prose(tmp_path: Path, monkeypatch) -> None:
    """If claude exits 0 but the envelope carries no prose in ``result``
    (refused, empty completion, schema drift), surface a clear error
    rather than returning — or persisting — an empty biography."""
    prose = _make_prose_repo(tmp_path)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        proc = AsyncMock()
        # Exit 0 but the `result` field is missing entirely.
        proc.communicate = AsyncMock(return_value=(b'{"type":"result","is_error":false}', b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    with pytest.raises(RuntimeError, match="no prose"):
        await provider.generate(_request())
    # Nothing was persisted — no empty biography artifact on disk.
    assert not (prose / "biographies" / "abc-123" / "12345-v1.md").exists()


@pytest.mark.asyncio
async def test_generate_raises_when_prose_repo_missing(tmp_path: Path) -> None:
    """A configured prose repo path that doesn't exist should surface
    a clear error, not a confusing CalledProcessError on the subprocess."""
    provider = ClaudeCodeProvider(prose_repo_path=tmp_path / "nonexistent")
    with pytest.raises(RuntimeError, match="prose repo not found"):
        await provider.generate(_request())


@pytest.mark.asyncio
async def test_generate_versions_increment_per_character(tmp_path: Path, monkeypatch) -> None:
    """Two successive generations for the same character produce v1
    then v2 — the provider must read existing biography files when
    deciding the next version."""
    prose = _make_prose_repo(tmp_path)
    bio_dir = prose / "biographies" / "abc-123"
    call_count = {"n": 0}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        # ck3_chronicler-cj2f: provider also shells out to git for the
        # auto-commit pass. Treat git invocations as no-op success — the
        # test cares about claude --print bookkeeping, not the commit;
        # git semantics are covered by their own tests below.
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        call_count["n"] += 1
        # The prose for each call comes back in the envelope `result`;
        # the provider writes the version-suffixed file itself.
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(
                _result_envelope(f"version-{call_count['n']} body"),
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    r1 = await provider.generate(_request())
    r2 = await provider.generate(_request())
    assert r1.text == "version-1 body"
    assert r2.text == "version-2 body"
    assert (bio_dir / "12345-v1.md").is_file()
    assert (bio_dir / "12345-v2.md").is_file()
    # And the matching briefings should have been written.
    assert (prose / "briefings" / "abc-123" / "12345-v1.md").is_file()
    assert (prose / "briefings" / "abc-123" / "12345-v2.md").is_file()


@pytest.mark.asyncio
async def test_generate_passes_correct_argv_to_subprocess(tmp_path: Path, monkeypatch) -> None:
    """Lock down the CLI invocation: model flag, --print, --output-format
    json, --disallowedTools, --system-prompt-file pointing at the prose
    repo's CLAUDE.md — and the ABSENCE of the agentic flags
    (--permission-mode, -p). If any of these change accidentally we
    fail loudly."""
    prose = _make_prose_repo(tmp_path)
    captured: dict[str, list] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        # cj2f: ignore git auto-commit invocations — this test only
        # cares about the claude --print argv shape.
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        captured["argv"] = list(argv)
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(
        prose_repo_path=prose, model="claude-opus-4-7[1m]", claude_bin="claude"
    )
    await provider.generate(_request())

    argv = captured["argv"]
    assert argv[0] == "claude"
    assert "--print" in argv
    assert "--model" in argv and "claude-opus-4-7[1m]" in argv
    assert "--output-format" in argv and "json" in argv
    # Lean scoped completion: tools denied so the model can't go agentic.
    tools_idx = argv.index("--disallowedTools")
    assert "Write" in argv[tools_idx + 1] and "Bash" in argv[tools_idx + 1]
    # The role-reshape register rides via --system-prompt-file. Issue
    # #19: it points at a per-call temp file holding the assembled
    # register + voice rules, NOT at the prose repo's CLAUDE.md — the
    # transport ships req.system_prompt and derives nothing itself.
    # Content + cleanup are pinned by
    # test_system_prompt_file_carries_the_requests_system_prompt.
    sysprompt_idx = argv.index("--system-prompt-file")
    assert argv[sysprompt_idx + 1] != str(prose / "CLAUDE.md")
    assert argv[sysprompt_idx + 1].endswith("chronicler-system-prompt.md")
    # Agentic flags are gone (27ov.14): no edit permissions, no -p prompt
    # (it's piped via stdin — wpx4).
    assert "--permission-mode" not in argv
    assert "-p" not in argv


# --- Issue #19: the system prompt is transported, not re-derived ---


@pytest.mark.asyncio
async def test_system_prompt_file_carries_the_requests_system_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    """Issue #19: --system-prompt-file must carry ``req.system_prompt``
    verbatim, not the prose repo's CLAUDE.md alone.

    Passing CLAUDE.md directly is what dropped voice/biography-woven.md
    from every woven generation on this transport after 27ov.14 removed
    the agentic wrapper that used to @-reference it.
    """
    prose = _make_prose_repo(tmp_path)
    seen: dict[str, str] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        argv_list = list(argv)
        sp_path = Path(argv_list[argv_list.index("--system-prompt-file") + 1])
        # Read it here: the provider removes it once the call returns.
        seen["path"] = str(sp_path)
        seen["content"] = sp_path.read_text(encoding="utf-8")
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    await provider.generate(_request(system_prompt="(register)\n\n(woven voice rules)"))

    assert seen["content"] == "(register)\n\n(woven voice rules)"
    # Temp file, not the prose repo's own CLAUDE.md.
    assert seen["path"] != str(prose / "CLAUDE.md")


@pytest.mark.asyncio
async def test_system_prompt_file_is_removed_after_generation(tmp_path: Path, monkeypatch) -> None:
    """The temp file must not outlive the call — one per generation, and
    the prose dir is a git repo the user reads."""
    prose = _make_prose_repo(tmp_path)
    seen: dict[str, Path] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        argv_list = list(argv)
        seen["path"] = Path(argv_list[argv_list.index("--system-prompt-file") + 1])
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    await ClaudeCodeProvider(prose_repo_path=prose).generate(_request())

    assert not seen["path"].exists()


@pytest.mark.asyncio
async def test_system_prompt_file_is_removed_when_the_subprocess_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Cleanup on the error path too — the failure exit is the one that
    historically leaks temp files."""
    prose = _make_prose_repo(tmp_path)
    seen: dict[str, Path] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        argv_list = list(argv)
        seen["path"] = Path(argv_list[argv_list.index("--system-prompt-file") + 1])
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b"boom"))
        proc.returncode = 1
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(RuntimeError):
        await ClaudeCodeProvider(prose_repo_path=prose).generate(_request())

    assert not seen["path"].exists()


@pytest.mark.asyncio
async def test_generate_refuses_an_empty_system_prompt(tmp_path: Path, monkeypatch) -> None:
    """An empty system prompt would run the CLI with no chronicler
    register at all — generic-assistant prose persisted as a biography.
    That is the exact silent failure this issue removes, so the
    transport refuses rather than degrading."""
    prose = _make_prose_repo(tmp_path)

    async def fail_if_called(*argv, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("claude was invoked without a system prompt")

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fail_if_called,
    )

    with pytest.raises(RuntimeError, match="system prompt"):
        await ClaudeCodeProvider(prose_repo_path=prose).generate(_request(system_prompt="  "))


# --- ck3_chronicler-vcv2-followup: attribution footer on each biography ---


def test_format_attribution_footer_with_full_token_counts() -> None:
    """vcv2-followup: the attribution footer renders model + thousand-
    separated token counts so a reader of the on-disk biography sees
    which model wrote it and how much it cost in tokens."""
    from chronicler.narrative.claude_code import _format_attribution_footer

    footer = _format_attribution_footer(
        model="claude-opus-4-7[1m]",
        input_tokens=12345,
        output_tokens=678,
    )
    assert footer.startswith("\n\n---\n")
    assert "Set down by Claude Code" in footer
    assert "claude-opus-4-7[1m]" in footer
    assert "12,345 in" in footer
    assert "678 out" in footer


def test_format_attribution_footer_collapses_when_tokens_missing() -> None:
    """vcv2-followup: when the JSON envelope didn't yield token counts
    (parse failure, model variant that omits usage), the footer still
    surfaces the model attribution rather than rendering an awkward
    half-empty token segment."""
    from chronicler.narrative.claude_code import _format_attribution_footer

    footer = _format_attribution_footer(
        model="claude-opus-4-7[1m]",
        input_tokens=None,
        output_tokens=None,
    )
    assert "claude-opus-4-7[1m]" in footer
    # No '·' between model and missing tokens — segment dropped cleanly.
    assert " in " not in footer
    assert " out" not in footer


@pytest.mark.asyncio
async def test_generate_appends_attribution_footer_to_biography_file(
    tmp_path: Path, monkeypatch
) -> None:
    """vcv2-followup end-to-end: the on-disk .md ends with the
    attribution line. Body in NarrativeResponse stays bare prose
    (the DB column doesn't double-store the footer)."""
    prose = _make_prose_repo(tmp_path)

    # Realistic envelope shape from the 2026-05-08 smoke session, with
    # the prose riding in `result` (lean shape).
    envelope = json.dumps(
        {
            "type": "result",
            "result": "Bare biography prose.",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 5000,
                "cache_read_input_tokens": 200,
                "output_tokens": 850,
            },
            "modelUsage": {"claude-opus-4-7[1m]": {"input_tokens": 100}},
        }
    ).encode("utf-8")

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(envelope, b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose, model="claude-opus-4-7[1m]")
    response = await provider.generate(_request())

    # NarrativeResponse.text stays bare prose — the footer is on disk only.
    assert response.text == "Bare biography prose."

    # The .md file gained the attribution footer, with thousand-
    # separated token counts (5,300 in = 100 marginal + 5,000 cache_create
    # + 200 cache_read; 850 out).
    bio_path = prose / "biographies" / "abc-123" / "12345-v1.md"
    written = bio_path.read_text(encoding="utf-8")
    assert written.startswith("Bare biography prose.\n")
    assert "Set down by Claude Code" in written
    assert "claude-opus-4-7[1m]" in written
    assert "5,300 in" in written
    assert "850 out" in written


# --- ck3_chronicler-cj2f: auto-commit after biography write ---


@pytest.mark.asyncio
async def test_generate_runs_git_add_and_commit_after_biography_write(
    tmp_path: Path, monkeypatch
) -> None:
    """cj2f: ClaudeCodeProvider stages + commits the briefing/biography
    pair after claude --print completes. The prose-repo CLAUDE.md tells
    Claude Code NOT to commit itself ('the chronicler tool commits');
    this provider is what makes that promise true. The git invocations
    surface as additional asyncio.create_subprocess_exec calls — pin
    that they happen and that the message references the character."""
    prose = _make_prose_repo(tmp_path)
    invocations: list[list[str]] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        invocations.append(list(argv))
        if argv and argv[0] == "git":
            # diff --cached --quiet returns 1 ('changes staged') so commit fires.
            return _git_ok_proc(argv)
        # claude --print: return the prose in the envelope `result`.
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose, model="claude-opus-4-7[1m]")
    await provider.generate(_request())

    # Three git invocations after the claude --print: add, diff, commit.
    git_calls = [inv for inv in invocations if inv and inv[0] == "git"]
    assert any("add" in inv for inv in git_calls), git_calls
    assert any("diff" in inv for inv in git_calls), git_calls
    commit_call = next((inv for inv in git_calls if "commit" in inv), None)
    assert commit_call is not None
    # Commit message names the character + version + model so the prose-
    # repo log carries the per-bio audit trail without needing the diff.
    msg_idx = commit_call.index("-m")
    msg = commit_call[msg_idx + 1]
    assert "12345" in msg
    assert "v1" in msg
    assert "claude-opus-4-7[1m]" in msg
    # Both artifacts staged + committed via --only.
    assert "briefings/abc-123/12345-v1.md" in commit_call
    assert "biographies/abc-123/12345-v1.md" in commit_call


@pytest.mark.asyncio
async def test_generate_skips_commit_when_diff_quiet_reports_no_changes(
    tmp_path: Path, monkeypatch
) -> None:
    """cj2f: idempotence — if `git diff --cached --quiet` exits 0 (no
    changes staged), the commit step is skipped. Repeat-runs against
    unchanged content (rare, but possible if claude regenerates byte-
    identical output) don't pollute the log with empty commits."""
    prose = _make_prose_repo(tmp_path)
    invocations: list[list[str]] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        invocations.append(list(argv))
        if argv and argv[0] == "git":
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            # diff --cached --quiet returns 0 ('clean') — commit MUST be skipped.
            proc.returncode = 0
            proc.kill = lambda: None
            proc.wait = AsyncMock(return_value=0)
            return proc
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    response = await provider.generate(_request())
    assert response.text == "body"

    # add + diff fire; commit does not.
    git_calls = [inv for inv in invocations if inv and inv[0] == "git"]
    assert any("add" in inv for inv in git_calls)
    assert any("diff" in inv for inv in git_calls)
    assert not any("commit" in inv for inv in git_calls)


@pytest.mark.asyncio
async def test_generate_swallows_git_failures_so_biography_still_returns(
    tmp_path: Path, monkeypatch
) -> None:
    """cj2f: a git failure (not a repo, FileNotFoundError, etc) MUST
    NOT propagate — the biography is already on disk and must reach
    the caller. The provider's error contract treats any exception
    from generate() as a wiped response, so failing here would lose
    real LLM work over a missing git binary or a non-repo prose dir."""
    prose = _make_prose_repo(tmp_path)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        if argv and argv[0] == "git":
            # Simulate `git` not on PATH.
            raise FileNotFoundError(2, "no such file", "git")
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("biography body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    response = await provider.generate(_request())
    assert response.text == "biography body"


# --- Provider contract ---


def test_provider_name_includes_model() -> None:
    p = ClaudeCodeProvider(prose_repo_path=Path("/tmp/dummy"), model="claude-opus-4-7[1m]")
    assert p.name == "claude-code:claude-opus-4-7[1m]"


def test_provider_uses_env_overrides(monkeypatch, tmp_path: Path) -> None:
    """CHRONICLER_PROSE_REPO_PATH + CHRONICLER_CLAUDE_CODE_MODEL +
    CHRONICLER_CLAUDE_CODE_BIN are read at construction when the
    explicit kwargs are absent. Lets ops override without code changes."""
    monkeypatch.setenv("CHRONICLER_PROSE_REPO_PATH", str(tmp_path))
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIN", "claude-canary")

    from chronicler.narrative.claude_code import make_claude_code_provider

    p = make_claude_code_provider()
    assert p.prose_repo_path == tmp_path
    assert p.name == "claude-code:claude-sonnet-4-6"
    # _bin is private but the model name confirms env wiring works.


# --- ck3_chronicler-5d9o: per-kind model routing ---


def test_model_for_kind_defaults_biography_and_closing_to_opus(monkeypatch) -> None:
    """No env set: biography + biography_woven + chronicle_export all
    route to Opus (narrative craft)."""
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", raising=False)

    from chronicler.narrative.claude_code import (
        DEFAULT_BIO_MODEL,
        DEFAULT_CLOSING_MODEL,
        _model_for_kind,
    )

    assert _model_for_kind("biography") == DEFAULT_BIO_MODEL
    assert _model_for_kind("biography_woven") == DEFAULT_BIO_MODEL
    assert _model_for_kind("chronicle_export") == DEFAULT_CLOSING_MODEL


def test_model_for_kind_per_kind_env_overrides(monkeypatch) -> None:
    """Per-kind env vars override their kind's default but do NOT leak
    across kinds. Lets ops tune one kind at a time without touching the
    others."""
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", "claude-sonnet-4-6")
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", raising=False)

    from chronicler.narrative.claude_code import (
        DEFAULT_CLOSING_MODEL,
        _model_for_kind,
    )

    assert _model_for_kind("biography") == "claude-sonnet-4-6"
    assert _model_for_kind("chronicle_export") == DEFAULT_CLOSING_MODEL


def test_model_for_kind_legacy_global_env_wins_over_per_kind(monkeypatch) -> None:
    """CHRONICLER_CLAUDE_CODE_MODEL is the legacy single-model knob. When
    set, it MUST resolve every kind to its value — even if per-kind envs
    are also set. This preserves pre-5d9o caller expectations: scripts
    that pin a model via the legacy var still get a single-model provider."""
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-opus-4-7[1m]")
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", "claude-sonnet-4-6")

    from chronicler.narrative.claude_code import _model_for_kind

    assert _model_for_kind("biography") == "claude-opus-4-7[1m]"
    assert _model_for_kind("biography_woven") == "claude-opus-4-7[1m]"
    assert _model_for_kind("chronicle_export") == "claude-opus-4-7[1m]"


def test_resolved_models_snapshot_includes_global_override_state(monkeypatch) -> None:
    """The Settings 'Models' panel reads this snapshot. global_override is
    None when no legacy env is set, and carries the legacy tag when it is —
    so the FE can render 'override: <tag>' alongside the per-kind rows."""
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", raising=False)

    from chronicler.narrative.claude_code import (
        DEFAULT_BIO_MODEL,
        DEFAULT_CLOSING_MODEL,
    )
    from chronicler.narrative.model_resolution import resolved_models

    snap = resolved_models()
    assert snap == {
        "biography": DEFAULT_BIO_MODEL,
        "closing": DEFAULT_CLOSING_MODEL,
        "global_override": None,
    }

    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-opus-4-7[1m]")
    snap = resolved_models()
    assert snap == {
        "biography": "claude-opus-4-7[1m]",
        "closing": "claude-opus-4-7[1m]",
        "global_override": "claude-opus-4-7[1m]",
    }


def test_provider_constructor_model_kwarg_overrides_all_kinds() -> None:
    """Explicit ``model=`` kwarg behaves like CHRONICLER_CLAUDE_CODE_MODEL:
    every kind on this provider instance resolves to it. Preserves the
    pre-5d9o constructor contract."""
    p = ClaudeCodeProvider(prose_repo_path=Path("/tmp/dummy"), model="claude-haiku-4-5")
    assert p.name == "claude-code:claude-haiku-4-5"
    snap = p.resolved_models()
    assert snap == {
        "biography": "claude-haiku-4-5",
        "closing": "claude-haiku-4-5",
        "global_override": "claude-haiku-4-5",
    }


def test_provider_resolved_models_reflects_env_defaults_when_no_override(
    monkeypatch,
) -> None:
    """Without an explicit kwarg, ``resolved_models`` mirrors the
    module-level snapshot (env + per-kind defaults). Same source of truth
    the Settings endpoint reads."""
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", raising=False)

    p = ClaudeCodeProvider(prose_repo_path=Path("/tmp/dummy"))
    snap = p.resolved_models()
    # ck3_chronicler-cs1o: defaults are the BASE model — the [1m]
    # long-context variant is applied per-call by prompt size now.
    assert snap["biography"] == "claude-opus-4-7"
    assert snap["closing"] == "claude-opus-4-7"
    assert snap["global_override"] is None
    assert "memory" not in snap
    # provider.name still reports the biography resolution — cost dashboard's
    # provider key stays stable when no env overrides are set.
    assert p.name == "claude-code:claude-opus-4-7"


@pytest.mark.asyncio
async def test_generate_passes_kind_specific_model_to_claude_argv(
    tmp_path: Path, monkeypatch
) -> None:
    """The load-bearing assertion for 5d9o: per-kind model resolution
    propagates through to ``claude --model`` argv. biography uses Opus;
    chronicle_export routes to the closing model (also Opus by default)."""
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", "claude-sonnet-4-6")
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", raising=False)
    prose = _make_prose_repo(tmp_path)
    captured_argv: list[list[str]] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured_argv.append(list(argv))
        if argv and argv[0] == "git":
            return _git_ok_proc(argv)
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(_result_envelope("body"), b""))
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock(return_value=0)
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)

    # Chronicle export → Sonnet (overridden via env).
    closing_req = NarrativeRequest(
        kind="chronicle_export",
        prompt_version="chronicle_export_v1",
        system_prompt="(legacy)",
        user_prompt="(briefing payload)",
        metadata={"character_id": "12345", "campaign_uuid": "abc-123"},
    )
    await provider.generate(closing_req)

    # Biography → Opus.
    await provider.generate(_request())

    # Locate the claude invocations (ignore git auto-commit subprocesses).
    claude_calls = [argv for argv in captured_argv if argv and argv[0] != "git"]
    assert len(claude_calls) == 2, (
        f"expected 2 claude invocations, got {len(claude_calls)}: {claude_calls}"
    )
    closing_argv, bio_argv = claude_calls

    closing_model_idx = closing_argv.index("--model")
    assert closing_argv[closing_model_idx + 1] == "claude-sonnet-4-6"

    bio_model_idx = bio_argv.index("--model")
    # cs1o: small test briefing → base Opus tag, no [1m] (long-context
    # engages per-call only above the 180k-token estimate threshold).
    assert bio_argv[bio_model_idx + 1] == "claude-opus-4-7"


# --- ck3_chronicler-27ov.31 (audit M-T5): timeout-kill + cancel-kill ---
# A hanging claude subprocess is one of the most likely production
# failures and exactly what wedges the narrative queue. These tests
# drive the real _run_subprocess branches with a proc whose
# communicate() never returns.


# Bound at import time — BEFORE any test monkeypatches the tempfile module
# attribute — so the spy below constructs the genuine class, not itself (the
# provider module and this module share the one tempfile module object).
_REAL_TEMPORARY_DIRECTORY = _tempfile_for_spy.TemporaryDirectory


class _SpyTempDir:
    """Wraps the real TemporaryDirectory so tests can assert the
    throwaway lean cwd is cleaned up on every exit path (l7rk)."""

    instances: list[_SpyTempDir] = []

    def __init__(self, *args, **kwargs) -> None:
        self._real = _REAL_TEMPORARY_DIRECTORY(*args, **kwargs)
        self.name = self._real.name
        self.cleaned = False
        _SpyTempDir.instances.append(self)

    # Issue #19 moved the lean cwd to a `with` block in generate() — it
    # now also holds the system-prompt file, so one scope owns both.
    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        self.cleaned = True
        self._real.cleanup()


def _hanging_proc() -> tuple[AsyncMock, asyncio.Event, list[bool]]:
    """A fake claude proc whose communicate() blocks forever. Returns
    (proc, started-event, kill-call-log)."""
    started = asyncio.Event()
    killed: list[bool] = []

    async def hang(input=None):  # noqa: ANN001 — mirrors communicate()
        started.set()
        await asyncio.Event().wait()  # never set — hangs until killed

    proc = AsyncMock()
    proc.communicate = hang
    proc.returncode = None
    proc.kill = lambda: killed.append(True)
    proc.wait = AsyncMock(return_value=0)
    return proc, started, killed


async def test_generate_timeout_kills_subprocess_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TimeoutError branch: wait_for trips, proc is killed + reaped,
    RuntimeError names the timeout, and the lean cwd is cleaned up."""
    prose = _make_prose_repo(tmp_path)
    proc, _started, killed = _hanging_proc()

    async def fake_create_subprocess_exec(*argv, **kwargs):
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    _SpyTempDir.instances = []
    monkeypatch.setattr(
        "chronicler.narrative.claude_code.tempfile.TemporaryDirectory",
        _SpyTempDir,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose, request_timeout=0.01)
    with pytest.raises(RuntimeError, match=r"timed out after 0\.01s"):
        await provider.generate(_request())

    assert killed == [True]
    proc.wait.assert_awaited()
    assert len(_SpyTempDir.instances) == 1
    assert _SpyTempDir.instances[0].cleaned is True
    # Nothing persisted: the bio file must not exist for a timed-out call.
    assert not list((prose / "biographies").rglob("*.md"))


async def test_generate_cancel_kills_subprocess_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CancelledError branch (c5wq): the queue-page Cancel button
    cancels the generate task; the still-running proc is killed and the
    cancellation propagates so the scheduler can mark the item
    failed("cancelled"). Lean cwd cleaned up on this path too."""
    prose = _make_prose_repo(tmp_path)
    proc, started, killed = _hanging_proc()

    async def fake_create_subprocess_exec(*argv, **kwargs):
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    _SpyTempDir.instances = []
    monkeypatch.setattr(
        "chronicler.narrative.claude_code.tempfile.TemporaryDirectory",
        _SpyTempDir,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)  # default timeout
    task = asyncio.create_task(provider.generate(_request()))
    # Bounded wait: if generate() dies before reaching communicate(),
    # fail loudly instead of deadlocking the test on started.wait().
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert killed == [True]
    proc.wait.assert_awaited()
    assert len(_SpyTempDir.instances) == 1
    assert _SpyTempDir.instances[0].cleaned is True


async def test_generate_cancel_after_exit_does_not_double_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cancel branch only kills when returncode is None — a proc
    that already exited must not be killed again."""
    prose = _make_prose_repo(tmp_path)
    started = asyncio.Event()
    killed: list[bool] = []

    async def hang(input=None):  # noqa: ANN001
        started.set()
        # Simulate the process exiting on its own just before the
        # cancellation lands.
        proc.returncode = 0
        await asyncio.Event().wait()

    proc = AsyncMock()
    proc.communicate = hang
    proc.returncode = None
    proc.kill = lambda: killed.append(True)
    proc.wait = AsyncMock(return_value=0)

    async def fake_create_subprocess_exec(*argv, **kwargs):
        return proc

    monkeypatch.setattr(
        "chronicler.narrative.claude_code.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = ClaudeCodeProvider(prose_repo_path=prose)
    task = asyncio.create_task(provider.generate(_request()))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert killed == []
