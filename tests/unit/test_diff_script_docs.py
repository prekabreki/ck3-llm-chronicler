"""Tests for scripts/diff_script_docs.py.

Validates the static analysis (extraction of hooks and getters from
the mod source) and the diff logic (regression flagging on a
fixture pair of script_docs dumps).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load() -> object:
    spec = importlib.util.spec_from_file_location(
        "diff_script_docs",
        Path(__file__).resolve().parents[2] / "scripts" / "diff_script_docs.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["diff_script_docs"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load()


def test_collect_mod_usage_finds_on_death(mod) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    mod_root = repo_root / "mod" / "chronicler"
    usage = mod.collect_mod_usage(mod_root)
    assert "on_death" in usage.on_actions
    # We don't strictly require these specific getters by name, only that
    # *some* are discovered from our scripted_effects.
    assert usage.getters  # non-empty


def test_collect_mod_usage_handles_missing_dirs(mod, tmp_path: Path) -> None:
    usage = mod.collect_mod_usage(tmp_path)  # empty dir
    assert usage.on_actions == set()
    assert usage.getters == set()


def _write_fixture(dirpath: Path, **files: str) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (dirpath / name).write_text(content, encoding="utf-8")


def test_check_on_actions_flags_removed_hooks(mod, tmp_path: Path) -> None:
    base = tmp_path / "base"
    new = tmp_path / "new"
    _write_fixture(base, **{"on_actions.info": "on_death\non_marriage\n"})
    _write_fixture(new, **{"on_actions.info": "on_marriage\n"})  # on_death gone

    statuses = mod.check_on_actions(base, new, {"on_death", "on_marriage"})
    by_name = {s.name: s for s in statuses}
    assert by_name["on_death"].in_base is True
    assert by_name["on_death"].in_new is False
    assert by_name["on_marriage"].in_base is True
    assert by_name["on_marriage"].in_new is True


def test_check_getters_against_all_dumps(mod, tmp_path: Path) -> None:
    base = tmp_path / "base"
    new = tmp_path / "new"
    _write_fixture(base, **{"event_targets.log": "GetID\nGetGameStartDate\n"})
    _write_fixture(new, **{"event_targets.log": "GetGameStartDate\n"})  # GetID removed

    statuses = mod.check_getters(base, new, {"GetID", "GetGameStartDate"})
    by_name = {s.name: s for s in statuses}
    assert by_name["GetID"].in_base is True
    assert by_name["GetID"].in_new is False
    assert by_name["GetGameStartDate"].in_new is True


def test_main_returns_nonzero_when_hook_removed(
    mod, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    base = tmp_path / "base"
    new = tmp_path / "new"
    fake_mod = tmp_path / "fakemod"

    _write_fixture(base, **{"on_actions.info": "on_death\non_marriage\n"})
    _write_fixture(new, **{"on_actions.info": "on_marriage\n"})

    on_action_dir = fake_mod / "common" / "on_action"
    on_action_dir.mkdir(parents=True)
    (on_action_dir / "chronicler.txt").write_text("on_death = {\n  on_actions = { x }\n}\n")

    rc = mod.main(
        [
            "--base",
            str(base),
            "--new",
            str(new),
            "--mod",
            str(fake_mod),
        ]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "on_death" in captured.out
    assert "REMOVED" in captured.out


def test_main_returns_zero_when_clean(mod, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    base = tmp_path / "base"
    new = tmp_path / "new"
    fake_mod = tmp_path / "fakemod"

    content = "on_death\n"
    _write_fixture(base, **{"on_actions.info": content})
    _write_fixture(new, **{"on_actions.info": content})

    on_action_dir = fake_mod / "common" / "on_action"
    on_action_dir.mkdir(parents=True)
    (on_action_dir / "chronicler.txt").write_text("on_death = {\n}\n")

    rc = mod.main(
        [
            "--base",
            str(base),
            "--new",
            str(new),
            "--mod",
            str(fake_mod),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "no regressions" in captured.out
