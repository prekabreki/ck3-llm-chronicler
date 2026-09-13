"""v0_9_character_region_summary_json

Revision ID: f1a08ek7w0r
Revises: ebc825c7d3ed
Create Date: 2026-05-04 14:30:00.000000

Adds a nullable ``region_summary_json`` text column to ``characters`` so
save-tail can persist each tracked character's resolved
:class:`chronicler.save.worldbuilding.RegionSummary` JSON. The
death-biography pipeline reads this column and threads it into the
prompt as the World-context scene-setter block (ck3_chronicler-8ek
slice 1).

The column mirrors the design of ``coa_json`` (a98349146815) — JSON
text persisted at save-tail tracked-character refresh time, no need
to re-parse the save when generating biographies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a08ek7w0r"
down_revision: str | Sequence[str] | None = "ebc825c7d3ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("region_summary_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("region_summary_json")
