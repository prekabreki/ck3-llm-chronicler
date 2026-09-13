"""Structured logging setup for chronicler processes.

Entry points (CLI, orchestrator) call ``setup_logging()`` once at startup.
Logs land under ``~/Documents/chronicler/logs/`` by default; override with
the ``CHRONICLER_LOG_DIR`` environment variable. Files rotate at 10 MB
with five backups kept.

Quarantine rule (enforced by the tailer's parser and ingest layer):
validation failures, JSON errors, and DB-constraint violations land as
rows in the per-campaign ``quarantine`` table (raw_line, error, ts) so
one bad line never stops the pipeline. Nothing raises out of the ingest
loop. Operators review quarantined rows via the v0.9 failed-event UI.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR_ENV = "CHRONICLER_LOG_DIR"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def get_log_dir() -> Path:
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override)
    from chronicler.config import chronicler_data_dir

    return chronicler_data_dir() / "logs"


def setup_logging(log_name: str = "chronicler", level: int = logging.INFO) -> Path:
    """Configure root logger with rotating file + stderr handlers. Idempotent.

    Returns the log directory that was created/used.
    """
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = RotatingFileHandler(
        log_dir / f"{log_name}.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    stderr_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
    return log_dir
