"""v0_2_event_date_iso

Revision ID: e51cf611ac2c
Revises: 03457d7644f3
Create Date: 2026-05-01 14:25:53.088334

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e51cf611ac2c"
down_revision: str | Sequence[str] | None = "03457d7644f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("event_date_iso", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_events_char_date_iso",
            ["primary_character_id", "event_date_iso"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_char_date_iso")
        batch_op.drop_column("event_date_iso")
