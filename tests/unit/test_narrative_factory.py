"""ck3_chronicler-cs1o: transport selection via CHRONICLER_NARRATIVE_BACKEND."""

from __future__ import annotations

import pytest

from chronicler.narrative.anthropic import AnthropicProvider
from chronicler.narrative.claude_code import ClaudeCodeProvider
from chronicler.narrative.factory import (
    CHRONICLER_NARRATIVE_BACKEND_ENV,
    make_narrative_provider,
    resolve_backend,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(CHRONICLER_NARRATIVE_BACKEND_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_default_backend_is_claude_code():
    assert resolve_backend() == "claude-code"
    assert isinstance(make_narrative_provider(), ClaudeCodeProvider)


def test_explicit_claude_code(monkeypatch):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "claude-code")
    assert isinstance(make_narrative_provider(), ClaudeCodeProvider)


def test_anthropic_backend_with_key(monkeypatch, tmp_path):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = make_narrative_provider(prose_repo_path=tmp_path)
    assert isinstance(p, AnthropicProvider)
    assert p.prose_repo_path == tmp_path


def test_backend_value_is_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "  Anthropic ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(make_narrative_provider(prose_repo_path=tmp_path), AnthropicProvider)


def test_anthropic_backend_without_key_fails_loud(monkeypatch):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "anthropic")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        make_narrative_provider()


def test_unknown_backend_fails_loud(monkeypatch):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "ollama")
    with pytest.raises(ValueError, match="ollama"):
        make_narrative_provider()


def test_model_kwarg_passes_through_both_backends(monkeypatch, tmp_path):
    p = make_narrative_provider(model="claude-haiku-4-5")
    assert isinstance(p, ClaudeCodeProvider)
    assert p.name == "claude-code:claude-haiku-4-5"

    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p2 = make_narrative_provider(prose_repo_path=tmp_path, model="claude-haiku-4-5")
    assert p2.name == "anthropic:claude-haiku-4-5"


# --- issue #45: registry, settings-first precedence, third backend ---


def _write_settings(payload: dict) -> None:
    """Write the isolated settings file conftest points the store at."""
    from chronicler.settings_store import save_settings

    save_settings(payload)


def test_registry_covers_all_three_backends_and_names_them_in_errors(monkeypatch):
    """The valid-set message derives from the registry, so it cannot drift."""
    from chronicler.narrative.factory import known_backends

    assert known_backends() == ("anthropic", "claude-code", "openai-compatible")

    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "gpt5")
    with pytest.raises(ValueError) as exc:
        make_narrative_provider()
    for backend in known_backends():
        assert backend in str(exc.value)


def test_openai_compatible_is_reachable_through_the_factory(monkeypatch, tmp_path):
    """Regression for the #45 headline bug: resolve_backend() returned
    'openai-compatible' and doctor probed it, but the factory had no branch
    and raised 'unknown backend'."""
    from chronicler.narrative.openai_compatible import OpenAICompatibleProvider

    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "llama3")
    provider = make_narrative_provider(prose_repo_path=tmp_path)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == "ollama:llama3"
    assert provider.max_concurrent == 1  # local preset stays serial


def test_openai_compatible_without_model_fails_loud(monkeypatch):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.delenv("CHRONICLER_OPENAI_MODEL", raising=False)
    with pytest.raises(ValueError, match="CHRONICLER_OPENAI_MODEL"):
        make_narrative_provider()


def test_paid_preset_without_key_fails_loud(monkeypatch):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "deepseek")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "deepseek-chat")
    monkeypatch.delenv("CHRONICLER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="requires an API key"):
        make_narrative_provider()


# --- settings-vs-env precedence matrix (the #45 danger zone) ---


def test_backend_precedence_settings_only(monkeypatch, tmp_path):
    _write_settings({"narrative_backend": "anthropic", "anthropic_api_key": "sk-from-settings"})
    assert resolve_backend() == "anthropic"
    provider = make_narrative_provider(prose_repo_path=tmp_path)
    assert provider.name.startswith("anthropic:")


def test_backend_precedence_env_only(monkeypatch, tmp_path):
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert resolve_backend() == "anthropic"
    assert make_narrative_provider(prose_repo_path=tmp_path).name.startswith("anthropic:")


def test_backend_precedence_settings_beats_env(monkeypatch):
    """The repo-wide convention: a persisted choice is not downgraded by a
    stale export in whatever shell launched the app."""
    _write_settings({"narrative_backend": "claude-code"})
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "anthropic")
    assert resolve_backend() == "claude-code"
    assert isinstance(make_narrative_provider(), ClaudeCodeProvider)


def test_backend_precedence_neither_set_is_the_default():
    assert resolve_backend() == "claude-code"


def test_api_key_precedence_settings_beats_env(monkeypatch, tmp_path):
    from chronicler.narrative.anthropic import get_anthropic_api_key

    _write_settings({"anthropic_api_key": "sk-from-settings"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    assert get_anthropic_api_key() == "sk-from-settings"


def test_openai_config_precedence_settings_beats_env(monkeypatch, tmp_path):
    _write_settings(
        {
            "narrative_backend": "openai-compatible",
            "openai_preset": "lmstudio",
            "openai_model": "qwen3",
        }
    )
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "llama3")
    provider = make_narrative_provider(prose_repo_path=tmp_path)
    assert provider.name == "lmstudio:qwen3"


def test_corrupt_settings_file_falls_through_to_env(monkeypatch, tmp_path):
    """settings_store's contract: settings are an enhancement, never
    load-bearing. A corrupt file must not break provider construction."""
    from chronicler.settings_store import DEFAULT_SETTINGS_PATH

    DEFAULT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_SETTINGS_PATH.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setenv(CHRONICLER_NARRATIVE_BACKEND_ENV, "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert resolve_backend() == "anthropic"
    assert make_narrative_provider(prose_repo_path=tmp_path).name.startswith("anthropic:")


def test_provider_name_maps_back_to_its_backend():
    """/provider-status derives its mode from this, and every
    openai-compatible tag used to fall through to 'other'."""
    from chronicler.narrative.backend_config import backend_for_provider_name

    assert backend_for_provider_name("claude-code:claude-opus-4-7[1m]") == "claude-code"
    assert backend_for_provider_name("anthropic:claude-opus-4-7") == "anthropic"
    assert backend_for_provider_name("ollama:llama3") == "openai-compatible"
    assert backend_for_provider_name("openai:gpt-5") == "openai-compatible"
    assert backend_for_provider_name("openai-compatible:whatever") == "openai-compatible"
    assert backend_for_provider_name("fake-double") == "other"
    assert backend_for_provider_name("") == "other"
