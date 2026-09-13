"""Rotate CK3's debug.log between play sessions.

Paradox's `error.log` and `debug.log` are notorious for ballooning to
multi-GB sizes when mods are loaded (research §2.5). This script:

1. Compresses the current ``debug.log`` to
   ``<logs>/archives/debug-YYYYMMDD-HHMM.log.gz``
2. Truncates ``debug.log`` to zero bytes
3. Resets the per-campaign ``tail_offset`` in the registry to 0 (since
   the tailer's saved offset no longer points at meaningful data)

CK3 holds ``debug.log`` in append mode while running. Truncating from
outside while CK3 is running is best-effort: on Windows the file may
be locked. Run this between sessions, not during.

Logic lives in :mod:`chronicler.tailer.log_rotate` so the HTTP rotate
endpoint (ck3_chronicler-z6jm) can call the same helpers; this script
remains for CLI users.

Usage::

    uv run python scripts/rotate_debug_log.py
    uv run python scripts/rotate_debug_log.py --log /custom/debug.log --campaigns Test other
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from chronicler.config import get_ck3_debug_log
from chronicler.tailer.log_rotate import (
    archive_and_truncate,
    reset_tail_offsets,
)

# Re-export so existing callers / tests that import from this script
# (importlib.util.spec_from_file_location) keep working without
# tracking the rename. New code should import from
# chronicler.tailer.log_rotate directly.
__all__ = ["archive_and_truncate", "reset_tail_offsets", "main", "parse_args"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Path to debug.log (defaults to CK3's standard location).",
    )
    p.add_argument(
        "--campaigns",
        nargs="*",
        default=None,
        help="Campaign names whose tail_offset to reset. Defaults to all active campaigns.",
    )
    p.add_argument(
        "--no-reset",
        action="store_true",
        help="Skip resetting tail offsets (rotation only).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Route chronicler.tailer.log_rotate's INFO/WARNING records to stdout
    # for the CLI user. The HTTP route handler (audit F-25) keeps using
    # the regular logger configuration so its records flow through
    # uvicorn's structured logging instead of polluting stdout.
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    args = parse_args(argv)
    log_path = args.log or get_ck3_debug_log()
    archive_and_truncate(log_path)
    if not args.no_reset:
        n = reset_tail_offsets(args.campaigns or [])
        if n == 0:
            print("no campaign offsets needed resetting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
