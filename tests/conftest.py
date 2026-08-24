"""Shared pytest setup for a freshly migrated test database."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATABASE = _ROOT / "rcapi_test.sqlite"

os.environ["DB_CONNECTION_STRING"] = f"sqlite+pysqlite:///{_TEST_DATABASE}"
os.environ["DB_SCHEMA"] = "rcapi"
os.environ["EXTERNAL_FHIR_SERVER_URL"] = "http://localhost:9090/fhir"
os.environ["HAPI_FHIR_CQL_EXECUTION_URL"] = "http://localhost:8080/fhir"


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    _TEST_DATABASE.unlink(missing_ok=True)
    config = Config(_ROOT / "alembic.ini")
    command.upgrade(config, "head")
