"""Alembic migration environment for the RC-API database."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.schema import CreateSchema

from alembic import context
from src.services.job_state import Base
from src.util.settings import db_connection_string

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", db_connection_string.replace("%", "%%"))
target_metadata = Base.metadata


def _context_options() -> dict:
    schema = target_metadata.schema
    return {
        "target_metadata": target_metadata,
        "include_schemas": bool(schema),
        "version_table_schema": schema,
        "compare_type": True,
    }


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_context_options(),
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
        schema = target_metadata.schema
        if schema:
            connection.execute(CreateSchema(schema, if_not_exists=True))
            connection.commit()
        context.configure(connection=connection, **_context_options())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
