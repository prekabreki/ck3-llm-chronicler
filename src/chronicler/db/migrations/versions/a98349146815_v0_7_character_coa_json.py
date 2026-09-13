"""v0_7_character_coa_json

Revision ID: a98349146815
Revises: 53d89df90f2c
Create Date: 2026-05-02 21:00:00.000000

Adds a nullable ``coa_json`` text column to ``characters`` so save-tail
can persist each tracked character's resolved CoA structure (the
``coat_of_arms_manager_database[<id>]`` dict reached via the character
→ dynasty_house → coat_of_arms_id chain). Backend then exposes the
persisted JSON via /api/campaigns/{name}/characters/{ck3_id}/coa for
the real-heraldry SVG composition renderer to consume.

Part of ck3_chronicler-7ao subsystem 2.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a98349146815"
down_revision: str | Sequence[str] | None = "53d89df90f2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("coa_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("coa_json")
