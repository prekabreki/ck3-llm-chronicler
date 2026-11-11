"""v0_14_biography_system_prompt_sha256

Revision ID: i28sha256bio01
Revises: cs1ocost001a
Create Date: 2026-11-11 00:00:00.000000

Issue #28: record the first 16 hex chars of the assembled system
prompt's SHA-256 on each new biography row, so the row can be tied to
the editable prose rules that produced it.

Additive and nullable: existing rows keep NULL and are not rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i28sha256bio01"
down_revision: str | Sequence[str] | None = "cs1ocost001a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("system_prompt_sha256", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.drop_column("system_prompt_sha256")
