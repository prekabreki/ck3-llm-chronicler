"""Tracked-character lifecycle (V02 biography opt-in list).

One of three concerns in the chronicler registry package (audit F-23):
this module owns the ``tracked_characters`` table — add / update /
remove, the vysp.10 pause/resume/bump lifecycle, and the scheduler's
hot-path :func:`get_tracked_character_ids` set lookup.

Defaulting to "biography for everyone" was untenable: V02-S01 testing
showed 1,144 deaths in 9 in-game months across the whole world. The
user adds characters they care about (typically player + spouse +
heirs + close rivals) via ``chronicler track <id> --campaign <name>``;
the scheduler skips any character not on the list.

All public symbols re-export from ``chronicler.db.registry`` for
backwards compatibility — callers should keep importing from the
top-level package.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chronicler.db.registry._core import _connect, _ensure_columns

_CREATE_TRACKED = """
CREATE TABLE IF NOT EXISTS tracked_characters (
    campaign_id TEXT NOT NULL,
    character_id INTEGER NOT NULL,
    note TEXT,
    added_at TEXT NOT NULL,
    role VARCHAR(32),
    paused_at TEXT,
    bumped_at TEXT,
    PRIMARY KEY (campaign_id, character_id)
    -- audit L18 (ck3_chronicler-27ov.79): no FK to campaigns. Enforcement
    -- is off (see _core._connect) because child rows can predate / outlive
    -- the registry campaign row; delete_campaign cleans these up explicitly.
)
"""

# ck3_chronicler-lvv + 4cl + vysp.10: lazy migration for tracked-character
# columns. Registry isn't Alembic-managed, so the pattern is to inspect
# PRAGMA table_info on each connect and ALTER TABLE if columns are missing.
# Idempotent — does nothing on fresh DBs (the columns are already in
# _CREATE_TRACKED).
#
# ck3_chronicler-nx2x: the preferred_provider / preferred_model columns were
# retired (dead multi-provider fiction — Claude Code is the single permanent
# backend; nothing ever set or read them). They're dropped from the schema +
# this list so fresh DBs are clean. Existing registries keep the now-unused
# physical columns — they're never selected into TrackedCharacter, so a
# destructive DROP COLUMN migration on live data isn't worth the risk.
_TRACKED_REQUIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("role", "VARCHAR(32)"),
    ("paused_at", "TEXT"),
    ("bumped_at", "TEXT"),
)


def _setup(conn: sqlite3.Connection) -> None:
    """Schema setup for the tracked_characters table. Called by
    :func:`_core._connect` once per registry path per process."""
    conn.execute(_CREATE_TRACKED)
    _ensure_columns(conn, "tracked_characters", _TRACKED_REQUIRED_COLUMNS)


@dataclass(frozen=True)
class TrackedCharacter:
    campaign_id: str
    character_id: int
    note: str | None
    added_at: str
    role: str | None = None
    # vysp.10: pause + bump support. paused_at NULL = active. bumped_at
    # is the most-recent manual priority bump; the scheduler sorts pending
    # tracked characters by bumped_at desc so a fresh bump jumps the queue.
    paused_at: str | None = None
    bumped_at: str | None = None


# ck3_chronicler-lvv + 4cl: fields a caller may set or override via add /
# update_tracked_character. character_id + campaign_id are PK, added_at
# is set internally — none of those belong on the updatable list.
_TRACKED_UPDATABLE_FIELDS: frozenset[str] = frozenset({"note", "role"})


def add_tracked_character(
    campaign_id: str,
    character_id: int,
    *,
    note: str | None = None,
    role: str | None = None,
    registry: Path | None = None,
) -> None:
    """Mark a character as eligible for automatic biography generation.

    Idempotent — re-adding an already-tracked character updates any
    provided fields (``note``, ``role``) while leaving the original
    ``added_at`` intact. Fields left as ``None`` do NOT clobber existing
    values, so partial updates are safe.
    """
    added_at = datetime.now(UTC).isoformat()
    with _connect(registry) as conn:
        conn.execute(
            """
            INSERT INTO tracked_characters (
                campaign_id, character_id, note, added_at, role
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, character_id) DO UPDATE SET
                note = COALESCE(excluded.note, tracked_characters.note),
                role = COALESCE(excluded.role, tracked_characters.role)
            """,
            (
                campaign_id,
                character_id,
                note,
                added_at,
                role,
            ),
        )


def update_tracked_character(
    campaign_id: str,
    character_id: int,
    *,
    registry: Path | None = None,
    **fields: object,
) -> bool:
    """Update one or more updatable fields on a tracked character.

    Pass field=value kwargs from :data:`_TRACKED_UPDATABLE_FIELDS`.
    Setting a field to ``None`` explicitly clears it — distinct from
    ``add_tracked_character`` which preserves on None. Returns True if a
    row was updated. Unknown kwargs raise ``ValueError`` rather than
    being silently ignored.
    """
    unknown = set(fields) - _TRACKED_UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown tracked-character fields: {sorted(unknown)}")
    if not fields:
        return False
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    values.extend([campaign_id, character_id])
    with _connect(registry) as conn:
        cur = conn.execute(
            f"UPDATE tracked_characters SET {assignments} "
            "WHERE campaign_id = ? AND character_id = ?",
            values,
        )
        return cur.rowcount > 0


def remove_tracked_character(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> bool:
    """Remove a tracked character. Returns True if a row was removed."""
    with _connect(registry) as conn:
        cur = conn.execute(
            "DELETE FROM tracked_characters WHERE campaign_id = ? AND character_id = ?",
            (campaign_id, character_id),
        )
        return cur.rowcount > 0


def is_character_tracked(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> bool:
    with _connect(registry) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM tracked_characters
            WHERE campaign_id = ? AND character_id = ?
            """,
            (campaign_id, character_id),
        ).fetchone()
        return row is not None


def _row_to_tracked_character(row: Any) -> TrackedCharacter:
    return TrackedCharacter(
        campaign_id=row["campaign_id"],
        character_id=row["character_id"],
        note=row["note"],
        added_at=row["added_at"],
        role=row["role"],
        paused_at=row["paused_at"] if "paused_at" in row.keys() else None,  # noqa: SIM118 — sqlite3.Row needs explicit .keys() for column-existence
        bumped_at=row["bumped_at"] if "bumped_at" in row.keys() else None,  # noqa: SIM118 — sqlite3.Row needs explicit .keys() for column-existence
    )


def list_tracked_characters(
    campaign_id: str, *, registry: Path | None = None
) -> list[TrackedCharacter]:
    with _connect(registry) as conn:
        cur = conn.execute(
            """
            SELECT * FROM tracked_characters
            WHERE campaign_id = ?
            ORDER BY added_at
            """,
            (campaign_id,),
        )
        return [_row_to_tracked_character(row) for row in cur.fetchall()]


def get_tracked_character(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> TrackedCharacter | None:
    """Single-row lookup (audit F-21 / ck3_chronicler-m0wy).

    pause/resume/bump/add need to return the freshly-mutated row so the
    FE can patch its TanStack Query cache. They previously called
    list_tracked_characters() then scanned for a match — one full table
    scan per mutation. This helper hits the row directly."""
    with _connect(registry) as conn:
        row = conn.execute(
            """
            SELECT * FROM tracked_characters
            WHERE campaign_id = ? AND character_id = ?
            """,
            (campaign_id, character_id),
        ).fetchone()
        return _row_to_tracked_character(row) if row is not None else None


# vysp.10: tracked-character lifecycle helpers. The biography scheduler
# is expected to honour these flags (pause => skip; bumped_at => sort
# pending queue by bumped_at desc). A future scheduler refactor will read
# these directly; for now the helpers just mutate the rows.


def pause_tracked_character(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> bool:
    """Pause biography auto-generation for this tracked character.

    Sets ``paused_at = now`` (UTC ISO). Returns True if the row exists
    and was updated. Re-pausing an already-paused character refreshes
    ``paused_at`` (cheap and idempotent — no point gating on prior state).
    """
    paused_at = datetime.now(UTC).isoformat()
    with _connect(registry) as conn:
        cur = conn.execute(
            "UPDATE tracked_characters SET paused_at = ? "
            "WHERE campaign_id = ? AND character_id = ?",
            (paused_at, campaign_id, character_id),
        )
        return cur.rowcount > 0


def resume_tracked_character(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> bool:
    """Resume biography auto-generation. Clears ``paused_at``."""
    with _connect(registry) as conn:
        cur = conn.execute(
            "UPDATE tracked_characters SET paused_at = NULL "
            "WHERE campaign_id = ? AND character_id = ?",
            (campaign_id, character_id),
        )
        return cur.rowcount > 0


def bump_tracked_character(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> bool:
    """Bump this tracked character to the head of the biography queue.

    Sets ``bumped_at = now`` so the scheduler will pick it up before
    other pending characters. Idempotent (re-bumping refreshes the
    timestamp, which is the desired behaviour).
    """
    bumped_at = datetime.now(UTC).isoformat()
    with _connect(registry) as conn:
        cur = conn.execute(
            "UPDATE tracked_characters SET bumped_at = ? "
            "WHERE campaign_id = ? AND character_id = ?",
            (bumped_at, campaign_id, character_id),
        )
        return cur.rowcount > 0


def get_tracked_character_ids(campaign_id: str, *, registry: Path | None = None) -> set[int]:
    """Fast path: just the IDs as a set, for the scheduler's hot path."""
    with _connect(registry) as conn:
        cur = conn.execute(
            "SELECT character_id FROM tracked_characters WHERE campaign_id = ?",
            (campaign_id,),
        )
        return {int(row["character_id"]) for row in cur.fetchall()}


def get_tracked_status(
    campaign_id: str, character_id: int, *, registry: Path | None = None
) -> tuple[bool, str | None]:
    """Returns ``(is_paused, bumped_at)`` for a tracked character.

    Untracked characters return ``(False, None)`` — the scheduler's
    `_is_tracked` filter is the gate for "is this character known at
    all"; this helper just answers "if it is tracked, what's its
    paused/bumped state". Used by the scheduler (ck3_chronicler-t2v5) to
    skip paused chars and prioritize bumped ones in the deferred-drain
    replay order.
    """
    with _connect(registry) as conn:
        row = conn.execute(
            """
            SELECT paused_at, bumped_at FROM tracked_characters
            WHERE campaign_id = ? AND character_id = ?
            """,
            (campaign_id, character_id),
        ).fetchone()
        if row is None:
            return (False, None)
        return (row["paused_at"] is not None, row["bumped_at"])
