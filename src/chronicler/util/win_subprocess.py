"""Windows console-window suppression for child processes.

On Windows, spawning a subprocess (rakaly, the ``claude`` CLI, ``git``)
flashes a black console window. During gameplay — monthly autosaves,
auto-resume draining a backlog, a biography git-commit after every death,
the closing/archive flow firing many git calls — those flashes interrupt
CK3. ``CREATE_NO_WINDOW | DETACHED_PROCESS`` plus a ``STARTUPINFO`` with
``SW_HIDE`` suppress them, including the cmd.exe flash from ``.cmd``/``.bat``
shims that ``CREATE_NO_WINDOW`` alone misses.

Single source of truth so every subprocess spawn site stays consistent and
a new one can't silently regress (ck3_chronicler-w26q). No-ops off win32.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def creationflags() -> int:
    """``creationflags`` for ``subprocess`` spawns. 0 off win32."""
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS


def startupinfo() -> Any | None:
    """Belt-and-suspenders ``STARTUPINFO`` that hides any console a child (or
    grandchild, e.g. a ``.cmd`` shim) tries to open. ``None`` off win32."""
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si
