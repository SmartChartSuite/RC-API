from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Response
from fastapi.responses import JSONResponse

from src.models.fhir import OperationOutcome
from src.models.response import ResponseCreatedResponse, ResponseParameter, ResponseRecord, ResponseRequest
from src.routers import response as response_router


def _make_record(response_id: str = "response-1") -> SimpleNamespace:
    timestamp = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        response_id=response_id,
        batch_job_id="batch-1",
        job_package="SyphilisRegistry",
        patient_id="patient-123",
        user_id="user-123",
        response={"resourceType": "QuestionnaireResponse", "status": "completed"},
        created_at=timestamp,
        updated_at=timestamp,
    )


async def test_search_responses_filters_by_user(monkeypatch):
    captured: dict = {}

    def _fake_get_responses(batch_job_id=None, job_package=None, user_id=None):
        captured["batch_job_id"] = batch_job_id
        captured["job_package"] = job_package
        captured["user_id"] = user_id
        return [_make_record()]

    monkeypatch.setattr(response_router, "get_responses", _fake_get_responses)

    result = await response_router.search_responses(batch_job_id="batch-1", job_package="SyphilisRegistry", claims={"sub": "user-123"})

    assert captured == {"batch_job_id": "batch-1", "job_package": "SyphilisRegistry", "user_id": "user-123"}
    assert isinstance(result[0], ResponseRecord)
    assert result[0].responseId == "response-1"


async def test_get_response_record_returns_404_when_missing(monkeypatch):
    monkeypatch.setattr(response_router, "get_response", lambda response_id: None)

    result = await response_router.get_response_record("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404


async def test_create_response_record_sets_location_and_returns_parameters(monkeypatch):
    monkeypatch.setattr(response_router.uuid, "uuid4", lambda: "response-123")
    monkeypatch.setattr(response_router, "create_response", lambda *args: True)

    body = ResponseRequest(
        parameter=[
            ResponseParameter(name="batchJobId", valueString="batch-1"),
            ResponseParameter(name="jobPackage", valueString="SyphilisRegistry"),
            ResponseParameter(name="patientId", valueString="patient-123"),
            ResponseParameter(name="response", resource={"resourceType": "QuestionnaireResponse", "status": "completed"}),
        ]
    )
    response = Response()

    result = await response_router.create_response_record(body, response, claims={"sub": "user-123"})

    assert isinstance(result, ResponseCreatedResponse)
    assert response.headers["Location"] == "/response/response-123"
    assert result.parameter[0].valueString == "response-123"


async def test_update_response_record_returns_operation_outcome(monkeypatch):
    monkeypatch.setattr(response_router, "update_response_body", lambda response_id, body: True)

    result = await response_router.update_response_record("response-1", {"resourceType": "QuestionnaireResponse"}, claims={})

    assert isinstance(result, OperationOutcome)
    assert result.issue[0].diagnostics == "Response response-1 updated."


async def test_delete_response_delegates_to_job_state(monkeypatch):
    deleted = JSONResponse({"resourceType": "OperationOutcome", "issue": []}, status_code=200)
    monkeypatch.setattr(response_router, "delete_response_record", lambda response_id: deleted)

    result = await response_router.delete_response("response-1", _=None)

    assert result is deleted
