"""Watch CK3's save directory for new ``.ck3`` files.

The v0.6 architecture's eyes on the world. CK3 writes a new save to the
configured save directory on each autosave (and on manual save). This
module emits a path stream of "new save observed" events that the
ingest loop can react to.

CK3 keeps multiple autosave slots that **rotate** — typically
``autosave.ck3`` is the latest with ``autosave_1.ck3`` being one step
back, and the count varies by setting. Default is 2 slots. With monthly
autosave (the v0.6-recommended config), rotation happens every in-game
month. We emit each save the moment we see it; downstream code reads
the file before the next rotation can overwrite it. CK3 typically takes
1-3 real-world seconds between writing a save and starting the next, so
even a leisurely parse + diff fits comfortably.

Two filtering layers:

1. **Filename pattern** — defaults to ``*.ck3`` so manual saves are
   captured too (useful for "load an old save and chronicle it"
   workflows). Set ``pattern="autosave*.ck3"`` to focus on autosaves.
   ck3_chronicler-fi7: pattern accepts a tuple/list of globs (or a
   comma-separated string) so callers can match
   ``("autosave.ck3", "autosave_exit.ck3")`` while excluding the
   numerically-suffixed rotation backups.
2. **Mtime/inode dedupe** — CK3 may trigger multiple watchfiles
   notifications for one save write (e.g. tmpfile-then-rename). We
   dedupe on (path, mtime_ns) so a single write produces a single yield.

The synchronous core, :class:`SaveDirectoryState`, is the unit-tested
piece. The async :func:`watch_saves` wraps it with ``watchfiles.awatch``
for live operation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

from watchfiles import awatch

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SaveFileEvent:
    """A new (or rotated-into-place) save file observation."""

    path: Path
    mtime_ns: int
    size_bytes: int


def _file_signature(path: Path) -> tuple[int, int] | None:
    """Return (mtime_ns, size) or None if the file disappeared."""
    try:
        stat = path.stat()
    except (FileNotFoundError, PermissionError):
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _normalize_patterns(
    pattern: str | tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """ck3_chronicler-fi7: accept str / tuple / list / comma-separated string.

    Mirrors :func:`chronicler.save.ingest._expand_patterns` so callers
    can pass either form interchangeably without coupling the watcher
    to the ingest module."""
    if isinstance(pattern, str):
        parts = tuple(part.strip() for part in pattern.split(","))
        return tuple(p for p in parts if p)
    return tuple(pattern)


class SaveDirectoryState:
    """Synchronous tracker for a save directory.

    Owns a {path: signature} map of files we've already emitted so a
    single CK3 save write doesn't produce duplicate events when
    watchfiles fires multiple notifications.
    """

    def __init__(
        self,
        save_dir: Path,
        *,
        pattern: str | tuple[str, ...] | list[str] = "*.ck3",
        emit_existing: bool = False,
    ) -> None:
        self.save_dir = save_dir
        self.patterns = _normalize_patterns(pattern)
        self._seen: dict[Path, tuple[int, int]] = {}
        if not emit_existing:
            # Pre-populate _seen so existing files don't fire on first scan.
            for p in self._scan():
                sig = _file_signature(p)
                if sig is not None:
                    self._seen[p.resolve()] = sig

    def _scan(self) -> Iterator[Path]:
        """All files currently matching any of the patterns.

        Globbing each pattern independently (rather than a single
        combined glob) keeps the rotation-backup exclusion working —
        ``autosave.ck3`` and ``autosave_exit.ck3`` match exactly,
        without dragging in ``autosave_1.ck3`` / ``autosave_2.ck3``.
        Resolved-path dedup prevents a file from emitting twice when
        multiple patterns happen to match the same file."""
        if not self.save_dir.exists():
            return
        seen: set[Path] = set()
        for pat in self.patterns:
            for p in self.save_dir.glob(pat):
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                yield p

    def poll(self) -> Iterator[SaveFileEvent]:
        """Emit one event per new/changed save file since the last poll.

        A file is "new" if either its path isn't in our seen-map yet, or
        its (mtime_ns, size) signature changed (rewrite, rotation).
        """
        for p in self._scan():
            resolved = p.resolve()
            sig = _file_signature(p)
            if sig is None:
                continue
            if self._seen.get(resolved) == sig:
                continue
            # Either fresh file or its signature changed (rewrite/rotate)
            self._seen[resolved] = sig
            yield SaveFileEvent(path=p, mtime_ns=sig[0], size_bytes=sig[1])


async def watch_saves(
    save_dir: Path,
    *,
    pattern: str | tuple[str, ...] | list[str] = "*.ck3",
    emit_existing: bool = False,
    stop_event: object | None = None,
) -> AsyncIterator[SaveFileEvent]:
    """Yield a :class:`SaveFileEvent` for each new save observed.

    On entry, optionally drains any files already in the directory
    (``emit_existing=True`` for backfill workflows). Otherwise only
    files written/rotated after the watch starts are emitted.

    ``stop_event`` is an :class:`asyncio.Event` (or compatible) that
    lets the caller terminate cleanly via :mod:`watchfiles.awatch`.
    """
    state = SaveDirectoryState(save_dir, pattern=pattern, emit_existing=emit_existing)

    if emit_existing:
        for evt in state.poll():
            yield evt

    log.info(
        "watching save directory: %s (pattern=%r, emit_existing=%s)",
        save_dir,
        pattern,
        emit_existing,
    )

    # ck3_chronicler-gedg: wrap awatch in a retry-with-backoff loop so a
    # transient OSError (save_dir briefly unmounts on OneDrive sync, the
    # OS reaps the watch handle during sleep/resume, etc.) doesn't kill
    # save-tail until process restart. The save_dir is on the user's
    # Documents folder on Windows; flaky filesystem events are real.
    while True:
        try:
            async for changes in awatch(save_dir, stop_event=stop_event):  # type: ignore[arg-type]
                # Filter to events under save_dir matching the pattern.
                # watchfiles reports per-change, but our state.poll handles
                # the actual diffing — we just trigger the scan when *any*
                # relevant file changes.
                relevant = any(Path(p).suffix == ".ck3" for _change_type, p in changes)
                if not relevant:
                    continue
                for evt in state.poll():
                    yield evt
        except (OSError, PermissionError) as e:
            log.warning(
                "watch_saves: %s during awatch on %s; retrying in 2s",
                type(e).__name__,
                save_dir,
                exc_info=e,
            )
            await asyncio.sleep(2.0)
            continue
        # awatch exited cleanly (stop_event set or upstream returned).
        return
