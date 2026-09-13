"""v0_7_fts5_search_indexes

Revision ID: ebc825c7d3ed
Revises: 35db08a36b62
Create Date: 2026-05-03 11:37:05.077054

Creates FTS5 virtual tables shadowing biographies + memories so the
v0.7 portal's search box can full-text query both at human latency.
ck3_chronicler-b2y.

Each shadow table is a content-table FTS5 with sync triggers wiring
inserts / updates / deletes back to the FTS index. The shadow is
populated from the existing rows on first migration via INSERT INTO
SELECT.

If the host SQLite was built without FTS5 (vanishingly rare on
Python 3.11+ but possible) the migration logs and skips — the search
endpoint falls back to LIKE on those campaigns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ebc825c7d3ed"
down_revision: str | Sequence[str] | None = "35db08a36b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fts5_available(conn) -> bool:
    """True iff the SQLite build includes FTS5."""
    try:
        conn.exec_driver_sql("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.exec_driver_sql("DROP TABLE _fts5_probe")
        return True
    except Exception:
        return False


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if not _fts5_available(bind):
        return

    bind.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS biographies_fts USING fts5(
            body,
            content='biographies',
            content_rowid='id',
            tokenize='porter unicode61'
        )
        """
    )
    bind.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            body,
            content='memories',
            content_rowid='id',
            tokenize='porter unicode61'
        )
        """
    )

    bind.exec_driver_sql(
        "INSERT INTO biographies_fts(rowid, body) SELECT id, body FROM biographies"
    )
    bind.exec_driver_sql("INSERT INTO memories_fts(rowid, body) SELECT id, body FROM memories")

    for table, fts in (("biographies", "biographies_fts"), ("memories", "memories_fts")):
        bind.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_ai AFTER INSERT ON {table} BEGIN
                INSERT INTO {fts}(rowid, body) VALUES (new.id, new.body);
            END
            """
        )
        bind.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_ad AFTER DELETE ON {table} BEGIN
                INSERT INTO {fts}({fts}, rowid, body) VALUES ('delete', old.id, old.body);
            END
            """
        )
        bind.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_au AFTER UPDATE ON {table} BEGIN
                INSERT INTO {fts}({fts}, rowid, body) VALUES ('delete', old.id, old.body);
                INSERT INTO {fts}(rowid, body) VALUES (new.id, new.body);
            END
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    for table in ("biographies", "memories"):
        for suffix in ("ai", "ad", "au"):
            bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {table}_{suffix}")
    bind.exec_driver_sql("DROP TABLE IF EXISTS biographies_fts")
    bind.exec_driver_sql("DROP TABLE IF EXISTS memories_fts")
