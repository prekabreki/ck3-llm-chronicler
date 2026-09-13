"""v0_11_drop_character_relevance

Revision ID: u6cr0d11e0d
Revises: g7md7c001abc
Create Date: 2026-05-08 23:30:00.000000

Drops the dead ``relevance`` column from ``characters``
(ck3_chronicler-u6cr).

The column was introduced in the v0.1 initial schema with a
``server_default='unknown'`` and was intended to carry a salience
label ('player' / 'heir' / 'spouse' / etc.) for ranking + UI
prioritisation. No production code path ever wrote a non-default
value: import-save and save-tail both upsert without setting
relevance, so every persisted row stayed at 'unknown'. The
chronicle folio's Vitals card already hides the row when the value
is the dead default (jnze edit pass). The search-rank dependency
in ``search_characters_by_name`` falls back to ``ck3_id`` ordering
— acceptable since the name-rank computation already does the
heavy lifting; relevance was only a tie-breaker that ranked all
characters identically.

If we ever decide to populate a real salience signal it should be
a numeric float (event-count, biography-count) rather than a
string label — see ck3_chronicler-u6cr's option 2 (repurpose).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u6cr0d11e0d"
down_revision: str | Sequence[str] | None = "g7md7c001abc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("relevance")


def downgrade() -> None:
    """Downgrade schema."""
    import sqlalchemy as sa

    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "relevance",
                sa.String(),
                nullable=False,
                server_default="unknown",
            )
        )
