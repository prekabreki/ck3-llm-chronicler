"""v0_5_character_female

Revision ID: 53d89df90f2c
Revises: 40fffebe45eb
Create Date: 2026-05-02 12:00:00.000000

Adds a nullable ``female`` boolean to ``characters`` so the biography
and consolidator prompts can render an explicit gender line. Live
testing against Ælla 12267 (a man whose spouse name 'Beorhtgyth' kept
fooling the model into writing 'she/her') drove this — see
ck3_chronicler-30s.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "53d89df90f2c"
down_revision: str | Sequence[str] | None = "40fffebe45eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("female", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("female")
