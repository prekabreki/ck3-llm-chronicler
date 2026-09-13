"""v0_9_character_coa_history

Revision ID: 7b8d0c0a3a01
Revises: f1a08ek7w0r
Create Date: 2026-05-06 23:30:00.000000

Adds an append-only ``character_coa_history`` table so save-tail can
record every resolved-CoA change per tracked character (cadet branch
formation, in-game facelift, dynasty rename). The Chronicle Folio
renders these rows as a medallion timeline, surfacing arms transitions
that today get overwritten silently on the per-character ``coa_json``
column.

ck3_chronicler-7b8d.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b8d0c0a3a01"
down_revision: str | Sequence[str] | None = "f1a08ek7w0r"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "character_coa_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.String(), nullable=False),
        sa.Column("coa_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["characters.ck3_id"],
            name=op.f("fk_character_coa_history_character_id_characters"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_character_coa_history")),
    )
    op.create_index(
        "ix_character_coa_history_char_observed",
        "character_coa_history",
        ["character_id", "observed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_character_coa_history_char_observed",
        table_name="character_coa_history",
    )
    op.drop_table("character_coa_history")
