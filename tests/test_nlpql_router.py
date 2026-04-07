"""
Tests for nlpql_router.py
Endpoints covered:
  GET  /forms/nlpql
  GET  /forms/nlpql/{library_name}
  POST /forms/nlpql
  PUT  /forms/nlpql/{library_name}

Route Ordering Note
-------------------
nlpql_router is now registered BEFORE forms_router in main.py (user fix).
This means GET /forms/nlpql now correctly routes to get_nlpql_libraries().

NLPAAS_URL Note
---------------
In production, NLPAAS_URL env var is the string "False" when NLPaaS isn't
configured. settings.py appends "/" making it "False/". create_nlpql() checks
`if not nlpaas_url:` — "False/" is truthy, so only a truly empty/falsy value
triggers the guard. Tests that want to simulate "no NLPaaS" directly patch
`src.services.libraryhandler.nlpaas_url` to "" (empty string, falsy).
"""

import base64
from unittest.mock import patch

from tests.conftest import load_fixture, make_fhir_searchset, make_response

# ---------------------------------------------------------------------------
# Valid NLPQL that the phenotype library name parser can extract metadata from
# ---------------------------------------------------------------------------
VALID_NLPQL = "\n".join(
    [
        "// Phenotype library TestNLPQLLibrary;",
        'phenotype "TestNLPQLLibrary" version "1.0.0";',
        'documentset myDocs: Clarity.createReportTagList(["Radiology"]);',
    ]
)


# ===========================================================================
# GET /forms/nlpql  — list all NLPQL libraries
# ===========================================================================
class TestGetNlpqlLibraries:
    def test_get_nlpql_libraries_success(self, client, mock_httpx):
        """GET /forms/nlpql → Bundle of NLPQL libraries from CQF Ruler."""
        nlpql_lib = load_fixture("fhir_library_nlpql")
        bundle = make_fhir_searchset([nlpql_lib])
        mock_httpx.get.return_value = make_response(200, bundle)

        response = client.get("/forms/nlpql")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] == 1

    def test_get_nlpql_libraries_empty(self, client, mock_httpx):
        """GET /forms/nlpql → Bundle with total=0 when no NLPQL libraries exist."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)

        response = client.get("/forms/nlpql")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] == 0

    def test_get_nlpql_libraries_server_error(self, client, mock_httpx):
        """GET /forms/nlpql → OperationOutcome when CQF Ruler returns non-200."""
        mock_httpx.get.return_value = make_response(503, {})

        response = client.get("/forms/nlpql")
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"


# ===========================================================================
# GET /forms/nlpql/{library_name}
# ===========================================================================
class TestGetNlpqlByName:
    def test_get_nlpql_by_name_returns_decoded_text(self, client, mock_httpx):
        """GET /forms/nlpql/{name} → plain text NLPQL content when found."""
        nlpql_lib = load_fixture("fhir_library_nlpql")
        bundle = make_fhir_searchset([nlpql_lib])
        mock_httpx.get.return_value = make_response(200, bundle)

        response = client.get("/forms/nlpql/TestNLPQLLibrary")
        expected = base64.b64decode(nlpql_lib["content"][0]["data"]).decode("utf-8")
        assert expected in response.text or response.json() == expected

    def test_get_nlpql_by_name_not_found(self, client, mock_httpx):
        """GET /forms/nlpql/{name} → OperationOutcome not-found when no match."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)

        response = client.get("/forms/nlpql/NonExistentLibrary")
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "not-found"

    def test_get_nlpql_server_error(self, client, mock_httpx):
        """GET /forms/nlpql/{name} → OperationOutcome on server error."""
        mock_httpx.get.return_value = make_response(503, {})

        response = client.get("/forms/nlpql/TestLibrary")
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"


# ===========================================================================
# POST /forms/nlpql
# ===========================================================================
class TestSaveNlpql:
    def test_save_nlpql_no_nlpaas_configured(self, client, mock_httpx):
        """POST /forms/nlpql → 400 OperationOutcome when NLPAAS_URL is not configured.

        Patches nlpaas_url to "" (empty/falsy) to trigger the guard in create_nlpql.
        The default env NLPAAS_URL resolves to "False/" (truthy) in settings.py,
        so we must patch the module-level variable directly.
        """
        with patch("src.services.libraryhandler.nlpaas_url", ""):
            response = client.post("/forms/nlpql", content=VALID_NLPQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "invalid"

    def test_save_nlpql_success(self, client, mock_httpx):
        """POST /forms/nlpql → 201 OperationOutcome when NLPaaS validates and CQF Ruler accepts."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)
        mock_httpx.post.return_value = make_response(201, {"id": "new-nlpql-001"})

        with (
            patch("src.services.libraryhandler.nlpaas_url", "http://nlpaas.example.org/"),
            patch("src.services.libraryhandler.validate_nlpql", return_value=True),
        ):
            response = client.post("/forms/nlpql", content=VALID_NLPQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 201
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["severity"] == "information"

    def test_save_nlpql_empty_body_rejected(self, client):
        """POST /forms/nlpql with empty body → 400 OperationOutcome (custom validation handler)."""
        response = client.post("/forms/nlpql", content="", headers={"Content-Type": "text/plain"})
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_save_nlpql_updates_existing_library(self, client, mock_httpx):
        """POST /forms/nlpql → 201 when library already exists (triggers PUT on CQF Ruler)."""
        nlpql_lib = load_fixture("fhir_library_nlpql")
        existing_bundle = make_fhir_searchset([nlpql_lib])
        mock_httpx.get.return_value = make_response(200, existing_bundle)
        mock_httpx.put.return_value = make_response(200, {"id": "test-nlpql-library-001"})

        with (
            patch("src.services.libraryhandler.nlpaas_url", "http://nlpaas.example.org/"),
            patch("src.services.libraryhandler.validate_nlpql", return_value=True),
        ):
            response = client.post("/forms/nlpql", content=VALID_NLPQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 201

    def test_save_nlpql_validation_fails(self, client, mock_httpx):
        """POST /forms/nlpql → 400 OperationOutcome when NLPQL validation fails."""
        error_oo = {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "invalid", "diagnostics": "Parse error"}],
        }
        with (
            patch("src.services.libraryhandler.nlpaas_url", "http://nlpaas.example.org/"),
            patch("src.services.libraryhandler.validate_nlpql", return_value=error_oo),
        ):
            response = client.post("/forms/nlpql", content=VALID_NLPQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"


# ===========================================================================
# PUT /forms/nlpql/{library_name}
# ===========================================================================
class TestUpdateNlpql:
    def test_update_nlpql_success(self, client, mock_httpx):
        """PUT /forms/nlpql/{name} → 201 OperationOutcome on successful update."""
        nlpql_lib = load_fixture("fhir_library_nlpql")
        existing_bundle = make_fhir_searchset([nlpql_lib])
        mock_httpx.get.return_value = make_response(200, existing_bundle)
        mock_httpx.put.return_value = make_response(200, {"id": "test-nlpql-library-001"})

        with (
            patch("src.services.libraryhandler.nlpaas_url", "http://nlpaas.example.org/"),
            patch("src.services.libraryhandler.validate_nlpql", return_value=True),
        ):
            response = client.put("/forms/nlpql/TestNLPQLLibrary", content=VALID_NLPQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 201
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["severity"] == "information"

    def test_update_nlpql_empty_body_rejected(self, client):
        """PUT /forms/nlpql/{name} with empty body → 400 (custom validation handler)."""
        response = client.put("/forms/nlpql/TestNLPQLLibrary", content="", headers={"Content-Type": "text/plain"})
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
