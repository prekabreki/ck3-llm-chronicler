"""v0_5_memories

Revision ID: 016f995d7ad5
Revises: e51cf611ac2c
Create Date: 2026-05-01 19:29:27.627737

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "016f995d7ad5"
down_revision: str | Sequence[str] | None = "e51cf611ac2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("trigger_event_id", sa.Integer(), nullable=True),
        sa.Column("events_through_event_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("prompt_template_version", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("generated_at", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["characters.ck3_id"],
            name=op.f("fk_memories_character_id_characters"),
        ),
        sa.ForeignKeyConstraint(
            ["events_through_event_id"],
            ["events.id"],
            name=op.f("fk_memories_events_through_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["memories.id"],
            name=op.f("fk_memories_superseded_by_id_memories"),
        ),
        sa.ForeignKeyConstraint(
            ["trigger_event_id"],
            ["events.id"],
            name=op.f("fk_memories_trigger_event_id_events"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.create_index(
            "ix_memories_character_active",
            ["character_id", "superseded_by_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_memories_character_id",
            ["character_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_index("ix_memories_character_id")
        batch_op.drop_index("ix_memories_character_active")

    op.drop_table("memories")
