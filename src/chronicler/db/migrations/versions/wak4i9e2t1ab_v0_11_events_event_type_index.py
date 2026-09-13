"""v0_11_events_event_type_index

Revision ID: wak4i9e2t1ab
Revises: zx2l8a5p3df1
Create Date: 2026-05-14 22:30:00.000000

ck3_chronicler-wak4: index on ``events.event_type``.

``compute_played_character_ids`` runs ``SELECT primary_character_id,
payload_json FROM events WHERE event_type = 'title_acquired'`` on every
character list/detail request — without an index, that's a full-table
scan that grows linearly with campaign age. On a 60-year campaign with
tens of thousands of title_acquired events the query exceeded a few
hundred ms; the Codex page mount and per-character detail load both
felt sluggish under live play.

Single-column index — cheap to add (small table even at 50K events;
SQLite builds it instantly) and the query is exact-match on event_type.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "wak4i9e2t1ab"
down_revision: str | Sequence[str] | None = "zx2l8a5p3df1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_events_event_type",
        "events",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_events_event_type", table_name="events")
