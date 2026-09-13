"""Tests for scripts/install_mod.py.

We exercise install() against a fake CK3 mod folder under tmp_path and
verify the symlink + descriptor end up correct. Symlink creation is the
only OS-dependent bit; on Windows runners without developer mode it
falls back to a junction.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_install_mod():
    spec = importlib.util.spec_from_file_location(
        "install_mod",
        Path(__file__).resolve().parents[2] / "scripts" / "install_mod.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["install_mod"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def install_mod():
    return _load_install_mod()


@pytest.fixture
def staged_mod_source(install_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point install() at a throwaway mod source under tmp_path.

    install() otherwise links the CK3 mod folder at the *live* repo
    mod/chronicler. pytest keeps a few numbered tmp dirs and rmtree's the
    older ones on later runs; on Windows rmtree follows the directory
    junction and deletes the TARGET, wiping tracked mod source files out
    of the working tree (ck3_chronicler-d7vi). Staging a disposable copy
    under tmp_path means the junction only ever targets garbage.
    """
    src = tmp_path / "repo_mod" / install_mod.MOD_NAME
    src.mkdir(parents=True)
    (src / "descriptor.mod").write_text('name="Chronicler"\n', encoding="utf-8")
    monkeypatch.setattr(install_mod, "mod_source_dir", lambda: src)
    return src


def _can_symlink_or_junction(tmp_path: Path) -> bool:
    """Skip the live-symlink tests if neither symlink nor mklink works here."""
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        import os

        os.symlink(target, link, target_is_directory=True)
        link.unlink()
        return True
    except OSError:
        if sys.platform != "win32":
            return False
        import subprocess

        link2 = tmp_path / "link2"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link2), str(target)],
            capture_output=True,
        )
        return result.returncode == 0


def test_dry_run_makes_no_changes(install_mod, tmp_path: Path) -> None:
    mod_folder = tmp_path / "mod"
    mod_folder.mkdir()
    rc = install_mod.install(mod_folder=mod_folder, dry_run=True)
    assert rc == 0
    assert list(mod_folder.iterdir()) == []


def test_errors_when_mod_folder_missing(install_mod, tmp_path: Path) -> None:
    rc = install_mod.install(mod_folder=tmp_path / "does-not-exist")
    assert rc == 2


def test_descriptor_text_includes_path(install_mod, tmp_path: Path) -> None:
    text = install_mod.descriptor_text(tmp_path / "some" / "place")
    assert 'name="Chronicler"' in text
    assert "supported_version=" in text
    assert "path=" in text
    assert tmp_path.name in text


def test_full_install_creates_link_and_descriptor(
    install_mod, staged_mod_source, tmp_path: Path
) -> None:
    if not _can_symlink_or_junction(tmp_path):
        pytest.skip("symlink/junction creation not permitted in this environment")

    mod_folder = tmp_path / "mod"
    mod_folder.mkdir()

    rc = install_mod.install(mod_folder=mod_folder)
    assert rc == 0

    install_link = mod_folder / install_mod.MOD_NAME
    descriptor = mod_folder / f"{install_mod.MOD_NAME}.mod"

    assert install_link.exists()
    assert (install_link / "descriptor.mod").exists()  # follows the link
    assert descriptor.exists()
    assert 'name="Chronicler"' in descriptor.read_text(encoding="utf-8")


def test_idempotent_reinstall(install_mod, staged_mod_source, tmp_path: Path) -> None:
    if not _can_symlink_or_junction(tmp_path):
        pytest.skip("symlink/junction creation not permitted in this environment")

    mod_folder = tmp_path / "mod"
    mod_folder.mkdir()

    assert install_mod.install(mod_folder=mod_folder) == 0
    assert install_mod.install(mod_folder=mod_folder) == 0  # second run = no-op


def test_refuses_existing_non_symlink_without_force(install_mod, tmp_path: Path) -> None:
    mod_folder = tmp_path / "mod"
    mod_folder.mkdir()
    (mod_folder / install_mod.MOD_NAME).mkdir()  # real dir, not a symlink

    rc = install_mod.install(mod_folder=mod_folder)
    assert rc == 3
