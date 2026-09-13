"""Regression test for ck3_chronicler-7p2: alembic migration must not
clobber chronicler's logging config.

Scenario: chronicler dev calls setup_logging() at startup, then triggers
alembic migrations on every per-campaign DB connect. Before the fix,
``alembic env.py`` called ``fileConfig(config.config_file_name)`` with
the stdlib default ``disable_existing_loggers=True``, which silently
disabled every chronicler.* logger that had been imported up to that
point. Even with that flag set to False, alembic.ini's
``[logger_root] level = WARNING`` would then drop root from INFO to
WARNING and suppress chronicler.* INFO logs (which inherit from root).

Net effect during the cqo smoke session 2026-05-04: chronicler.save.*
INFO/DEBUG logs were entirely absent, hiding what the ingest pipeline
was doing and making issue ck3_chronicler-pxk impossible to triage.

Fix: env.py only calls fileConfig when no handlers are attached to
root yet (i.e. standalone alembic CLI use). Under chronicler dev,
chronicler.logging_setup has already attached handlers, so fileConfig
is skipped and our config survives.
"""

from __future__ import annotations

import logging
from pathlib import Path

from chronicler.cli.main import _alembic_upgrade
from chronicler.logging_setup import setup_logging


def test_alembic_migration_preserves_chronicler_logging(
    tmp_path: Path,
) -> None:
    setup_logging(level=logging.INFO)
    root_handlers_before = list(logging.getLogger().handlers)
    root_level_before = logging.getLogger().level
    chronicler_logger = logging.getLogger("chronicler.save.ingest")
    chronicler_disabled_before = chronicler_logger.disabled

    _alembic_upgrade(tmp_path / "campaign.db")

    assert logging.getLogger().level == root_level_before, (
        "alembic migration must not change root logger level. "
        f"before={root_level_before}, after={logging.getLogger().level}. "
        "If WARNING (30), env.py's fileConfig is leaking alembic.ini's "
        "[logger_root] level into the chronicler runtime."
    )
    assert logging.getLogger().handlers == root_handlers_before, (
        "alembic migration must not replace root logger handlers."
    )
    assert chronicler_logger.disabled == chronicler_disabled_before, (
        "alembic migration must not disable chronicler.save.ingest. "
        "Symptom of disable_existing_loggers leaking through."
    )
    assert chronicler_logger.getEffectiveLevel() == logging.INFO, (
        "chronicler.save.ingest effective level must remain INFO so "
        "ingest pipeline progress is observable in chronicler dev runs."
    )
