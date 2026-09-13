"""v0_5_character_nickname

Revision ID: 40fffebe45eb
Revises: 016f995d7ad5
Create Date: 2026-05-01 20:55:22.305034

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "40fffebe45eb"
down_revision: str | Sequence[str] | None = "016f995d7ad5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("nickname", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("nickname")
