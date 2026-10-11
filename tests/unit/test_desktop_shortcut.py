"""Unit tests for :mod:`chronicler.desktop_shortcut` (issue #10).

The module writes into the user's Desktop and shells out to PowerShell /
``gio`` / ``xdg-user-dir``, so every test mocks the platform, the
filesystem, and subprocess — nothing here runs a real PowerShell command
or touches a real Desktop.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

import chronicler.desktop_shortcut as ds


def _make_fake_ck3_install(tmp_path: Path) -> Path:
    """A fake CK3 root carrying a real (Pillow-written) exe_icon.bmp."""
    from PIL import Image

    install = tmp_path / "ck3"
    gfx = install / "game" / "gfx"
    gfx.mkdir(parents=True)
    Image.new("RGBA", (16, 16), (134, 59, 255, 255)).save(gfx / "exe_icon.bmp", format="BMP")
    return install


@pytest.fixture
def linux_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Platform=linux, a fake install, and the data dir pinned to tmp_path."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    install = _make_fake_ck3_install(tmp_path)
    monkeypatch.setenv("CHRONICLER_CK3_INSTALL_DIR", str(install))
    return install


def test_linux_writes_desktop_file_with_correct_contents(
    tmp_path: Path, linux_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Linux path writes a valid .desktop to the Desktop + applications
    dirs, with the Exec / Icon / Categories the issue specifies, and marks
    it executable."""
    desktop_dir = tmp_path / "Desktop"
    apps_dir = tmp_path / "apps"
    gio_calls: list[Path] = []

    monkeypatch.setattr(ds, "_linux_desktop_dir", lambda: desktop_dir)
    monkeypatch.setattr(ds, "_linux_applications_dir", lambda: apps_dir)
    monkeypatch.setattr(ds, "_gio_set_trusted", gio_calls.append)

    result = ds.create_desktop_shortcut()

    assert result.platform == "linux"
    assert result.path == str(desktop_dir / "ck3-chronicler.desktop")

    icon = tmp_path / "ck3-chronicler.png"
    assert icon.is_file()

    desktop_file = desktop_dir / "ck3-chronicler.desktop"
    content = desktop_file.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Type=Application" in content
    assert "Name=CK3 Chronicler" in content
    assert "Exec=/usr/bin/python3 -m chronicler launch" in content
    assert f"Icon={icon}" in content
    assert "Categories=Game;" in content

    # Same file lands in the applications dir, honouring XDG_DATA_HOME.
    assert (apps_dir / "ck3-chronicler.desktop").read_text(encoding="utf-8") == content

    # Executable, and gio-set-trusted was called on the Desktop copy.
    assert desktop_file.stat().st_mode & 0o777 == 0o755
    assert gio_calls == [desktop_file]


def test_linux_quotes_exec_when_the_python_path_has_spaces(
    tmp_path: Path, linux_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A python at ``/opt/venv with space/bin/python`` must be quoted in
    ``Exec=`` or the .desktop entry splits the path on the space."""
    monkeypatch.setattr(sys, "executable", "/opt/venv with space/bin/python")
    desktop_dir = tmp_path / "Desktop"
    monkeypatch.setattr(ds, "_linux_desktop_dir", lambda: desktop_dir)
    monkeypatch.setattr(ds, "_linux_applications_dir", lambda: tmp_path / "apps")
    monkeypatch.setattr(ds, "_gio_set_trusted", lambda path: None)

    ds.create_desktop_shortcut()

    content = (desktop_dir / "ck3-chronicler.desktop").read_text(encoding="utf-8")
    assert 'Exec="/opt/venv with space/bin/python" -m chronicler launch' in content


def test_linux_desktop_dir_falls_back_to_home_when_xdg_user_dir_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``xdg-user-dir`` (or it errors) → ``~/Desktop``."""
    monkeypatch.setattr(
        ds.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", ""),
    )
    assert ds._linux_desktop_dir() == Path.home() / "Desktop"


def test_unsupported_platform_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS (and anything else) raises UnsupportedPlatformError, never
    writes a shortcut."""
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ds.UnsupportedPlatformError):
        ds.create_desktop_shortcut()


def test_no_ck3_install_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No resolvable CK3 install → NoCk3InstallError, and nothing written."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CHRONICLER_CK3_INSTALL_DIR", raising=False)
    monkeypatch.setattr(
        "chronicler.heraldry.extractor.find_ck3_install", lambda override=None: None
    )

    with pytest.raises(ds.NoCk3InstallError):
        ds.create_desktop_shortcut()

    # Nothing landed in the data dir.
    assert list(tmp_path.iterdir()) == []


def test_windows_lnk_via_encoded_command_without_path_interpolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows path shells out to PowerShell over -EncodedCommand with
    every path in the environment — never interpolated into the script —
    so PS 5.1's embedded-quote dropping cannot corrupt the .lnk."""
    from PIL import Image

    install = tmp_path / "ck3"
    (install / "game" / "gfx").mkdir(parents=True)
    Image.new("RGBA", (16, 16), (134, 59, 255, 255)).save(
        install / "game" / "gfx" / "exe_icon.bmp", format="BMP"
    )

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", r"C:\Python with spaces\python.exe")
    monkeypatch.setattr(ds, "Path", PureWindowsPath)
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHRONICLER_CK3_INSTALL_DIR", str(install))

    calls: list[list[str]] = []
    captured_env: dict[str, str] = {}

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "GetFolderPath" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "C:\\Users\\test\\Desktop\n", "")
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ds.subprocess, "run", fake_run)

    result = ds.create_desktop_shortcut()

    assert result.platform == "windows"
    assert result.path == r"C:\Users\test\Desktop\CK3 Chronicler.lnk"

    # Exactly two subprocess calls: resolve Desktop, then create the .lnk.
    assert len(calls) == 2
    create_argv = calls[1]
    assert create_argv[0] == "powershell"
    assert create_argv[3] == "-EncodedCommand"

    # The script is a fixed constant; the target path must NOT appear in it.
    script = base64.b64decode(create_argv[4]).decode("utf-16-le")
    assert r"C:\Python with spaces\pythonw.exe" not in script
    assert "$env:CHRONICLER_LNK_TARGET" in script
    assert "$env:CHRONICLER_LNK_ARGS" in script

    # Paths travel via env, so the spaces survive PS 5.1's quoting trap.
    assert captured_env["CHRONICLER_LNK_TARGET"] == r"C:\Python with spaces\pythonw.exe"
    assert captured_env["CHRONICLER_LNK_ARGS"] == "-m chronicler launch"
    assert captured_env["CHRONICLER_LNK_PATH"] == r"C:\Users\test\Desktop\CK3 Chronicler.lnk"
    assert captured_env["CHRONICLER_LNK_ICON"].endswith(",0")
    assert captured_env["CHRONICLER_LNK_CWD"] == r"C:\Python with spaces"


def test_overwrite_in_place_never_creates_a_duplicate(
    tmp_path: Path, linux_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-clicking lands on the same path and leaves exactly one shortcut."""
    desktop_dir = tmp_path / "Desktop"
    monkeypatch.setattr(ds, "_linux_desktop_dir", lambda: desktop_dir)
    monkeypatch.setattr(ds, "_linux_applications_dir", lambda: tmp_path / "apps")
    monkeypatch.setattr(ds, "_gio_set_trusted", lambda path: None)

    first = ds.create_desktop_shortcut()
    second = ds.create_desktop_shortcut()

    assert first.path == second.path == str(desktop_dir / "ck3-chronicler.desktop")
    assert [p.name for p in desktop_dir.iterdir()] == ["ck3-chronicler.desktop"]
