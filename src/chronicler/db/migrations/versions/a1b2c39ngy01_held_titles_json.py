"""held_titles_json

Revision ID: a1b2c39ngy01
Revises: b762b5ba1a38
Create Date: 2026-05-30 14:30:00.000000

Adds a nullable ``held_titles_json`` text column to ``characters`` so
save-tail can persist EVERY directly-held title (grandest-first) at the
moment of the last refresh, not just the single highest-tier primary
(ck3_chronicler-9ngy).

Mirrors ``primary_title_json`` (zx2l8a5p3df1): a JSON list of
``[{"key","name","tier"}, ...]`` whose head equals primary_title_json.
Like primary_title_json it is deliberately *not* on the
NULLABLE-on-update allowlist — a transient absence of held titles (the
death tick, where the heir has already inherited) preserves the last live
value, which is the "titles at death" the biography surfaces read.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c39ngy01"
down_revision: str | Sequence[str] | None = "b762b5ba1a38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("held_titles_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("held_titles_json")
