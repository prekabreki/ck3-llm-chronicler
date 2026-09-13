"""v0_11_primary_title_json

Revision ID: zx2l8a5p3df1
Revises: j7u1m2n3o4ab
Create Date: 2026-05-13 19:50:00.000000

Adds a nullable ``primary_title_json`` text column to ``characters`` so
save-tail can persist each tracked character's highest-tier directly-held
title at the moment of the last refresh (ck3_chronicler-zx2l).

Mirrors ``great_cause_json`` / ``region_summary_json`` as a
snapshot-derived JSON column. Unlike ``great_cause_json``, the column is
*not* on the NULLABLE-on-update allowlist — a transient absence of held
titles (the death tick, where the heir has already inherited) preserves
the last live value rather than wiping it. That last live value is the
"title at death" for biography surfaces.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zx2l8a5p3df1"
down_revision: str | Sequence[str] | None = "j7u1m2n3o4ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("primary_title_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("primary_title_json")
