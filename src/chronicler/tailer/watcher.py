"""Watch a CK3 ``debug.log`` for new ``[CHRONICLER]``-tagged lines.

The watcher tracks a byte offset into the file. On each change it reads
forward from the offset, yielding only complete (newline-terminated)
lines. A partial trailing line is left in the file — its bytes do not
advance the offset, so the next read picks up from the same point and
sees the now-complete line.

Truncation is handled: if the file shrinks below the current offset
(rotation or the ``rotate_debug_log.py`` script), the offset resets to
zero and we re-read from the start. CK3 holds the file in append mode;
on Windows that does not block our concurrent read open.

The synchronous core (:meth:`TailedFile.read_new_lines`) is what we
unit-test — we drive it with file writes from the test, no event loop
needed. The async :func:`tail` wraps it with ``watchfiles.awatch`` to
react to OS-level change notifications.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

from watchfiles import awatch

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TailedLine:
    text: str
    offset: int  # byte offset just past this line's terminator


class TailedFile:
    """Synchronous append-only file reader with truncation detection.

    Owns a single byte offset; advance it only by yielding complete lines.
    """

    def __init__(self, path: Path, *, start_offset: int = 0) -> None:
        self.path = path
        self.offset = start_offset

    def read_new_lines(self) -> Iterator[TailedLine]:
        if not self.path.exists():
            return

        size = self.path.stat().st_size
        if size < self.offset:
            log.warning(
                "tailer: %s shrank from %d to %d; resetting offset",
                self.path,
                self.offset,
                size,
            )
            self.offset = 0
        if size == self.offset:
            return

        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # Partial — leave the offset before it.
                    break
                self.offset = fh.tell()
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                yield TailedLine(text=text, offset=self.offset)


async def tail(
    path: Path,
    *,
    start_offset: int = 0,
    stop_event: object | None = None,
) -> AsyncIterator[TailedLine]:
    """Yield lines from ``path`` indefinitely, reacting to OS change events.

    On entry, drains any content already past ``start_offset`` so a restart
    after a crash picks up exactly where it left off. ``stop_event`` (if
    given, an :class:`asyncio.Event`) lets the caller cancel cleanly via
    ``watchfiles.awatch``.
    """
    state = TailedFile(path, start_offset=start_offset)

    for line in state.read_new_lines():
        yield line

    parent = path.parent
    async for changes in awatch(parent, stop_event=stop_event):  # type: ignore[arg-type]
        affected = any(Path(changed).resolve() == path.resolve() for _, changed in changes)
        if not affected:
            continue
        for line in state.read_new_lines():
            yield line
