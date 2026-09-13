"""Shared internals for the chronicler registry package.

Exports the data-dir resolution, the ``_connect`` context manager,
and the schema-init bookkeeping used by :mod:`campaigns`,
:mod:`tracked`, and :mod:`suppression`. Splitting ``registry.py``
into a package (audit F-23) made each concern visible on its own;
this module keeps the wiring shared so callers continue to write
``from chronicler.db.registry import _connect``.

F-49: schema setup wraps ALTER TABLE in ``try/except OperationalError``
matching ``"duplicate column name"``. Two writers racing on a fresh
registry — one process inside chronicler, another on the same DB
file (e.g. a dev tool) — used to crash the loser when both ran the
lazy migration. The per-process ``_INIT_LOCK`` only guards
intra-process races; cross-process the OS scheduler decides who
wins. Treating the duplicate-column error as success closes that.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DATA_DIR_ENV = "CHRONICLER_DATA_DIR"

# F022 / 81v: schema-init memoisation. The first _connect for a given
# registry path runs CREATE TABLE IF NOT EXISTS + PRAGMA table_info +
# ALTER TABLE for any missing columns. Subsequent connects to the same
# path skip that work — the registry file is single-process and won't
# regress its schema during the lifetime of a chronicler run.
_INITIALIZED_PATHS: set[str] = set()
_INIT_LOCK = threading.Lock()

log = logging.getLogger(__name__)


def get_data_dir() -> Path:
    # Single source of truth (CHRONICLER_DATA_DIR override + per-OS default,
    # XDG on Linux) lives in chronicler.config; imported lazily to avoid a
    # module-load cycle.
    from chronicler.config import chronicler_data_dir

    return chronicler_data_dir()


def registry_path() -> Path:
    return get_data_dir() / "registry.db"


def campaign_db_path(campaign_id: str) -> Path:
    return get_data_dir() / "campaigns" / f"{campaign_id}.db"


def _ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    required: tuple[tuple[str, str], ...],
) -> None:
    """Lazy-migration helper: ALTER TABLE ADD COLUMN for any column in
    ``required`` that PRAGMA table_info reports missing.

    F-49: ``ALTER TABLE`` is wrapped in ``try/except`` so a cross-process
    race (two writers both observing a fresh path before either has
    committed the new column) doesn't crash the loser. SQLite raises
    ``OperationalError: duplicate column name: <col>`` when the column
    already exists; we treat that as success. Any other OperationalError
    re-raises.
    """
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    for col_name, col_type in required:
        if col_name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                # Another writer added it between our PRAGMA check and our
                # ALTER. Schema is now in the desired state — log and proceed.
                log.debug("registry %s.%s already added by concurrent writer", table, col_name)
                continue
            raise


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection to the registry DB; lazy-init schema once per path.

    Three tables share this connection: ``campaigns``, ``tracked_characters``,
    ``suppressed_event_kinds``. Each sub-module owns its CREATE TABLE +
    lazy-migration helpers and exposes a ``_setup(conn)`` callable that
    this function invokes the first time it sees a given registry path.
    Lazy imports break what would otherwise be a circular at module
    load (sub-modules import ``_connect`` from here).

    ck3_chronicler-v4z: hold ``_INIT_LOCK`` across the schema work, not
    just the membership check. The earlier "check, release, DDL,
    re-acquire to record" shape let two threads observing a fresh path
    both run schema setup and one of them hit ``duplicate column name``
    on ALTER TABLE. Window is microseconds and the lock is taken once
    per path per process — perf impact is negligible. F-49 hardens the
    cross-process case via :func:`_ensure_columns`.
    """
    p = path or registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    # ck3_chronicler-27ov.9 (audit H5): the registry is raw sqlite3 shared
    # between the ingest consumer and API threads. WAL lets readers coexist
    # with the writer; busy_timeout makes a contended write wait up to 5s
    # instead of instantly raising 'database is locked' into the caller.
    # journal_mode is persistent in the DB file — re-executing per connect
    # is a cheap no-op after the first time.
    conn.execute("PRAGMA busy_timeout = 5000")
    # ck3_chronicler-27ov.79 (audit L18): foreign_keys is intentionally left
    # OFF. The child tables (tracked_characters / suppressed_event_kinds) no
    # longer declare ON DELETE CASCADE — the codebase writes child rows keyed
    # by campaign_id that legitimately precede (or live in a different
    # registry than) the campaigns row (auto-track during ingest, per-campaign
    # vs registry split), so enforcing the FK would reject valid inserts.
    # Referential cleanup on delete is delete_campaign's explicit job.
    key = str(p.resolve())
    try:
        with _INIT_LOCK:
            if key not in _INITIALIZED_PATHS:
                # ck3_chronicler-3nq7/apq5: switch to WAL once per path,
                # under the lock. journal_mode is persistent in the file,
                # but running `PRAGMA journal_mode = WAL` on *every* connect
                # let N concurrent first-opens race the exclusive
                # journal-mode switch and raise 'database is locked' (the
                # residual flake the v4z init-lock didn't cover). Only the
                # first opener per path does the switch now; later
                # connections inherit WAL from the file and rely on
                # busy_timeout for write contention.
                conn.execute("PRAGMA journal_mode = WAL")
                # Lazy import — sub-modules import _connect from us.
                from chronicler.db.registry import campaigns as _campaigns
                from chronicler.db.registry import suppression as _suppression
                from chronicler.db.registry import tracked as _tracked

                _campaigns._setup(conn)
                _tracked._setup(conn)
                _suppression._setup(conn)
                _INITIALIZED_PATHS.add(key)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Public seams (audit L22 / ck3_chronicler-27ov.79): external callers should
# use these instead of reaching for the underscore-prefixed internals. The
# registry sub-modules still import ``_connect`` directly (they can't import
# the package __init__ without a cycle); ``connect`` is the same context
# manager under a public name.
connect = _connect


def ensure_schema(path: Path | None = None) -> None:
    """Idempotently create/upgrade the registry schema at ``path``.

    Runs the lazy ``_setup`` pass that :func:`connect` performs on first use
    of a path, without handing back the connection — a real seam for callers
    that only need the schema present.
    """
    with _connect(path):
        pass
