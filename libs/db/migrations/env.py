"""Alembic environment.

Two deviations from the generated template, both deliberate:

1. **The URL comes from ``DATABASE_URL``, not ``alembic.ini``.** A connection string with a password
   must not live in a tracked file.
2. **``compare_type`` and ``compare_server_default`` are on.** Without them autogenerate silently
   ignores a column whose type or default changed, and the migration history stops describing the
   schema — which is only discovered during a restore, when it is too late to fix cheaply.

Importing ``libs.db.models`` is what registers every table on ``Base.metadata``. A model not
imported there is invisible here and produces no migration, with no error (see that module's
docstring).
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from libs.db.models import Base
from libs.db.session import database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
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
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
