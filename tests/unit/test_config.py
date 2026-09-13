"""Tests for chronicler.config — path resolution.

The token-pricing tests (rate card + compute_generation_cost) moved to
test_cost.py alongside the rate card itself when pricing was relocated
out of config.py into cost.py (ck3_chronicler-27ov.80, audit L24).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chronicler.config import (
    CHRONICLER_CK3_INSTALL_DIR_ENV,
    CHRONICLER_PROSE_REPO_PATH_ENV,
    CHRONICLER_SAVE_DIR_ENV,
    CK3_DEBUG_LOG_ENV,
    CK3_DEFAULT_LOG_PATH,
    CK3_DEFAULT_SAVE_DIR,
    default_prose_repo_path,
    get_ck3_debug_log,
    resolve_ck3_install_dir,
    resolve_prose_repo_path,
    resolve_save_dir,
)

# --- ck3_chronicler-f9w.1: path resolvers ---


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the settings store at a temp file and clear path env vars
    so resolver tests are deterministic regardless of dev shell state."""
    target = tmp_path / "settings.json"
    monkeypatch.setattr("chronicler.settings_store.DEFAULT_SETTINGS_PATH", target)
    monkeypatch.delenv(CHRONICLER_SAVE_DIR_ENV, raising=False)
    monkeypatch.delenv(CHRONICLER_CK3_INSTALL_DIR_ENV, raising=False)
    monkeypatch.delenv(CHRONICLER_PROSE_REPO_PATH_ENV, raising=False)
    return target


def test_resolve_save_dir_default_when_no_override_no_env(
    isolated_settings: Path,
) -> None:
    resolved = resolve_save_dir()
    assert resolved.value == CK3_DEFAULT_SAVE_DIR
    assert resolved.source == "default"
    # exists is whatever's true for this dev box; assert it's a bool.
    assert isinstance(resolved.exists, bool)


def test_resolve_save_dir_env_wins_over_default(
    isolated_settings: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "via_env"
    target.mkdir()
    monkeypatch.setenv(CHRONICLER_SAVE_DIR_ENV, str(target))
    resolved = resolve_save_dir()
    assert resolved.value == target
    assert resolved.source == "env"
    assert resolved.exists is True


def test_resolve_save_dir_override_wins_over_env(
    isolated_settings: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings file beats env var beats default."""
    from chronicler.settings_store import save_settings

    override = tmp_path / "via_settings"
    override.mkdir()
    save_settings({"save_dir": str(override)}, path=isolated_settings)

    env_target = tmp_path / "via_env"
    env_target.mkdir()
    monkeypatch.setenv(CHRONICLER_SAVE_DIR_ENV, str(env_target))

    resolved = resolve_save_dir()
    assert resolved.value == override
    assert resolved.source == "override"


def test_resolve_save_dir_marks_missing_override_as_not_existing(
    isolated_settings: Path, tmp_path: Path
) -> None:
    """A user-set override that points to a folder which doesn't exist
    (typo, deleted, drive unmounted) should resolve to source=override
    but exists=False so the UI can flag it."""
    from chronicler.settings_store import save_settings

    save_settings({"save_dir": str(tmp_path / "does_not_exist")}, path=isolated_settings)
    resolved = resolve_save_dir()
    assert resolved.source == "override"
    assert resolved.exists is False


def test_resolve_ck3_install_dir_override_wins(isolated_settings: Path, tmp_path: Path) -> None:
    from chronicler.settings_store import save_settings

    override = tmp_path / "ck3_root"
    override.mkdir()
    save_settings({"ck3_install_dir": str(override)}, path=isolated_settings)
    resolved = resolve_ck3_install_dir()
    assert resolved.value == override
    assert resolved.source == "override"
    assert resolved.exists is True


def test_resolve_ck3_install_dir_falls_back_to_probe_when_unconfigured(
    isolated_settings: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No override, no env → resolver delegates to find_ck3_install."""
    probed = tmp_path / "probed_root"
    probed.mkdir()

    def fake_probe(override: Path | None = None) -> Path | None:
        return probed

    monkeypatch.setattr("chronicler.heraldry.extractor.find_ck3_install", fake_probe)
    resolved = resolve_ck3_install_dir()
    assert resolved.value == probed
    assert resolved.source == "probe"
    assert resolved.exists is True


def test_resolve_ck3_install_dir_returns_none_when_probe_fails(
    isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When nothing is configured and the Steam probe misses, the
    resolver returns value=None so the UI can flag 'not found'."""

    def fake_probe(override: Path | None = None) -> Path | None:
        return None

    monkeypatch.setattr("chronicler.heraldry.extractor.find_ck3_install", fake_probe)
    resolved = resolve_ck3_install_dir()
    assert resolved.value is None
    assert resolved.source == "probe"
    assert resolved.exists is False


# --- ck3_chronicler-jnze: prose-repo path resolution ---


def test_resolve_prose_repo_path_override_wins(isolated_settings: Path, tmp_path: Path) -> None:
    from chronicler.settings_store import save_settings

    override = tmp_path / "via_settings"
    override.mkdir()
    save_settings({"prose_repo_path": str(override)}, path=isolated_settings)
    resolved = resolve_prose_repo_path()
    assert resolved.value == override
    assert resolved.source == "override"
    assert resolved.exists is True


def test_resolve_prose_repo_path_env_wins_over_default(
    isolated_settings: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "via_env"
    target.mkdir()
    monkeypatch.setenv(CHRONICLER_PROSE_REPO_PATH_ENV, str(target))
    resolved = resolve_prose_repo_path()
    assert resolved.value == target
    assert resolved.source == "env"
    assert resolved.exists is True


def test_resolve_prose_repo_path_ignores_a_sibling_checkout(
    isolated_settings: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #20 removed the sibling autodetect (ck3_chronicler-jnze).

    It resolved ``../ck3_chronicler_prose`` relative to this checkout —
    the maintainer's two-repo layout and nobody else's. Worse, it
    resolved *silently*: a public user who happened to have a
    similarly-named neighbour directory would generate against it with
    nothing in the logs. Settings and env overrides still work, which is
    how an existing install keeps its real prose directory.
    """
    fake_root = tmp_path / "ck3_chronicler"
    (fake_root / "src" / "chronicler").mkdir(parents=True)
    fake_config_file = fake_root / "src" / "chronicler" / "config.py"
    fake_config_file.write_text("# placeholder", encoding="utf-8")
    sibling = tmp_path / "ck3_chronicler_prose"
    sibling.mkdir()

    import chronicler.config as config_module

    monkeypatch.setattr(config_module, "__file__", str(fake_config_file))
    resolved = resolve_prose_repo_path()
    assert resolved.value != sibling
    assert resolved.source == "default"


def test_resolve_prose_repo_path_defaults_to_the_init_prose_target(
    isolated_settings: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The terminal default is what `chronicler init-prose` creates, under
    the platform data dir — not the old hard-coded C:/git path, which was
    wrong on every machine that isn't the maintainer's Windows one."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data"))
    resolved = resolve_prose_repo_path()
    assert resolved.value == tmp_path / "data" / "prose"
    assert resolved.value == default_prose_repo_path()
    assert resolved.source == "default"


def test_default_prose_repo_path_tracks_the_data_dir_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module-level constant would freeze whatever CHRONICLER_DATA_DIR
    was set to at import — which is why this is a function."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "one"))
    assert default_prose_repo_path() == tmp_path / "one" / "prose"
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "two"))
    assert default_prose_repo_path() == tmp_path / "two" / "prose"


# --- ck3_chronicler-27ov.80 (audit L24): get_ck3_debug_log precedence ---


def _isolate_debug_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the settings store at a temp file and clear the debug-log env
    var so the precedence tests are deterministic."""
    target = tmp_path / "settings.json"
    monkeypatch.setattr("chronicler.settings_store.DEFAULT_SETTINGS_PATH", target)
    monkeypatch.delenv(CK3_DEBUG_LOG_ENV, raising=False)
    return target


def test_get_ck3_debug_log_default_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_debug_log(tmp_path, monkeypatch)
    assert get_ck3_debug_log() == CK3_DEFAULT_LOG_PATH


def test_get_ck3_debug_log_env_wins_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_debug_log(tmp_path, monkeypatch)
    monkeypatch.setenv(CK3_DEBUG_LOG_ENV, str(tmp_path / "via_env.log"))
    assert get_ck3_debug_log() == tmp_path / "via_env.log"


def test_get_ck3_debug_log_settings_override_wins_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-27ov.80 (audit L24): the settings file must now win,
    matching every other resolver — previously this path ignored settings
    entirely, the one path the Settings layer could never override."""
    from chronicler.settings_store import save_settings

    target = _isolate_debug_log(tmp_path, monkeypatch)
    monkeypatch.setenv(CK3_DEBUG_LOG_ENV, str(tmp_path / "via_env.log"))
    save_settings({"ck3_debug_log": str(tmp_path / "via_settings.log")}, path=target)
    assert get_ck3_debug_log() == tmp_path / "via_settings.log"
