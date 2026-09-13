"""v0_10_character_great_cause_json

Revision ID: g7md7c001abc
Revises: 7b8d0c0a3a01
Create Date: 2026-05-08 16:00:00.000000

Adds a nullable ``great_cause_json`` text column to ``characters`` so
save-tail can persist each tracked character's resolved
:class:`chronicler.save.worldbuilding.GreatCauseFacts` JSON. The
biography pipeline reads this column and threads a "Current great
cause" block into the briefing when the character is currently bound
to a crusade, great holy war, or papal crusade
(ck3_chronicler-7md7).

Snapshot-derived: when CK3 deletes the war from active_wars on
war_concluded, the next save-tail tick rewrites this column to NULL
without explicit lifecycle bookkeeping. Mirrors the design of
``region_summary_json`` (f1a08ek7w0r).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g7md7c001abc"
down_revision: str | Sequence[str] | None = "7b8d0c0a3a01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("great_cause_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("great_cause_json")
