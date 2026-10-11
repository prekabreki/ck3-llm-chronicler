"""Desktop-shortcut creation for the Chronicler (issue #10).

Creates a desktop icon that runs ``chronicler launch`` — the verb that
starts the chronicler, opens CK3 and opens the web app (issue #9) — on
Windows and Linux.

Design rules that follow from the issue's danger zones:

- The endpoint writes into the user's Desktop, so this module may only
  ever touch its own file names (``ck3-chronicler.desktop`` on Linux,
  ``CK3 Chronicler.lnk`` on Windows). It never lists, deletes, or
  renames anything else in that directory, so re-clicking overwrites in
  place and nothing else can be clobbered.
- The Windows ``WScript.Shell`` invocation passes every user-controlled
  path through environment variables (not script-string interpolation),
  because Windows PowerShell 5.1 silently drops embedded quotes from
  native arguments and a path with spaces or apostrophes would break the
  ``.lnk`` otherwise. The script body is a fixed constant shipped over
  ``-EncodedCommand``.
- Only Python 3 + Pillow. No pywin32, no winshell.
"""

from __future__ import annotations

import base64
import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LINUX = "linux"
WINDOWS = "windows"

# The icon shipped by the game: a 512x512 32-bit BMP with alpha.
_CK3_EXE_ICON = Path("game") / "gfx" / "exe_icon.bmp"


class UnsupportedPlatformError(RuntimeError):
    """Raised on platforms with no supported shortcut (macOS, …)."""


class NoCk3InstallError(RuntimeError):
    """Raised when no CK3 install resolves, so there is no icon to draw."""


@dataclass(frozen=True)
class DesktopShortcutResult:
    """Where the shortcut landed and which platform it targets."""

    path: str
    platform: str


def _platform() -> str:
    if sys.platform.startswith("linux"):
        return LINUX
    if sys.platform == "win32":
        return WINDOWS
    return sys.platform


def _ck3_icon_source() -> Path | None:
    """Locate the game's ``exe_icon.bmp``, or ``None`` when no install."""
    from chronicler.config import resolve_ck3_install_dir

    resolved = resolve_ck3_install_dir()
    if resolved.value is None or not resolved.exists:
        return None
    icon = resolved.value / _CK3_EXE_ICON
    return icon if icon.is_file() else None


def _icon_dest(platform: str) -> Path:
    """The converted icon path under ``chronicler_data_dir()``."""
    from chronicler.config import chronicler_data_dir

    data_dir = chronicler_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    name = "ck3-chronicler.png" if platform == LINUX else "ck3-chronicler.ico"
    return data_dir / name


def _write_icon(source: Path, dest: Path, platform: str) -> None:
    """Convert the game's BMP to a PNG (Linux) or multi-size ICO (Windows)."""
    from PIL import Image

    with Image.open(source) as img:
        if platform == LINUX:
            img.save(dest, format="PNG")
        else:
            img.save(
                dest,
                format="ICO",
                sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
            )


def _quote_exec(exec_path: str) -> str:
    """Quote the executable in ``Exec=`` only when its path has spaces."""
    return f'"{exec_path}"' if any(c.isspace() for c in exec_path) else exec_path


def _desktop_entry_content(exec_path: str, icon_path: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=CK3 Chronicler\n"
        "Comment=Launch CK3 and the Chronicler\n"
        f"Exec={_quote_exec(exec_path)} -m chronicler launch\n"
        f"Icon={icon_path}\n"
        "Categories=Game;\n"
        "Terminal=false\n"
    )


def _linux_desktop_dir() -> Path:
    """The real Desktop dir via ``xdg-user-dir DESKTOP``, else ``~/Desktop``."""
    try:
        proc = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
    except OSError:
        proc = None
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    return Path.home() / "Desktop"


def _linux_applications_dir() -> Path:
    """The XDG applications dir, honouring ``XDG_DATA_HOME``."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "applications"


def _write_desktop_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _gio_set_trusted(path: Path) -> None:
    """Mark the .desktop trusted so GNOME lets double-click run it. Best-effort."""
    with contextlib.suppress(OSError):
        subprocess.run(
            ["gio", "set", str(path), "metadata::trusted", "true"],
            capture_output=True,
            check=False,
        )


def _create_linux(icon_source: Path) -> DesktopShortcutResult:
    icon_dest = _icon_dest(LINUX)
    _write_icon(icon_source, icon_dest, LINUX)

    content = _desktop_entry_content(sys.executable, icon_dest)

    desktop_dir = _linux_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = desktop_dir / "ck3-chronicler.desktop"
    _write_desktop_file(desktop_file, content)

    applications_dir = _linux_applications_dir()
    applications_dir.mkdir(parents=True, exist_ok=True)
    _write_desktop_file(applications_dir / "ck3-chronicler.desktop", content)

    _gio_set_trusted(desktop_file)

    return DesktopShortcutResult(path=str(desktop_file), platform=LINUX)


_PS_CREATE_SHORTCUT = (
    "$ws = New-Object -ComObject WScript.Shell\n"
    "$s = $ws.CreateShortcut($env:CHRONICLER_LNK_PATH)\n"
    "$s.TargetPath = $env:CHRONICLER_LNK_TARGET\n"
    "$s.Arguments = $env:CHRONICLER_LNK_ARGS\n"
    "$s.IconLocation = $env:CHRONICLER_LNK_ICON\n"
    "$s.WorkingDirectory = $env:CHRONICLER_LNK_CWD\n"
    "$s.Save()\n"
)


def _windows_desktop_dir() -> Path:
    """The real Desktop known folder (redirects possible), via PowerShell."""
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Environment]::GetFolderPath('Desktop')",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise OSError("could not resolve the Windows Desktop folder")
    return Path(proc.stdout.strip())


def _windows_powershell_invocation(
    lnk_path: Path, target: str, icon: Path
) -> tuple[list[str], dict[str, str]]:
    """Build the argv + env for the PowerShell ``WScript.Shell`` call.

    Paths travel via environment variables, so no user path is ever
    interpolated into the script string (the PS 5.1 quoting trap).
    """
    encoded = base64.b64encode(_PS_CREATE_SHORTCUT.encode("utf-16-le")).decode("ascii")
    argv = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded,
    ]
    env = {
        **os.environ,
        "CHRONICLER_LNK_PATH": str(lnk_path),
        "CHRONICLER_LNK_TARGET": target,
        "CHRONICLER_LNK_ARGS": "-m chronicler launch",
        "CHRONICLER_LNK_ICON": f"{icon},0",
        "CHRONICLER_LNK_CWD": str(Path(target).parent),
    }
    return argv, env


def _create_windows(icon_source: Path) -> DesktopShortcutResult:
    icon_dest = _icon_dest(WINDOWS)
    _write_icon(icon_source, icon_dest, WINDOWS)

    desktop_dir = _windows_desktop_dir()
    lnk_path = desktop_dir / "CK3 Chronicler.lnk"

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    argv, env = _windows_powershell_invocation(lnk_path, str(pythonw), icon_dest)
    subprocess.run(argv, env=env, capture_output=True, check=True)

    return DesktopShortcutResult(path=str(lnk_path), platform=WINDOWS)


def create_desktop_shortcut() -> DesktopShortcutResult:
    """Create the platform's shortcut, or raise a documented error.

    Raises :class:`UnsupportedPlatformError` on macOS and anything else,
    and :class:`NoCk3InstallError` when no CK3 install resolves (so there
    is no ``exe_icon.bmp`` to draw). In that case no shortcut is created:
    the chosen behaviour is to refuse rather than ship a shortcut without
    the CK3 art, and the Settings UI points the user at the Paths panel.
    """
    platform = _platform()
    if platform not in (LINUX, WINDOWS):
        raise UnsupportedPlatformError(
            f"desktop shortcuts are not supported on {platform!r} (Windows and Linux only)"
        )

    icon_source = _ck3_icon_source()
    if icon_source is None:
        raise NoCk3InstallError(
            "no CK3 install found — set the CK3 install directory in Settings "
            "(it provides the icon for the shortcut), then try again"
        )

    if platform == LINUX:
        return _create_linux(icon_source)
    return _create_windows(icon_source)
