"""SQLAlchemy engine + session factories for chronicler databases.

Per-campaign DBs run in WAL mode with ``synchronous = NORMAL`` so the tailer
(sole writer) does not block API readers. ``foreign_keys`` is enabled per
connection because SQLite defaults to off.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker


def _set_sqlite_pragmas(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    try:
        # Campaign DBs are shared between the save-tail writer, API readers,
        # the startup backfill executor thread, and lazily-built schedulers.
        # busy_timeout makes a contended op wait instead of instantly raising
        # 'database is locked' into the caller — parity with the registry's
        # raw-sqlite3 connection, which set this deliberately for the same
        # reason (ck3_chronicler-27ov.9 / audit H5). Set first so the WAL
        # switch below is itself covered.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def make_engine(url: str) -> Engine:
    """Build a SQLAlchemy engine with the chronicler pragmas wired in.

    ``url`` is a SQLAlchemy URL such as ``sqlite:///path/to/campaign.db``.
    Caller is responsible for ensuring the parent directory exists.
    """
    engine = create_engine(url, future=True)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def make_engine_for_path(path: Path) -> Engine:
    """Convenience wrapper: build an engine for a SQLite file path."""
    return make_engine(f"sqlite:///{path.as_posix()}")


def make_engine_for_existing_path(path: Path) -> Engine:
    """Issue #8: like :func:`make_engine_for_path`, but connecting to a path
    that does not exist raises instead of creating an empty database.

    Plain ``sqlite:///path`` inherits SQLite's create-on-open behaviour, so
    any pass that merely *reads* existing campaign DBs can bring one back
    from the dead as a table-less stub. The startup backfills did exactly
    that: each guards with :func:`campaign_db_is_usable`, but the guard and
    the connect are two steps, and a campaign deleted in between left a
    resurrected empty file behind plus a swallowed "no such table" warning.

    ``mode=rw`` is SQLite's read-write-but-never-create URI flag. Writes
    still work — this is not read-only — the only behaviour removed is
    creation, which no caller over pre-existing databases wants.
    """
    # The literal "file:" prefix and uri=true put SQLite in URI mode; the
    # path must be absolute for it to resolve the same way as a plain path.
    return make_engine(f"sqlite:///file:{path.resolve().as_posix()}?mode=rw&uri=true")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Context manager: commit on success, rollback on exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def transactional_connection(engine: Engine) -> Iterator[Connection]:
    """Context manager around a Core connection with an implicit transaction."""
    with engine.begin() as conn:
        yield conn
