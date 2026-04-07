"""
conftest.py — Shared fixtures for all RC-API tests.

IMPORTANT: Environment variables MUST be set before ANY import from src.*
because src/util/settings.py reads os.environ at module level (not lazily).
We do this at the top of this file before any src imports.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
#     Set ALL required env vars HERE, before any src.* import happens.
#     settings.py calls os.environ["CQF_RULER_R4"] at module scope.
# ---------------------------------------------------------------------------
_TEST_ENV = {
    "CQF_RULER_R4": "http://localhost:8080/fhir/",
    "EXTERNAL_FHIR_SERVER_URL": "http://localhost:9090/fhir/",
    "EXTERNAL_FHIR_SERVER_AUTH": "",
    "NLPAAS_URL": "False",  # string "False" → settings.py appends "/" → "False/" (truthy)
    "LOG_LEVEL": "WARNING",  # tests that test "no NLPaaS" patch libraryhandler.nlpaas_url directly
    "API_DOCS": "false",
    "KNOWLEDGEBASE_REPO_URL": "",
    "DOCS_PREPEND_URL": "",
    "DEPLOY_URL": "http://localhost/",
    "DB_CONNECTION_STRING": "sqlite+pysqlite:///rcapi_test.sqlite",
    "DB_SCHEMA": "rcapi",
}
for _key, _val in _TEST_ENV.items():
    os.environ.setdefault(_key, str(_val))


# ---------------------------------------------------------------------------
# Now it is safe to import from src / fastapi / httpx
# ---------------------------------------------------------------------------
import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helper: load a JSON fixture by filename stem
# ---------------------------------------------------------------------------
def load_fixture(name: str) -> dict:
    """Load a JSON fixture file from tests/fixtures/<name>.json"""
    path = FIXTURES_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def load_user_data() -> dict:
    """Load the user-supplied test data fixture."""
    return load_fixture("user_data")


# ---------------------------------------------------------------------------
# App fixture — patches lifespan so no DB/repo startup runs
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def app():
    """Return the FastAPI app with lifespan bypassed for testing."""
    with (
        patch("src.util.databaseclient.startup_connect"),
        patch("src.util.git.clone_repo_to_temp_folder"),
        patch("src.routers.forms_router.init_jobs_array"),
    ):
        from main import app as _app

        yield _app


@pytest.fixture(scope="session")
def client(app):
    """Session-scoped sync TestClient — reused across all tests."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# httpx mock — replaces the global httpx_client used across all routers
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_httpx(monkeypatch):
    """
    Patches `src.util.settings.httpx_client` with a MagicMock and propagates
    it to every module that imports it at the top level.

    Usage in tests:
        def test_foo(client, mock_httpx):
            mock_httpx.get.return_value = make_response(200, {...})
    """
    mock = MagicMock(spec=httpx.Client)
    targets = [
        "src.util.settings.httpx_client",
        "src.routers.cql_router.httpx_client",
        "src.routers.nlpql_router.httpx_client",
        "src.routers.forms_router.httpx_client",
        "src.routers.smartchartui.httpx_client",
        "src.services.libraryhandler.httpx_client",
        "src.models.functions.httpx_client",
        "src.models.forms.httpx_client",  # get_form / save_form_questionnaire
        "src.util.fhirclient.client",  # FhirClient internals
    ]
    for t in targets:
        monkeypatch.setattr(t, mock)
    yield mock


# ---------------------------------------------------------------------------
# FhirClient mock — patches the two FhirClient instances in smartchartui
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_fhir_clients(monkeypatch):
    """
    Patches `external_fhir_client` and `internal_fhir_client` in
    smartchartui.py with MagicMocks.
    Returns (external_mock, internal_mock).
    """
    ext = MagicMock()
    internal = MagicMock()
    monkeypatch.setattr("src.routers.smartchartui.external_fhir_client", ext)
    monkeypatch.setattr("src.routers.smartchartui.internal_fhir_client", internal)
    return ext, internal


# ---------------------------------------------------------------------------
# DB-related mocks — patch jobstate functions that hit SQLite
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_jobstate(monkeypatch):
    """
    Patches all jobstate functions used by smartchartui so tests don't need a
    real database.
    """
    mocks = {}
    for fn in [
        "get_job",
        "get_all_batch_jobs",
        "get_batch_job",
        "delete_batch_job",
        "add_to_batch_jobs",
        "add_to_jobs",
        "update_job_to_complete",
        "get_child_job_statuses",
    ]:
        m = MagicMock()
        monkeypatch.setattr(f"src.routers.smartchartui.{fn}", m)
        mocks[fn] = m
    return mocks


# ---------------------------------------------------------------------------
# Helpers: build mock httpx.Response objects
# ---------------------------------------------------------------------------
def make_response(status_code: int, json_body: dict | None = None, text: str | None = None) -> httpx.Response:
    """
    Build a real httpx.Response (not a mock) with the given status code and body.
    Use json_body for JSON responses or `text` for plain-text (CQL/NLPQL).
    """
    if json_body is not None:
        content = json.dumps(json_body).encode()
        headers = {"content-type": "application/json"}
    elif text is not None:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    else:
        content = b""
        headers = {}
    return httpx.Response(status_code=status_code, content=content, headers=headers)


def make_fhir_searchset(resources: list[dict]) -> dict:
    """Wrap a list of FHIR resources into a minimal searchset Bundle."""
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }
