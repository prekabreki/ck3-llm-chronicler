"""v0_12_character_memories_rebuilt_at

Revision ID: ma96slice1abc
Revises: zx2l8a5p3df1
Create Date: 2026-05-27 21:30:00.000000

Adds a nullable ``memories_rebuilt_at`` text column to ``characters`` for
the ck3_chronicler-0224 boundary-firing migration. Sentinel for the
"first boundary consolidation pass under the new code" auto-supersede
path: when this column is NULL, ``consolidate_memories`` supersedes
every currently-active memory for the character before generating
fresh Opus memories with no prior-memory context, then sets the column
to the wall-clock ISO timestamp. Subsequent boundary fires use the
normal incremental path (active memories feed into the next pass).

NULL on existing rows = "use the auto-supersede path on first run";
non-NULL = "already rebuilt at this UTC timestamp, normal pipeline".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ma96slice1abc"
down_revision: str | Sequence[str] | None = "wak4i9e2t1ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("memories_rebuilt_at", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("memories_rebuilt_at")
