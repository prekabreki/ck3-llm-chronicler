"""v0_12_drop_memory_pipeline

Revision ID: b762b5ba1a38
Revises: ma96slice1abc
Create Date: 2026-05-28 21:36:40.062160

Destructive: tears down the LLM-memory pipeline schema as part of the
cozy-coalescing-shannon demolition (plan tasks 1-16). Three pieces come
out together because they make no sense apart:

1. ``memories_fts`` — the FTS5 shadow table created in
   ``ebc825c7d3ed_v0_7_fts5_search_indexes`` plus its three sync
   triggers (``memories_ai`` / ``memories_au`` / ``memories_ad``).
   With the data table gone the shadow has nothing to index and the
   triggers would fire on a missing table on every memory write — but
   there are no memory writes anymore.

2. ``memories`` table itself — the per-character LLM-consolidator
   output. Every code path that read or wrote this table has already
   been deleted in earlier tasks of the same plan.

3. ``characters.memories_rebuilt_at`` — the ck3_chronicler-0224
   sentinel column that the dropped consolidate_memories path used
   to gate the auto-supersede first-pass behaviour. No consolidator
   left to read it.

Forward-only. ``downgrade()`` raises ``NotImplementedError`` — the
LLM-generated memory rows can't be reconstructed from anywhere else,
so the only honest way to "revert" is restoring the campaign DB from
a backup. Pretending we can recreate the schema empty would silently
strand callers expecting the old shape.

The trigger names match the migration that created them (not the
``memories_fts_*`` shape sometimes used elsewhere — the v0_7 migration
named them after the source table, ``memories_ai`` etc.).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b762b5ba1a38"
down_revision: str | Sequence[str] | None = "ma96slice1abc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    # 1. Drop the FTS5 sync triggers first — once the source table goes,
    #    these become references to nothing and SQLite would refuse to
    #    fire them anyway. Idempotent via IF EXISTS for the edge case
    #    where FTS5 wasn't available when ebc825c7d3ed ran (older
    #    SQLite builds) and the triggers were never installed.
    for suffix in ("ai", "ad", "au"):
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS memories_{suffix}")

    # 2. Drop the FTS5 shadow virtual table. Goes before ``memories``
    #    so the external-content link is severed cleanly. IF EXISTS for
    #    the same FTS5-not-available case.
    bind.exec_driver_sql("DROP TABLE IF EXISTS memories_fts")

    # 3. Drop the memories table proper. The FK constraints from
    #    ``memories.trigger_event_id`` / ``events_through_event_id`` to
    #    ``events.id`` and from ``memories.superseded_by_id`` to its
    #    own ``id`` all go away with the table. No other table FKs INTO
    #    memories, so there's nothing else to clean up.
    bind.exec_driver_sql("DROP TABLE IF EXISTS memories")

    # 4. Drop the ck3_chronicler-0224 sentinel column. Uses
    #    batch_alter_table so SQLite's table-rebuild ALTER mechanism
    #    runs — straight ``ALTER TABLE ... DROP COLUMN`` is supported
    #    on modern SQLite but the batch helper is the codebase's
    #    convention and works on every supported version.
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_column("memories_rebuilt_at")


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError(
        "v0.12 drop_memory_pipeline is forward-only; restore the campaign "
        "DB from a pre-v0.12 backup to revert. The LLM-generated memory "
        "rows can't be reconstructed from any other source."
    )
