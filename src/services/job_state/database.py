"""Own the job-state database engine and Alembic startup checks.

This module creates the single engine owner, ensures a configured named schema exists, and verifies
that the connected database is at the Alembic head revision. ``models`` owns metadata and ORM tables;
``batch_queue``, ``batch_records``, ``child_jobs``, and ``responses`` own runtime transactions through
``database.db_engine``. Migration definitions and application domain behavior do not belong here.
"""

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema

from src.services.job_state.models import Base
from src.util.settings import settings

db_engine = create_engine(settings.db_connection_string, pool_pre_ping=True)
_ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[3] / "alembic.ini"


def _ensure_schema_exists() -> None:
    schema = Base.metadata.schema
    if not schema:
        return
    with db_engine.begin() as connection:
        connection.execute(CreateSchema(schema, if_not_exists=True))


def _head_revision() -> str | None:
    config = Config(_ALEMBIC_CONFIG_PATH)
    return ScriptDirectory.from_config(config).get_current_head()


def _current_database_revision() -> str | None:
    with db_engine.connect() as connection:
        migration_context = MigrationContext.configure(
            connection,
            opts={"version_table_schema": Base.metadata.schema},
        )
        return migration_context.get_current_revision()


def initialize_db() -> None:
    """Verify connectivity and require the database to be migrated to Alembic head."""
    try:
        _ensure_schema_exists()
        current_revision = _current_database_revision()
        head_revision = _head_revision()
        if current_revision != head_revision:
            raise RuntimeError(f"Database schema revision is {current_revision or 'unversioned'}; expected {head_revision}. Run 'uv run alembic upgrade head' before starting the API.")
        logger.info(f"Database schema verified at Alembic revision {head_revision}.")
    except Exception as exc:
        logger.error(f"Database initialization failed: {exc}")
        raise
