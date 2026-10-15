"""Shared pytest setup for the migrated test database and test isolation.

Each pytest process migrates its own throwaway SQLite database (never a file in the repository root), and
autouse fixtures clear prompt caches and HTTP-client registries. HTTP is mocked per test with respx (the
`respx_mock` fixture); stateless shared fakes live in `tests.fakes` and builders in `tests.factories`, while
behavior belongs in the test modules."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from src.services.prompt_loader import clear_prompt_cache
from src.util.http_clients import clear_transport_factories, reset_http_clients

_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATABASE_DIR = Path(tempfile.mkdtemp(prefix="rcapi-test-db-"))
_TEST_DATABASE = _TEST_DATABASE_DIR / "rcapi_test.sqlite"
atexit.register(shutil.rmtree, _TEST_DATABASE_DIR, ignore_errors=True)

# Set before any `src` import so settings and the job_state engine bind to the per-run database.
os.environ["DB_CONNECTION_STRING"] = f"sqlite+pysqlite:///{_TEST_DATABASE}"
os.environ["DB_SCHEMA"] = "rcapi"
os.environ["EXTERNAL_FHIR_SERVER_URL"] = "http://localhost:9090/fhir"
os.environ["HAPI_FHIR_CQL_EXECUTION_URL"] = "http://localhost:8080/fhir"


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    config = Config(_ROOT / "alembic.ini")
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
def _reset_prompt_cache():
    clear_prompt_cache()
    yield
    clear_prompt_cache()


@pytest.fixture(autouse=True)
async def _reset_http_client_registry():
    await reset_http_clients()
    clear_transport_factories()
    yield
    await reset_http_clients()
    clear_transport_factories()
