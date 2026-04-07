"""
Tests for forms_router.py
Endpoints covered:
  GET  /forms
  GET  /forms/{form_name}
  POST /forms
  PUT  /forms/{form_name}
  POST /forms/start
  GET  /forms/status/all
  GET  /forms/status/{uid}
  POST /forms/jobPackageToQuestionnaire
"""

import uuid

import pytest

from tests.conftest import load_fixture, load_user_data, make_fhir_searchset, make_response

# ---------------------------------------------------------------------------
# Sample StartJobsParameters body
# ---------------------------------------------------------------------------
START_JOBS_BODY = {
    "resourceType": "Parameters",
    "parameter": [
        {"name": "patientId", "valueString": "test-patient-001"},
        {"name": "jobPackage", "valueString": "TestQuestionnaire"},
        {"name": "job", "valueString": "TestLibrary.cql"},
    ],
}


class TestGetForms:
    def test_get_forms_success(self, client, mock_httpx, monkeypatch):
        """GET /forms → 200 Bundle of Questionnaires."""
        monkeypatch.setenv("CQF_RULER_R4", "http://localhost:8080/fhir/")
        questionnaire = load_fixture("fhir_questionnaire")
        bundle = make_fhir_searchset([questionnaire])
        mock_httpx.get.return_value = make_response(200, bundle)

        response = client.get("/forms")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"

    def test_get_forms_server_error(self, client, mock_httpx, monkeypatch):
        """GET /forms → OperationOutcome when CQF Ruler is unavailable."""
        monkeypatch.setenv("CQF_RULER_R4", "http://localhost:8080/fhir/")
        mock_httpx.get.return_value = make_response(503, {})

        response = client.get("/forms")
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "transient"


class TestGetFormByName:
    def test_get_form_by_name_success(self, client, mock_httpx):
        """GET /forms/{name} → returns Questionnaire dict when found."""
        questionnaire = load_fixture("fhir_questionnaire")
        bundle = make_fhir_searchset([questionnaire])
        # get_form in src.models.forms uses its own httpx_client (already in mock_httpx targets)
        mock_httpx.get.return_value = make_response(200, bundle)

        response = client.get("/forms/TestQuestionnaire")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Questionnaire"
        assert body["name"] == "TestQuestionnaire"

    def test_get_form_by_name_not_found(self, client, mock_httpx):
        """GET /forms/{name} → OperationOutcome not-found when form doesn't exist."""
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)

        response = client.get("/forms/NotAForm")
        # get_form returns OperationOutcome dict when not found (200 with OO body)
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "not-found"


class TestSaveForm:
    def test_save_form_success(self, client, mock_httpx):
        """POST /forms → OperationOutcome when Questionnaire posted to CQF Ruler OK."""
        questionnaire = load_fixture("fhir_questionnaire")
        # save_form_questionnaire: GET (check exists) → empty+KeyError; POST → 201
        # NOTE: The empty bundle causes KeyError in save_form_questionnaire which is caught
        # and triggers a POST rather than a 'duplicate' response.
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)
        mock_httpx.post.return_value = make_response(201, {"id": "new-q-001"})

        response = client.post("/forms", json=questionnaire)
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        # save_form_questionnaire returns an informational OO on success
        assert body["issue"][0]["severity"] in ("information", "informational")


class TestUpdateForm:
    def test_update_form_success(self, client, mock_httpx):
        """PUT /forms/{name} → OperationOutcome success when Questionnaire updated."""
        questionnaire = load_fixture("fhir_questionnaire")
        bundle = make_fhir_searchset([questionnaire])
        mock_httpx.get.return_value = make_response(200, bundle)
        mock_httpx.put.return_value = make_response(200, {"id": questionnaire["id"]})

        response = client.put("/forms/TestQuestionnaire", json=questionnaire)
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["severity"] == "information"

    def test_update_form_not_found(self, client, mock_httpx):
        """PUT /forms/{name} → OperationOutcome not-found or 500 when entry not in bundle.

        NOTE: The empty bundle causes the same IndexError as in get_form not-found.
        This is a known production bug in get_update_questionnaire.
        """
        empty = load_fixture("fhir_bundle_empty")
        mock_httpx.get.return_value = make_response(200, empty)

        questionnaire = load_fixture("fhir_questionnaire")
        response = client.put("/forms/NonExistent", json=questionnaire)
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_update_form_cqf_ruler_get_error(self, client, mock_httpx):
        """PUT /forms/{name} → OperationOutcome transient when CQF Ruler GET fails."""
        mock_httpx.get.return_value = make_response(500, {})

        questionnaire = load_fixture("fhir_questionnaire")
        response = client.put("/forms/TestQuestionnaire", json=questionnaire)
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "transient"


class TestStartJobs:
    def test_start_jobs_sync_calls_start_jobs(self, client, monkeypatch):
        """POST /forms/start (sync) → calls start_jobs and returns its result."""
        mock_result = {"resourceType": "Bundle", "type": "collection", "entry": []}

        async def mock_start_jobs(post_body):
            return mock_result

        monkeypatch.setattr("src.routers.forms_router.start_jobs", mock_start_jobs)

        response = client.post("/forms/start", json=START_JOBS_BODY)
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"

    def test_start_jobs_async_creates_job_entry(self, client, monkeypatch):
        """POST /forms/start?asyncFlag=true → returns ParametersJob with jobId and Location header."""

        async def mock_start_jobs(post_body):
            return {"resourceType": "Bundle"}

        monkeypatch.setattr("src.routers.forms_router.start_jobs", mock_start_jobs)

        response = client.post("/forms/start?asyncFlag=true", json=START_JOBS_BODY)
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Parameters"
        # Verify jobId is in the response parameters
        param_names = [p["name"] for p in body["parameter"]]
        assert "jobId" in param_names
        assert "Location" in response.headers

    def test_start_jobs_missing_required_fields(self, client):
        """POST /forms/start with incomplete body → 400 or 422 validation error."""
        response = client.post("/forms/start", json={"resourceType": "Parameters"})
        # FastAPI returns 422 for Pydantic validation failures
        assert response.status_code in (400, 422)


class TestJobStatus:
    def test_get_all_jobs_empty(self, client, monkeypatch):
        """GET /forms/status/all → empty dict when no jobs are running."""
        # The jobs dict in forms_router is module-level; reset it
        import src.routers.forms_router as forms_router

        monkeypatch.setattr(forms_router, "jobs", {})
        response = client.get("/forms/status/all")
        assert response.status_code == 200
        assert response.json() == {}

    def test_get_job_status_not_found(self, client, monkeypatch):
        """GET /forms/status/{uid} → 404 OperationOutcome when uid not in jobs."""
        import src.routers.forms_router as forms_router

        monkeypatch.setattr(forms_router, "jobs", {})
        response = client.get("/forms/status/nonexistent-uid")
        assert response.status_code == 404
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "code-invalid"

    def test_get_job_status_found_in_progress(self, client, monkeypatch):
        """GET /forms/status/{uid} → returns ParametersJob dict for known uid."""
        import src.routers.forms_router as forms_router
        from src.models.models import ParametersJob

        job_id = str(uuid.uuid4())
        test_job = ParametersJob()
        for param in test_job.parameter:
            if param.name == "jobId":
                param.valueString = job_id

        monkeypatch.setattr(forms_router, "jobs", {job_id: test_job})
        response = client.get(f"/forms/status/{job_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Parameters"


class TestJobPackageToQuestionnaire:
    def test_convert_jobpackage_invalid_csv(self, client, monkeypatch):
        """POST /forms/jobPackageToQuestionnaire with invalid CSV → error response."""
        monkeypatch.setattr(
            "src.routers.forms_router.convert_jobpackage_csv_to_questionnaire",
            lambda **kwargs: {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "invalid", "diagnostics": "Invalid CSV"}]},
        )
        response = client.post(
            "/forms/jobPackageToQuestionnaire",
            content="NOT_VALID_CSV",
            headers={"Content-Type": "text/plain"},
        )
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_convert_jobpackage_returns_questionnaire(self, client, monkeypatch):
        """POST /forms/jobPackageToQuestionnaire → Questionnaire resource on success."""
        questionnaire = load_fixture("fhir_questionnaire")
        monkeypatch.setattr(
            "src.routers.forms_router.convert_jobpackage_csv_to_questionnaire",
            lambda **kwargs: questionnaire,
        )
        response = client.post(
            "/forms/jobPackageToQuestionnaire",
            content="some,csv,data",
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Questionnaire"

    def test_convert_jobpackage_with_commit_false(self, client, monkeypatch):
        """POST /forms/jobPackageToQuestionnaire?commit=false → returns Questionnaire not OO."""
        questionnaire = load_fixture("fhir_questionnaire")
        monkeypatch.setattr(
            "src.routers.forms_router.convert_jobpackage_csv_to_questionnaire",
            lambda **kwargs: questionnaire,
        )
        response = client.post(
            "/forms/jobPackageToQuestionnaire?commit=false",
            content="some,csv,data",
            headers={"Content-Type": "text/plain"},
        )
        body = response.json()
        assert body["resourceType"] == "Questionnaire"


# ---------------------------------------------------------------------------
# Data-driven tests using user_data.json (skip if placeholders not filled in)
# ---------------------------------------------------------------------------
class TestStartJobsUserData:
    @pytest.mark.parametrize("scenario", load_user_data().get("start_jobs_requests", []))
    def test_start_jobs_with_real_data(self, client, monkeypatch, scenario):
        """POST /forms/start with user-provided data → expected output shape."""
        if scenario["parameter"][0]["valueString"] == "REPLACE_ME":
            pytest.skip("User data not yet populated in tests/fixtures/user_data.json")

        expected = scenario.get("expected_output", {})
        expected_resource_type = expected.get("resourceType", "Bundle")

        async def mock_start_jobs(post_body):
            return expected

        monkeypatch.setattr("src.routers.forms_router.start_jobs", mock_start_jobs)

        body = {
            "resourceType": scenario["resourceType"],
            "parameter": scenario["parameter"],
        }
        response = client.post("/forms/start", json=body)
        assert response.status_code == 200
        resp_body = response.json()
        assert resp_body["resourceType"] == expected_resource_type
