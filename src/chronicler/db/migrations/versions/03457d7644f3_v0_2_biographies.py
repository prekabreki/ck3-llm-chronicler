"""v0_2_biographies

Revision ID: 03457d7644f3
Revises: ce15b164b63c
Create Date: 2026-05-01 14:22:47.852343

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03457d7644f3"
down_revision: str | Sequence[str] | None = "ce15b164b63c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "biographies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("prompt_template_version", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("events_through_event_id", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["characters.ck3_id"],
            name=op.f("fk_biographies_character_id_characters"),
        ),
        sa.ForeignKeyConstraint(
            ["events_through_event_id"],
            ["events.id"],
            name=op.f("fk_biographies_events_through_event_id_events"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_biographies")),
        sa.UniqueConstraint("character_id", "version", name="biographies_char_version"),
    )
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.create_index("ix_biographies_character_id", ["character_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.drop_index("ix_biographies_character_id")

    op.drop_table("biographies")
