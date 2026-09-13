"""Per-campaign suppression of quarantine event kinds (ck3_chronicler-fkw).

One of three concerns in the chronicler registry package (audit F-23):
when a recurring parser failure floods the quarantine table with the
same ``event_kind`` (e.g. a malformed birth payload from a mod), the
user can silence that kind via ``chronicler suppress-quarantine
<campaign> <kind>``. The tailer checks :func:`is_kind_suppressed`
before insert and drops the row silently if the kind is on the list.

All public symbols re-export from ``chronicler.db.registry`` for
backwards compatibility — callers should keep importing from the
top-level package.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chronicler.db.registry._core import _connect

_CREATE_SUPPRESSED = """
CREATE TABLE IF NOT EXISTS suppressed_event_kinds (
    campaign_id TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    suppressed_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, event_kind)
    -- audit L18 (ck3_chronicler-27ov.79): no FK to campaigns (enforcement is
    -- off; delete_campaign deletes these rows explicitly).
)
"""


def _setup(conn: sqlite3.Connection) -> None:
    """Schema setup for the suppressed_event_kinds table. Called by
    :func:`_core._connect` once per registry path per process."""
    conn.execute(_CREATE_SUPPRESSED)


@dataclass(frozen=True)
class SuppressedKind:
    campaign_id: str
    event_kind: str
    suppressed_at: str


def add_suppressed_kind(
    campaign_id: str,
    event_kind: str,
    *,
    registry: Path | None = None,
) -> None:
    """Mark an event kind as silenced for a campaign. Idempotent —
    re-adding the same kind keeps the original ``suppressed_at``."""
    suppressed_at = datetime.now(UTC).isoformat()
    with _connect(registry) as conn:
        conn.execute(
            """
            INSERT INTO suppressed_event_kinds (campaign_id, event_kind, suppressed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(campaign_id, event_kind) DO NOTHING
            """,
            (campaign_id, event_kind, suppressed_at),
        )


def remove_suppressed_kind(
    campaign_id: str,
    event_kind: str,
    *,
    registry: Path | None = None,
) -> bool:
    """Re-enable quarantining for an event kind. Returns True if a row
    was removed."""
    with _connect(registry) as conn:
        cur = conn.execute(
            "DELETE FROM suppressed_event_kinds WHERE campaign_id = ? AND event_kind = ?",
            (campaign_id, event_kind),
        )
        return cur.rowcount > 0


def is_kind_suppressed(
    campaign_id: str,
    event_kind: str | None,
    *,
    registry: Path | None = None,
) -> bool:
    """True iff this campaign has explicitly silenced this event kind.

    A ``None`` ``event_kind`` (parser couldn't recover the type) is
    never considered suppressed — so unattributable failures always
    reach the quarantine table where they can be inspected.
    """
    if event_kind is None:
        return False
    with _connect(registry) as conn:
        row = conn.execute(
            "SELECT 1 FROM suppressed_event_kinds WHERE campaign_id = ? AND event_kind = ?",
            (campaign_id, event_kind),
        ).fetchone()
        return row is not None


def list_suppressed_kinds(
    campaign_id: str, *, registry: Path | None = None
) -> list[SuppressedKind]:
    with _connect(registry) as conn:
        cur = conn.execute(
            """
            SELECT * FROM suppressed_event_kinds
            WHERE campaign_id = ?
            ORDER BY suppressed_at
            """,
            (campaign_id,),
        )
        return [
            SuppressedKind(
                campaign_id=row["campaign_id"],
                event_kind=row["event_kind"],
                suppressed_at=row["suppressed_at"],
            )
            for row in cur.fetchall()
        ]
