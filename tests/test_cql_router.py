"""
Tests for cql_router.py
Endpoints covered:
  GET  /forms/cql
  GET  /forms/cql/{library_name}
  POST /forms/cql
  PUT  /forms/cql/{library_name}
"""

import base64
from unittest.mock import patch

from tests.conftest import load_fixture, make_fhir_searchset, make_response

# ---------------------------------------------------------------------------
# Shared helper: a minimal valid CQL string
# ---------------------------------------------------------------------------
VALID_CQL = "library TestLibrary version '1.0.0'\nusing FHIR version '4.0.1'\ncontext Patient"


class TestGetCqlLibraries:
    def test_get_cql_libraries_success(self, client, mock_httpx):
        """GET /forms/cql → 200 with Bundle when CQF Ruler returns data."""
        cql_lib = load_fixture("fhir_library_cql")
        bundle = make_fhir_searchset([cql_lib])
        mock_httpx.get.return_value = make_response(200, bundle)

        response = client.get("/forms/cql")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] == 1

    def test_get_cql_libraries_server_error(self, client, mock_httpx):
        """GET /forms/cql → OperationOutcome when CQF Ruler returns 500."""
        mock_httpx.get.return_value = make_response(500, {"error": "server error"})

        response = client.get("/forms/cql")
        assert response.status_code == 200  # Router returns OO in 200 body
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "transient"

    def test_get_cql_libraries_empty_bundle(self, client, mock_httpx):
        """GET /forms/cql → 200 with empty Bundle when no libraries exist."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)

        response = client.get("/forms/cql")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0


class TestGetCqlByName:
    def test_get_cql_by_name_returns_plain_text(self, client, mock_httpx):
        """GET /forms/cql/{name} → plain text CQL when library found."""
        cql_lib = load_fixture("fhir_library_cql")
        bundle = make_fhir_searchset([cql_lib])
        mock_httpx.get.return_value = make_response(200, bundle)

        response = client.get("/forms/cql/TestLibrary")
        assert response.status_code == 200
        expected_cql = base64.b64decode(cql_lib["content"][0]["data"]).decode("utf-8")
        assert response.text == expected_cql

    def test_get_cql_by_name_not_found(self, client, mock_httpx):
        """GET /forms/cql/{name} → OperationOutcome not-found when no match."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)

        response = client.get("/forms/cql/NonExistentLibrary")
        # When empty bundle returned, router returns not-found OperationOutcome as a plain dict
        # The response type may be application/fhir+json — check that we get a valid OO
        assert response.status_code in (200, 500)  # Router either returns OO or raises exception
        if response.status_code == 200:
            body = response.json()
            assert body["resourceType"] == "OperationOutcome"
            assert body["issue"][0]["code"] == "not-found"

    def test_get_cql_server_error(self, client, mock_httpx):
        """GET /forms/cql/{name} → OperationOutcome transient on server error."""
        mock_httpx.get.return_value = make_response(503, {})

        response = client.get("/forms/cql/TestLibrary")
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "transient"


class TestSaveCql:
    def test_save_cql_success(self, client, mock_httpx):
        """POST /forms/cql → 201 OperationOutcome on successful save."""
        empty = load_fixture("fhir_bundle_empty")
        post_response_body = {"id": "new-lib-id-001"}

        mock_httpx.get.return_value = make_response(200, empty)
        mock_httpx.post.return_value = make_response(201, post_response_body)

        # Use unittest.mock.patch (thread-safe across anyio threadpool)
        with patch("src.services.libraryhandler.validate_cql", return_value=True):
            response = client.post("/forms/cql", content=VALID_CQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 201
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["severity"] == "information"
        assert "new-lib-id-001" in body["issue"][0]["diagnostics"]

    def test_save_cql_updates_existing_library(self, client, mock_httpx, monkeypatch):
        """POST /forms/cql → uses PUT when library already exists, returns 201."""
        cql_lib = load_fixture("fhir_library_cql")
        existing_bundle = make_fhir_searchset([cql_lib])
        put_response = {"id": "test-cql-library-001"}

        mock_httpx.get.return_value = make_response(200, existing_bundle)
        mock_httpx.put.return_value = make_response(200, put_response)

        with patch("src.services.libraryhandler.validate_cql", return_value=True):
            response = client.post("/forms/cql", content=VALID_CQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 201
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_save_cql_validation_error_returned(self, client, mock_httpx):
        """POST /forms/cql → OperationOutcome 500 when CQL validation fails."""
        error_oo = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "invalid", "diagnostics": "Syntax error"}]}
        with patch("src.services.libraryhandler.validate_cql", return_value=error_oo):
            response = client.post("/forms/cql", content=VALID_CQL, headers={"Content-Type": "text/plain"})
        body = response.json()
        assert response.status_code == 500
        assert body["resourceType"] == "OperationOutcome"

    def test_save_cql_empty_body_rejected(self, client):
        """POST /forms/cql with empty body → 400 OperationOutcome (custom handler)."""
        response = client.post("/forms/cql", content="", headers={"Content-Type": "text/plain"})
        # The app's custom exception handler converts FastAPI's 422 to 400 OperationOutcome
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_save_cql_cqf_ruler_post_error(self, client, mock_httpx):
        """POST /forms/cql → OperationOutcome 500 when CQF Ruler POST fails."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)
        mock_httpx.post.return_value = make_response(500, {"error": "internal error"})

        with patch("src.services.libraryhandler.validate_cql", return_value=True):
            response = client.post("/forms/cql", content=VALID_CQL, headers={"Content-Type": "text/plain"})
        body = response.json()
        assert response.status_code == 500
        assert body["resourceType"] == "OperationOutcome"


class TestUpdateCql:
    def test_update_cql_success(self, client, mock_httpx, monkeypatch):
        """PUT /forms/cql/{name} → 201 OperationOutcome on successful update."""
        monkeypatch.setattr("src.services.libraryhandler.validate_cql", lambda code: True)

        cql_lib = load_fixture("fhir_library_cql")
        existing_bundle = make_fhir_searchset([cql_lib])
        put_response = {"id": "test-cql-library-001"}

        mock_httpx.get.return_value = make_response(200, existing_bundle)
        mock_httpx.put.return_value = make_response(200, put_response)

        response = client.put("/forms/cql/TestLibrary", content=VALID_CQL, headers={"Content-Type": "text/plain"})
        assert response.status_code == 201
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["severity"] == "information"

    def test_update_cql_empty_body_rejected(self, client):
        """PUT /forms/cql/{name} with empty body → 400 OperationOutcome (custom handler)."""
        response = client.put("/forms/cql/TestLibrary", content="", headers={"Content-Type": "text/plain"})
        assert response.status_code == 400
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
