"""
Tests for smartchartui.py
Endpoints covered:
  GET    /smartchartui/Patient/{patient_id}
  GET    /smartchartui/Patient
  GET    /smartchartui/group
  GET    /smartchartui/questionnaire
  GET    /smartchartui/job/{id}
  GET    /smartchartui/batchjob
  GET    /smartchartui/batchjob/{id}
  DELETE /smartchartui/batchjob/{id}
  POST   /smartchartui/batchjob
  GET    /smartchartui/results/{id}
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import load_fixture, load_user_data, make_fhir_searchset, make_response


# ---------------------------------------------------------------------------
# Helpers to build realistic job / batchjob dicts for DB mocks
# ---------------------------------------------------------------------------
def _make_job_dict(job_id: str, status: str = "complete") -> dict:
    """Build a minimal ParametersJob dict as it would be stored in the DB."""
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "jobId", "valueString": job_id},
            {"name": "jobStartDateTime", "valueDateTime": "2024-01-01T00:00:00Z"},
            {"name": "jobStatus", "valueString": status},
            {"name": "result", "resource": {"resourceType": "Bundle", "type": "collection", "entry": []}},
            {"name": "jobCompletedDateTime", "valueDateTime": "2024-01-01T00:05:00Z"},
        ],
    }


def _make_batch_job_dict(batch_id: str, child_ids: list[str]) -> dict:
    """Build a minimal BatchParametersJob dict as stored in the DB."""
    entries = [{"item": {"display": cid}} for cid in child_ids]
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "batchId", "valueString": batch_id},
            {"name": "jobStartDateTime", "valueDateTime": "2024-01-01T00:00:00Z"},
            {"name": "patientId", "valueString": "test-patient-001"},
            {"name": "jobPackage", "valueString": "TestQuestionnaire"},
            {
                "name": "childJobs",
                "resource": {"resourceType": "List", "status": "current", "mode": "working", "entry": entries},
            },
        ],
    }


# ---------------------------------------------------------------------------
# POST body for creating a batch job
# ---------------------------------------------------------------------------
BATCH_JOB_BODY = {
    "resourceType": "Parameters",
    "parameter": [
        {"name": "patientId", "valueString": "test-patient-001"},
        {"name": "jobPackage", "valueString": "TestQuestionnaire"},
    ],
}


# ===========================================================================
# Patient endpoints
# ===========================================================================
class TestReadPatient:
    def test_read_patient_success(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient/{id} → Patient resource JSON."""
        ext_client, _ = mock_fhir_clients
        patient = load_fixture("fhir_patient")
        mock_resp = MagicMock()
        mock_resp.json.return_value = patient
        ext_client.readResource.return_value = mock_resp

        response = client.get(f"/smartchartui/Patient/{patient['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Patient"
        assert body["id"] == patient["id"]

    def test_read_patient_extracts_resource_type_from_response(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient/{id} → response contains Patient resource type."""
        ext_client, _ = mock_fhir_clients
        patient = load_fixture("fhir_patient")
        mock_resp = MagicMock()
        mock_resp.json.return_value = patient
        ext_client.readResource.return_value = mock_resp

        response = client.get(f"/smartchartui/Patient/{patient['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Patient"


class TestSearchPatient:
    def test_search_patient_no_params_returns_all(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient → searches all patients when no params given."""
        ext_client, _ = mock_fhir_clients
        patient = load_fixture("fhir_patient")
        bundle = make_fhir_searchset([patient])
        ext_client.searchResource.return_value = bundle

        response = client.get("/smartchartui/Patient")
        assert response.status_code == 200
        ext_client.searchResource.assert_called_once_with("Patient")

    def test_search_patient_by_id(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient?_id=xxx → searches by _id parameter."""
        ext_client, _ = mock_fhir_clients
        patient = load_fixture("fhir_patient")
        bundle = make_fhir_searchset([patient])
        ext_client.searchResource.return_value = bundle

        response = client.get("/smartchartui/Patient?_id=test-patient-001")
        assert response.status_code == 200
        call_args = ext_client.searchResource.call_args
        assert call_args[1]["parameters"] == {"_id": "test-patient-001"}

    def test_search_patient_by_name(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient?name=John → searches by name."""
        ext_client, _ = mock_fhir_clients
        patient = load_fixture("fhir_patient")
        bundle = make_fhir_searchset([patient])
        ext_client.searchResource.return_value = bundle

        response = client.get("/smartchartui/Patient?name=John")
        assert response.status_code == 200
        call_args = ext_client.searchResource.call_args
        assert "name" in call_args[1]["parameters"]

    def test_search_patient_by_identifier(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient?identifier=MRN-001 → searches by identifier."""
        ext_client, _ = mock_fhir_clients
        patient = load_fixture("fhir_patient")
        bundle = make_fhir_searchset([patient])
        ext_client.searchResource.return_value = bundle

        response = client.get("/smartchartui/Patient?identifier=MRN-001")
        assert response.status_code == 200
        call_args = ext_client.searchResource.call_args
        assert call_args[1]["parameters"] == {"identifier": "MRN-001"}

    def test_search_patient_by_name_and_birthdate(self, client, mock_fhir_clients):
        """GET /smartchartui/Patient?name=John&birthdate=1990-01-15 → searches both params."""
        ext_client, _ = mock_fhir_clients
        bundle = make_fhir_searchset([])
        ext_client.searchResource.return_value = bundle

        response = client.get("/smartchartui/Patient?name=John&birthdate=1990-01-15")
        assert response.status_code == 200
        call_args = ext_client.searchResource.call_args
        assert "name" in call_args[1]["parameters"]
        assert "birthdate" in call_args[1]["parameters"]


# ===========================================================================
# Group endpoint
# ===========================================================================
class TestSearchGroup:
    def test_search_group_returns_list(self, client, mock_fhir_clients, mock_httpx):
        """GET /smartchartui/group → list containing Group and Patient resources."""
        _, internal_client = mock_fhir_clients
        patient = load_fixture("fhir_patient")

        group = {
            "resourceType": "Group",
            "id": "test-group-001",
            "type": "person",
            "actual": True,
            "member": [{"entity": {"reference": "http://localhost:9090/fhir/Patient/test-patient-001"}}],
        }
        internal_client.searchResource.return_value = [group]

        mock_patient_resp = MagicMock()
        mock_patient_resp.json.return_value = patient
        mock_httpx.get.return_value = mock_patient_resp

        response = client.get("/smartchartui/group")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        # Should contain the Group + the Patient from the member reference
        resource_types = [item.get("resourceType") for item in body]
        assert "Group" in resource_types

    def test_search_group_empty(self, client, mock_fhir_clients):
        """GET /smartchartui/group → empty list when no groups exist."""
        _, internal_client = mock_fhir_clients
        internal_client.searchResource.return_value = []

        response = client.get("/smartchartui/group")
        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# Questionnaire endpoint
# ===========================================================================
class TestSearchQuestionnaire:
    def test_search_questionnaire_returns_list(self, client, mock_fhir_clients):
        """GET /smartchartui/questionnaire → list of Questionnaires."""
        _, internal_client = mock_fhir_clients
        questionnaire = load_fixture("fhir_questionnaire")
        internal_client.searchResource.return_value = [questionnaire]

        response = client.get("/smartchartui/questionnaire")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert body[0]["resourceType"] == "Questionnaire"

    def test_search_questionnaire_empty(self, client, mock_fhir_clients):
        """GET /smartchartui/questionnaire → empty list when no questionnaires."""
        _, internal_client = mock_fhir_clients
        internal_client.searchResource.return_value = []

        response = client.get("/smartchartui/questionnaire")
        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# Job (child job) endpoint
# ===========================================================================
class TestGetJob:
    def test_get_job_not_found(self, client, mock_jobstate):
        """GET /smartchartui/job/{id} → 404 OperationOutcome when job not in DB."""
        mock_jobstate["get_job"].return_value = None

        response = client.get("/smartchartui/job/nonexistent-job-id")
        assert response.status_code == 404
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "not-found"

    def test_get_job_found(self, client, mock_jobstate):
        """GET /smartchartui/job/{id} → returns job dict when found in DB."""
        job_id = str(uuid.uuid4())
        job = _make_job_dict(job_id)
        mock_jobstate["get_job"].return_value = job

        response = client.get(f"/smartchartui/job/{job_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Parameters"


# ===========================================================================
# Batch job GET endpoints
# ===========================================================================
class TestGetAllBatchJobs:
    def test_get_all_batch_jobs_empty(self, client, mock_jobstate):
        """GET /smartchartui/batchjob → empty list when no batch jobs in DB."""
        mock_jobstate["get_all_batch_jobs"].return_value = []

        response = client.get("/smartchartui/batchjob")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_all_batch_jobs_includes_status(self, client, mock_jobstate):
        """GET /smartchartui/batchjob → batch job dicts include batchJobStatus."""
        batch_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        batch_job = _make_batch_job_dict(batch_id, [child_id])

        mock_jobstate["get_all_batch_jobs"].return_value = [batch_job]
        mock_jobstate["get_child_job_statuses"].return_value = {child_id: "complete"}

        response = client.get("/smartchartui/batchjob")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 1
        param_names = [p["name"] for p in body[0]["parameter"]]
        assert "batchJobStatus" in param_names


class TestGetBatchJobById:
    def test_get_batch_job_not_found(self, client, mock_jobstate):
        """GET /smartchartui/batchjob/{id} → 404 when batch job not in DB."""
        mock_jobstate["get_batch_job"].return_value = None

        response = client.get("/smartchartui/batchjob/nonexistent-id")
        assert response.status_code == 404
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_get_batch_job_found(self, client, mock_jobstate):
        """GET /smartchartui/batchjob/{id} → returns batch job dict when found."""
        batch_id = str(uuid.uuid4())
        batch_job = _make_batch_job_dict(batch_id, [])
        mock_jobstate["get_batch_job"].return_value = batch_job

        response = client.get(f"/smartchartui/batchjob/{batch_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Parameters"


# ===========================================================================
# Delete batch job
# ===========================================================================
class TestDeleteBatchJob:
    def test_delete_batch_job_success(self, client, mock_jobstate):
        """DELETE /smartchartui/batchjob/{id} → OperationOutcome deleted success."""
        from fastapi.responses import JSONResponse

        from src.services.errorhandler import make_operation_outcome

        batch_id = str(uuid.uuid4())
        oo = make_operation_outcome("deleted", f"Batch Job ID {batch_id} has been successfully deleted from the database", "information")
        mock_jobstate["delete_batch_job"].return_value = JSONResponse(oo)

        response = client.delete(f"/smartchartui/batchjob/{batch_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_delete_batch_job_not_found(self, client, mock_jobstate):
        """DELETE /smartchartui/batchjob/{id} → 404 when batch job not in DB."""
        from fastapi.responses import JSONResponse

        from src.services.errorhandler import make_operation_outcome

        batch_id = "nonexistent"
        oo = make_operation_outcome("not-found", f"Batch Job ID {batch_id} was not found in the database")
        mock_jobstate["delete_batch_job"].return_value = JSONResponse(oo, status_code=404)

        response = client.delete(f"/smartchartui/batchjob/{batch_id}")
        assert response.status_code == 404


# ===========================================================================
# POST batch job
# ===========================================================================
class TestPostBatchJob:
    def test_post_batch_job_creates_job(self, client, mock_fhir_clients, mock_jobstate, mock_httpx):
        """POST /smartchartui/batchjob → creates batch job and returns it with Location header."""
        questionnaire = load_fixture("fhir_questionnaire")

        # Use unittest.mock.patch (thread-safe across anyio threadpool boundary)
        with patch("src.routers.smartchartui.get_form", return_value=questionnaire):
            # add_to_batch_jobs succeeds
            mock_jobstate["add_to_batch_jobs"].return_value = True
            response = client.post("/smartchartui/batchjob", json=BATCH_JOB_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Parameters"
        assert "Location" in response.headers

    def test_post_batch_job_db_failure_returns_500(self, client, mock_fhir_clients, mock_jobstate, mock_httpx):
        """POST /smartchartui/batchjob → 500 OperationOutcome when DB insert fails."""
        questionnaire = load_fixture("fhir_questionnaire")

        with patch("src.routers.smartchartui.get_form", return_value=questionnaire):
            mock_jobstate["add_to_batch_jobs"].return_value = False
            response = client.post("/smartchartui/batchjob", json=BATCH_JOB_BODY)

        assert response.status_code == 500
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"


# ===========================================================================
# Results endpoint
# ===========================================================================
class TestGetBatchJobResults:
    def test_get_results_not_found(self, client, mock_jobstate):
        """GET /smartchartui/results/{id} → 404 when batch job not in DB."""
        mock_jobstate["get_batch_job"].return_value = None

        response = client.get("/smartchartui/results/nonexistent-id")
        assert response.status_code == 404
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_get_results_with_complete_jobs(self, client, mock_jobstate, mock_fhir_clients, mock_httpx):
        """GET /smartchartui/results/{id} → Bundle with status observation when all jobs complete."""
        _, ext_client = mock_fhir_clients
        batch_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        patient = load_fixture("fhir_patient")

        batch_job = _make_batch_job_dict(batch_id, [child_id])
        child_job = _make_job_dict(child_id, status="complete")

        mock_jobstate["get_batch_job"].return_value = batch_job
        mock_jobstate["get_job"].return_value = child_job

        # external_fhir_client.readResource for the Patient
        mock_patient_response = MagicMock()
        mock_patient_response.json.return_value = patient
        ext_client = mock_fhir_clients[0]
        ext_client.readResource.return_value = mock_patient_response

        response = client.get(f"/smartchartui/results/{batch_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Bundle"


# ===========================================================================
# Data-driven tests using user_data.json
# ===========================================================================
class TestBatchJobUserData:
    @pytest.mark.parametrize("scenario", load_user_data().get("batch_jobs_requests", []))
    def test_batch_job_with_real_data(self, client, mock_fhir_clients, mock_jobstate, mock_httpx, scenario):
        """POST /smartchartui/batchjob with user-provided patient and job package data."""
        patient_param = next((p for p in scenario["parameter"] if p["name"] == "patientId"), None)
        if not patient_param or patient_param["valueString"] == "REPLACE_ME":
            pytest.skip("User data not yet populated in tests/fixtures/user_data.json")

        questionnaire = load_fixture("fhir_questionnaire")
        bundle = make_fhir_searchset([questionnaire])
        mock_httpx.get.return_value = make_response(200, bundle)
        mock_jobstate["add_to_batch_jobs"].return_value = True

        response = client.post("/smartchartui/batchjob", json=scenario)
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "Parameters"
        param_names = [p["name"] for p in body["parameter"]]
        assert "batchId" in param_names


class TestPatientSearchUserData:
    @pytest.mark.parametrize("patient", load_user_data().get("patients", []))
    def test_read_patient_with_real_id(self, client, mock_fhir_clients, patient):
        """GET /smartchartui/Patient/{id} with user-provided patient ID."""
        if patient["id"] == "REPLACE_ME":
            pytest.skip("User data not yet populated in tests/fixtures/user_data.json")

        ext_client, _ = mock_fhir_clients
        patient_fixture = load_fixture("fhir_patient")
        patient_fixture["id"] = patient["id"]

        mock_resp = MagicMock()
        mock_resp.json.return_value = patient_fixture
        ext_client.readResource.return_value = mock_resp

        response = client.get(f"/smartchartui/Patient/{patient['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == patient.get("expected_resource_type", "Patient")
        assert body["id"] == patient["id"]
