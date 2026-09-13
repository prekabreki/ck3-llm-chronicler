"""v0_13_biography_cost_columns

Revision ID: cs1ocost001a
Revises: a1b2c39ngy01
Create Date: 2026-06-05 00:00:00.000000

ck3_chronicler-cs1o: honest cost accounting for the dual-transport
narrative backend. Three nullable columns on ``biographies``:

- ``cache_read_tokens`` / ``cache_write_tokens`` — prompt-cache
  breakdown, subsets of the existing ``prompt_tokens`` (which remains
  the summed total: marginal + cache write + cache read). Cache reads
  bill at 0.1x base input and writes at 1.25x, so without the breakdown
  the USD math overstates input cost ~3x at the observed ~0.73
  cache-read ratio.
- ``cost_usd`` — the transport's own figure for the call: the claude
  --print envelope's top-level ``total_cost_usd`` (Anthropic's own
  number for what the call cost the programmatic credit pool), or the
  locally computed rate-card figure for the direct-API transport.

NULL on pre-cs1o rows; the cost endpoints fall back to the documented
summed-input approximation for those and flag it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cs1ocost001a"
down_revision: str | Sequence[str] | None = "a1b2c39ngy01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("biographies", schema=None) as batch_op:
        batch_op.drop_column("cost_usd")
        batch_op.drop_column("cache_write_tokens")
        batch_op.drop_column("cache_read_tokens")
