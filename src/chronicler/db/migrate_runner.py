"""Single canonical alembic-upgrade runner (ck3_chronicler-27ov.52, audit M-B6).

Three near-identical ``Config(repo_root/alembic.ini)`` + ``db_url`` +
``command.upgrade(head)`` implementations lived in ``cli/main.py``,
``save/adoption.py``, and ``migrate/migrator.py`` with subtly different
error wrapping — any change to how migrations are invoked had to be
found three times. Call sites keep their own error policy (the CLI lets
it raise, adoption wraps in :class:`~chronicler.save.adoption.
AlembicUpgradeFailed`, the migrator records per-row results) around
this one shared core.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from chronicler.config import find_repo_root


def _config_for(db_path: Path) -> Config:
    cfg = Config(str(find_repo_root() / "alembic.ini"))
    cfg.attributes["db_url"] = f"sqlite:///{db_path.as_posix()}"
    return cfg


def upgrade_to_head(db_path: Path) -> None:
    """Run ``alembic upgrade head`` against one per-campaign SQLite DB.

    Creates the parent directory if needed. Idempotent for already-
    migrated DBs (alembic no-ops at head). Raises whatever alembic
    raises — wrap at the call site for surface-specific error policy.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_config_for(db_path), "head")
