"""v0_7_quarantine_event_kind

Revision ID: 35db08a36b62
Revises: a98349146815
Create Date: 2026-05-03 11:07:06.710516

Adds a nullable ``event_kind`` text column to ``quarantine`` so the
parser can record the event type (when known) on quarantined rows.
This lets the v0.7 settings UI surface "you have N quarantined deaths"
groupings, and lets the suppression hook
(:func:`chronicler.db.registry.is_kind_suppressed`) drop further
quarantine rows of a kind the user has chosen to silence — useful when
a parser bug fills the table with the same recurring failure.

Part of ck3_chronicler-fkw.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "35db08a36b62"
down_revision: str | Sequence[str] | None = "a98349146815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("quarantine", schema=None) as batch_op:
        batch_op.add_column(sa.Column("event_kind", sa.String(64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("quarantine", schema=None) as batch_op:
        batch_op.drop_column("event_kind")
