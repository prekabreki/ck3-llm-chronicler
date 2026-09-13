"""Persistent user-overrides JSON store.

Lives in the chronicler data dir (``~/Documents/chronicler`` on Windows/macOS,
``~/.local/share/chronicler`` on Linux; see :func:`chronicler.config.chronicler_data_dir`).
Centralises GUI-set
overrides (save dir, CK3 install dir, etc.) so they survive restarts
without env vars or CLI flags. ck3_chronicler-f9w.1.

The schema is intentionally loose: a flat dict where each consumer owns
its own key. Unknown keys are preserved on write so a newer chronicler
that knows more keys can co-exist with an older one. Bad JSON or a
missing file return ``{}`` — settings are an enhancement, not a
load-bearing dependency, so a corrupted file should not crash the
process.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from chronicler.config import chronicler_data_dir

log = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = chronicler_data_dir() / "settings.json"

# ck3_chronicler-27ov.80 (audit L26): serialise the read-merge-write in
# update_settings. The Settings API runs sync endpoints in FastAPI's
# threadpool, so two concurrent PATCHes can each load the same baseline,
# merge their own key, and write back — the second writer's save_settings
# clobbers the first writer's key (lost update). save_settings is already
# atomic at the filesystem level (os.replace), but atomicity of the write
# doesn't help when both writers read a stale baseline. A process-global
# lock around load+merge+save closes the window.
_update_lock = threading.Lock()


def get_settings_path() -> Path:
    """Return the canonical settings file path (no env override yet)."""
    return DEFAULT_SETTINGS_PATH


def load_settings(*, path: Path | None = None) -> dict[str, Any]:
    """Return the settings dict, or ``{}`` if missing or unparseable."""
    target = path or DEFAULT_SETTINGS_PATH
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("settings file at %s could not be read; treating as empty", target)
        return {}
    if not isinstance(data, dict):
        log.warning("settings file at %s is not a JSON object; treating as empty", target)
        return {}
    return data


def save_settings(payload: dict[str, Any], *, path: Path | None = None) -> None:
    """Write ``payload`` atomically.

    Writes to a temp file in the same directory then ``os.replace``s onto
    the target so a concurrent reader never sees a half-written file.
    """
    target = path or DEFAULT_SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(dir=target.parent, prefix=".settings.", suffix=".tmp")
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_path, target)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def update_settings(updates: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    """Merge ``updates`` into the existing settings and persist.

    Keys whose value is ``None`` are removed from the file (so the
    consumer falls back to env/default). Returns the post-merge dict.

    The whole read-merge-write runs under :data:`_update_lock` so
    concurrent threadpool requests can't drop each other's keys
    (ck3_chronicler-27ov.80, audit L26).
    """
    with _update_lock:
        current = load_settings(path=path)
        for k, v in updates.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        save_settings(current, path=path)
        return current
