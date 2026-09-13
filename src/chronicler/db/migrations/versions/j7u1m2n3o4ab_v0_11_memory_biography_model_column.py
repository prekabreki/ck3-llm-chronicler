"""v0_11_memory_biography_model_column

Revision ID: j7u1m2n3o4ab
Revises: u6cr0d11e0d
Create Date: 2026-05-13 17:30:00.000000

ck3_chronicler-ju7j (Phase 1 of the LLM pipeline audit). Adds a nullable
``model`` column to ``memories`` and ``biographies``. The existing
``provider`` column stores the kind-resolved tag at insert time (now
"claude-code:claude-sonnet-4-6" for memory rows after the
``provider.name_for_kind`` fix), while ``model`` stores the model the
envelope actually billed (from ``modelUsage``). The two diverge when
Claude Code routes the requested model to a different one, or when
subagent calls show up in modelUsage. NULL on rows that pre-date this
column — older rows pre-Phase-1 already have the wrong (biography-
anchored) provider tag and there is no way to recover the true model
post hoc, so they stay NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j7u1m2n3o4ab"
down_revision: str | Sequence[str] | None = "u6cr0d11e0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.add_column(sa.Column("model", sa.String(), nullable=True))
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("model", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_column("model")
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.drop_column("model")
