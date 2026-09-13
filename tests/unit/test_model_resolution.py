"""ck3_chronicler-cs1o: backend-neutral model resolution + long-context predicate.

The narrative backend became dual-transport (claude-code | anthropic);
model choice is one shared decision. These tests pin the env-var
precedence chain and the conditional [1m]/1M-context predicate that
replaced the flat ``claude-opus-4-7[1m]`` default (which paid the 2x
long-context input surcharge on ~96% of real calls for nothing).
"""

from __future__ import annotations

import pytest

from chronicler.narrative import model_resolution as mr


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch, tmp_path):
    """Each test starts with no model envs set and an empty settings file.

    The settings store must be isolated too: model resolution reads the
    durable ``narrative_model`` keys, so without this the dev box's real
    settings.json would decide what ``test_default_is_base_opus`` sees.
    """
    for var in mr.ALL_MODEL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "chronicler.settings_store.DEFAULT_SETTINGS_PATH",
        tmp_path / "chronicler_settings.json",
    )


def test_default_is_base_opus():
    assert mr.model_for_kind("biography") == "claude-opus-4-7"
    assert mr.model_for_kind("biography_woven") == "claude-opus-4-7"
    assert mr.model_for_kind("chronicle_export") == "claude-opus-4-7"


def test_neutral_global_env_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("CHRONICLER_NARRATIVE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-haiku-4-5")
    assert mr.model_for_kind("biography") == "claude-sonnet-4-6"
    assert mr.model_for_kind("chronicle_export") == "claude-sonnet-4-6"


def test_legacy_global_env_still_honoured(monkeypatch):
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-haiku-4-5")
    assert mr.model_for_kind("chronicle_export") == "claude-haiku-4-5"


def test_global_beats_per_kind(monkeypatch):
    monkeypatch.setenv("CHRONICLER_NARRATIVE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BIO_MODEL", "claude-haiku-4-5")
    assert mr.model_for_kind("biography") == "claude-sonnet-4-6"


def test_per_kind_envs(monkeypatch):
    monkeypatch.setenv("CHRONICLER_NARRATIVE_CLOSING_MODEL", "claude-sonnet-4-6")
    assert mr.model_for_kind("chronicle_export") == "claude-sonnet-4-6"
    assert mr.model_for_kind("biography") == "claude-opus-4-7"


def test_legacy_per_kind_envs(monkeypatch):
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", "claude-sonnet-4-6")
    assert mr.model_for_kind("biography") == "claude-sonnet-4-6"
    assert mr.model_for_kind("biography_woven") == "claude-sonnet-4-6"
    assert mr.model_for_kind("chronicle_export") == "claude-opus-4-7"


def test_neutral_per_kind_beats_legacy_per_kind(monkeypatch):
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BIO_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", "claude-haiku-4-5")
    assert mr.model_for_kind("biography") == "claude-sonnet-4-6"


def test_blank_env_values_are_ignored(monkeypatch):
    monkeypatch.setenv("CHRONICLER_NARRATIVE_MODEL", "   ")
    assert mr.model_for_kind("biography") == "claude-opus-4-7"


def test_resolved_models_snapshot(monkeypatch):
    monkeypatch.setenv("CHRONICLER_NARRATIVE_CLOSING_MODEL", "claude-sonnet-4-6")
    snap = mr.resolved_models()
    assert snap == {
        "biography": "claude-opus-4-7",
        "closing": "claude-sonnet-4-6",
        "global_override": None,
    }


def test_resolved_models_reports_global_override(monkeypatch):
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-haiku-4-5")
    snap = mr.resolved_models()
    assert snap["global_override"] == "claude-haiku-4-5"


def test_estimate_prompt_tokens():
    assert mr.estimate_prompt_tokens("abcd" * 1000) == 1000
    assert mr.estimate_prompt_tokens("") == 0


def test_needs_long_context_threshold():
    assert not mr.needs_long_context(180_000)
    assert mr.needs_long_context(180_001)


def test_apply_long_context_bracket_suffix():
    assert mr.apply_long_context_bracket("claude-opus-4-7", 200_000) == "claude-opus-4-7[1m]"
    assert mr.apply_long_context_bracket("claude-opus-4-7", 10_000) == "claude-opus-4-7"
    # An explicit bracket override (user pinned [1m] themselves) is
    # never rewritten — neither stripped nor doubled.
    assert mr.apply_long_context_bracket("claude-opus-4-7[1m]", 10_000) == "claude-opus-4-7[1m]"
    assert mr.apply_long_context_bracket("claude-opus-4-7[1m]", 500_000) == "claude-opus-4-7[1m]"


@pytest.mark.asyncio
async def test_huge_briefing_engages_1m_in_claude_argv(tmp_path, monkeypatch):
    """End-to-end pin for cs1o: a briefing whose estimate exceeds the
    180k-token threshold appends [1m] to the --model argv value."""
    from unittest.mock import AsyncMock

    from chronicler.narrative.claude_code import ClaudeCodeProvider
    from chronicler.narrative.provider import NarrativeRequest

    prose = tmp_path
    (prose / "briefings").mkdir()
    (prose / "biographies").mkdir()
    (prose / "CLAUDE.md").write_text("(role override)", encoding="utf-8")
    captured_argv: list[list[str]] = []

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured_argv.append(list(argv))
        # Lean shape (27ov.14): the CLI writes nothing; the prose rides in
        # the envelope `result` and the provider persists the file itself.
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(b'{"type":"result","is_error":false,"result":"body"}', b"")
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
    req = NarrativeRequest(
        kind="biography_woven",
        prompt_version="biography_v5",
        system_prompt="(ignored)",
        # ~800k chars -> ~200k estimated tokens, over the 180k threshold.
        user_prompt="x" * 800_000,
        metadata={"character_id": "12345", "campaign_uuid": "abc-123"},
    )
    await provider.generate(req)
    claude_argv = [a for a in captured_argv if a and a[0] != "git"][0]
    model_idx = claude_argv.index("--model")
    assert claude_argv[model_idx + 1] == "claude-opus-4-7[1m]"


# --- durable settings layer -------------------------------------------------
# A model chosen for a campaign has to survive a terminal crash / restart, so
# it can't live only in the launching shell's env. These pin the settings keys
# and their precedence against the env chain above.


def _write_settings(payload: dict) -> None:
    from chronicler import settings_store

    settings_store.save_settings(payload)


def test_settings_global_sets_every_kind():
    _write_settings({"narrative_model": "claude-opus-5"})
    assert mr.model_for_kind("biography") == "claude-opus-5"
    assert mr.model_for_kind("biography_woven") == "claude-opus-5"
    assert mr.model_for_kind("chronicle_export") == "claude-opus-5"


def test_settings_global_beats_env_global(monkeypatch):
    """Repo convention (config._resolve): a persisted setting wins over the
    env, so a stale shell export can't silently downgrade a saved choice."""
    _write_settings({"narrative_model": "claude-opus-5"})
    monkeypatch.setenv("CHRONICLER_NARRATIVE_MODEL", "claude-sonnet-4-6")
    assert mr.model_for_kind("biography") == "claude-opus-5"


def test_settings_per_kind_keys():
    _write_settings(
        {
            "narrative_bio_model": "claude-opus-5",
            "narrative_closing_model": "claude-sonnet-4-6",
        }
    )
    assert mr.model_for_kind("biography") == "claude-opus-5"
    assert mr.model_for_kind("chronicle_export") == "claude-sonnet-4-6"


def test_settings_global_beats_settings_per_kind():
    """Mirrors the env tier: one global flip overrides per-kind choices."""
    _write_settings(
        {
            "narrative_model": "claude-opus-5",
            "narrative_bio_model": "claude-haiku-4-5",
        }
    )
    assert mr.model_for_kind("biography") == "claude-opus-5"


def test_settings_per_kind_beats_env_per_kind(monkeypatch):
    _write_settings({"narrative_bio_model": "claude-opus-5"})
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BIO_MODEL", "claude-sonnet-4-6")
    assert mr.model_for_kind("biography") == "claude-opus-5"


def test_env_global_still_beats_settings_per_kind(monkeypatch):
    """A per-kind setting must not outrank a global env flip — the global
    tier is resolved first regardless of where each value came from."""
    _write_settings({"narrative_bio_model": "claude-haiku-4-5"})
    monkeypatch.setenv("CHRONICLER_NARRATIVE_MODEL", "claude-sonnet-4-6")
    assert mr.model_for_kind("biography") == "claude-sonnet-4-6"


def test_blank_and_non_string_settings_are_ignored():
    """Whitespace or a wrong-typed value must fall through to the default,
    not resolve to an empty / non-string model tag."""
    _write_settings({"narrative_model": "   ", "narrative_bio_model": 42})
    assert mr.model_for_kind("biography") == mr.DEFAULT_BIO_MODEL


def test_unreadable_settings_never_fatal(monkeypatch):
    """Model resolution must not raise when the settings store blows up."""
    monkeypatch.setattr(
        "chronicler.settings_store.load_settings",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )
    assert mr.model_for_kind("biography") == mr.DEFAULT_BIO_MODEL


def test_resolved_models_reports_settings_global_override():
    """The /api/settings/models snapshot must show a settings-sourced global
    override, not just an env-sourced one."""
    _write_settings({"narrative_model": "claude-opus-5"})
    snap = mr.resolved_models()
    assert snap["biography"] == "claude-opus-5"
    assert snap["closing"] == "claude-opus-5"
    assert snap["global_override"] == "claude-opus-5"
