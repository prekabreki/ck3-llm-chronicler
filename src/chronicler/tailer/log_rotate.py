"""CK3 debug.log rotation helpers (ck3_chronicler-z6jm slice 1).

Paradox's ``debug.log`` balloons to multi-GB sizes during long
playthroughs (the same pain that motivated ``scripts/rotate_debug_log.py``
historically). This module owns the file-level operations + status
checks the ``z6jm`` slice surfaces:

- :func:`stat_debug_log` — read-only size + threshold check, used by the
  HTTP status endpoint and the tailer's per-tick warning.
- :func:`archive_and_truncate` — gzip the log to ``archives/`` and
  zero-truncate the original. Used by the rotation endpoint AND the
  legacy ``scripts/rotate_debug_log.py`` CLI; logic is identical.
- :func:`reset_tail_offsets` — reset per-campaign tail_offset rows in
  the registry to 0. The tailer's :class:`Watcher` already resets its
  in-memory offset on a detected file shrink (see ``watcher.py``); this
  resets the *persisted* registry value so a chronicler restart picks
  up cleanly.

The 200 MB threshold (``DEBUG_LOG_ROTATE_THRESHOLD_BYTES``) is what
``z6jm`` calls out — empirically the size at which CK3's debug.log
starts producing noticeable I/O drag on a long playthrough. Tunable
later if smoke proves it's the wrong number.

CK3 holds the file in append mode while running. ``archive_and_truncate``
catches OSError on the truncate phase so the operation degrades to "log
copied, original still on disk" rather than crashing.
"""

from __future__ import annotations

import gzip
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from chronicler.config import get_ck3_debug_log
from chronicler.db.registry import (
    get_campaign_by_name,
    list_campaigns,
    set_tail_offset,
)

log = logging.getLogger(__name__)

# 200 MB. Above this, the chronicler raises a UI alert recommending
# rotation. Tunable post-smoke; preserved as a module-level constant so
# tests can monkeypatch it down to a small value without touching the
# threshold check.
DEBUG_LOG_ROTATE_THRESHOLD_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class DebugLogStatus:
    """Read-only snapshot of CK3's debug.log size + rotation threshold.

    ``exceeded`` is the convenience flag the UI binds to: True iff the
    log exists AND ``size_bytes > threshold_bytes``. Non-existent log
    is reported as exists=False / size=0 / exceeded=False so the
    status endpoint never 500s on a fresh install."""

    exists: bool
    size_bytes: int
    threshold_bytes: int
    exceeded: bool
    path: str


def stat_debug_log(path: Path | None = None) -> DebugLogStatus:
    """ck3_chronicler-z6jm: read-only size check on debug.log.

    Defaults to ``chronicler.config.get_ck3_debug_log()`` so the HTTP
    status endpoint doesn't need to know the path resolution rules.
    Tests pass an explicit path to avoid touching the user's real log.
    """
    actual = path if path is not None else get_ck3_debug_log()
    threshold = DEBUG_LOG_ROTATE_THRESHOLD_BYTES
    if not actual.exists():
        return DebugLogStatus(
            exists=False,
            size_bytes=0,
            threshold_bytes=threshold,
            exceeded=False,
            path=str(actual),
        )
    size = actual.stat().st_size
    return DebugLogStatus(
        exists=True,
        size_bytes=size,
        threshold_bytes=threshold,
        exceeded=size > threshold,
        path=str(actual),
    )


def archive_and_truncate(log_path: Path) -> Path | None:
    """Compress the log to archives/ and truncate the original.

    Returns the archive path, or None if the log was missing/empty.

    Best-effort on Windows: if CK3 holds the file in exclusive mode,
    the truncate phase emits a warning to stderr but doesn't raise —
    the caller's invariant ("the archive exists; the original may
    still hold its content") is preserved either way."""
    if not log_path.exists():
        log.info("no log at %s — nothing to do.", log_path)
        return None
    size = log_path.stat().st_size
    if size == 0:
        log.info("%s is already empty.", log_path)
        return None

    archives = log_path.parent / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    archive = archives / f"debug-{stamp}.log.gz"

    log.info("archiving %s bytes -> %s", f"{size:,}", archive)
    with log_path.open("rb") as src, gzip.open(archive, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)

    # Truncate by re-opening with mode "w" then closing immediately.
    # Best-effort: if CK3 has the file locked exclusively, this may fail.
    try:
        with log_path.open("wb") as fh:
            fh.truncate(0)
        log.info("truncated %s", log_path)
    except OSError as e:
        log.warning("could not truncate %s (CK3 running?): %s", log_path, e)

    return archive


def reset_tail_offsets(campaign_names: list[str]) -> int:
    """Reset tail_offset to 0 for the named campaigns. Returns the count
    reset. Empty list resets every active campaign — that's the
    rotate-everything default the CLI script and the HTTP rotate
    endpoint both want.

    The tailer's :class:`Watcher` already resets its in-memory offset
    when it observes the file shrinking (see ``watcher.py``); this
    function resets the *persisted* registry value so a chronicler
    restart between rotation and next CK3 run picks up cleanly from
    offset 0."""
    if campaign_names:
        targets = []
        for name in campaign_names:
            c = get_campaign_by_name(name)
            if c is None:
                log.warning("no such campaign: %s", name)
                continue
            targets.append(c)
    else:
        targets = list_campaigns()

    count = 0
    for c in targets:
        if c.tail_offset != 0:
            set_tail_offset(c.id, 0)
            log.info("reset tail_offset for campaign %s (%s)", c.name, c.id)
            count += 1
    return count
