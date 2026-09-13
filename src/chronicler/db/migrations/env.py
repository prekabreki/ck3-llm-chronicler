"""Alembic environment.

The DB URL is taken from the ``CHRONICLER_DB_URL`` env var so the same
migrations apply to any per-campaign database. Defaults to a local
``dev.db`` at the repo root for development/testing.
"""

from __future__ import annotations

import logging
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from chronicler.db.models import Base

config = context.config

if config.config_file_name is not None and not logging.getLogger().handlers:
    # ck3_chronicler-7p2: only apply alembic.ini's [loggers] section
    # when nobody else has configured root yet (i.e., standalone
    # ``alembic upgrade head`` from the CLI). When chronicler dev runs
    # migrations, chronicler.logging_setup.setup_logging has already
    # attached our handlers and set root to INFO. fileConfig then
    # applies alembic.ini's [logger_root] level=WARNING, dropping root
    # to WARNING and silencing chronicler.* INFO logs that inherit
    # from it — even with disable_existing_loggers=False, because
    # that flag preserves OTHER loggers but still applies the
    # configured ones (root, sqlalchemy, alembic). Skipping the call
    # leaves chronicler's logging stack intact. Standalone alembic CLI
    # use is unaffected because root has no handlers in that path.
    # Found during cqo smoke session 2026-05-04.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

DEFAULT_URL = "sqlite:///dev.db"
# Programmatic callers (e.g. the CLI) set config.attributes['db_url'] before
# calling alembic.command.upgrade. Otherwise we fall back to the env var.
db_url = (
    (config.attributes.get("db_url") if hasattr(config, "attributes") else None)
    or os.environ.get("CHRONICLER_DB_URL")
    or DEFAULT_URL
)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
