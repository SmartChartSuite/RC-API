"""Tests QuestionnaireResponse search, CRUD mapping, batch linkage validation, and metadata preservation.

The tests monkeypatch `src.routers.response` persistence seams and UUID generation; write calls use the
canonical QuestionnaireResponseResource model, while database behavior belongs in the `src.services.job_state`
package tests."""

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import Response
from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, QuestionnaireResponseResource
from src.routers import response as response_router
from tests.factories import expected_author


def _make_record(response_id: str = "response-1") -> SimpleNamespace:
    timestamp = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        response_id=response_id,
        batch_job_id="batch-1",
        job_package="SyphilisRegistry",
        patient_id="patient-123",
        last_updated_by="user-123",
        response={"resourceType": "QuestionnaireResponse", "status": "completed"},
        created_at=timestamp,
        updated_at=timestamp,
    )


async def test_search_responses_returns_fhir_bundle(monkeypatch):
    captured: dict = {}

    def _fake_get_responses(batch_job_id=None, job_package=None):
        captured["batch_job_id"] = batch_job_id
        captured["job_package"] = job_package
        return [_make_record()]

    monkeypatch.setattr(response_router, "get_responses", _fake_get_responses)

    result = await response_router.search_responses(batch_job_id="batch-1", job_package="SyphilisRegistry", claims={"sub": "user-123"})

    assert captured == {"batch_job_id": "batch-1", "job_package": "SyphilisRegistry"}
    assert isinstance(result, BundleResource)
    assert result.type == "searchset"
    assert result.total == 1
    assert result.entry is not None
    assert result.entry[0].resource["resourceType"] == "QuestionnaireResponse"
    assert result.entry[0].resource["id"] == "response-1"


async def test_get_response_record_returns_404_when_missing(monkeypatch):
    monkeypatch.setattr(response_router, "get_response", lambda response_id: None)

    result = await response_router.get_response_record("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404


async def test_get_response_record_returns_questionnaire_response(monkeypatch):
    monkeypatch.setattr(response_router, "get_response", lambda response_id: _make_record(response_id))

    result = await response_router.get_response_record("response-1", claims={})

    assert isinstance(result, QuestionnaireResponseResource)
    assert result.resourceType == "QuestionnaireResponse"
    assert result.id == "response-1"
    assert result.author is None


async def test_create_response_record_sets_location_and_returns_questionnaire_response(monkeypatch):
    monkeypatch.setattr(response_router.uuid, "uuid4", lambda: "response-123")
    captured: dict = {}

    def _fake_create_response(*args):
        captured["response"] = args[5]
        return True

    monkeypatch.setattr(response_router, "create_response", _fake_create_response)
    monkeypatch.setattr(response_router, "get_batch_job", lambda batch_id: SimpleNamespace(batch_id=batch_id, patient_id="patient-123"))

    body = QuestionnaireResponseResource.model_validate(
        {
            "resourceType": "QuestionnaireResponse",
            "questionnaire": "Questionnaire/ExampleRegistry",
            "status": "completed",
            "subject": {"reference": "Patient/patient-123"},
            "author": {"display": "client-supplied"},
        }
    )
    response = Response()

    result = await response_router.create_response_record(body, batch_job_id="batch-1", response=response, claims={"sub": "user-123"})

    assert isinstance(result, QuestionnaireResponseResource)
    assert response.headers["Location"] == "/response/response-123"
    assert result.id == "response-123"
    assert result.resourceType == "QuestionnaireResponse"
    assert captured["response"]["id"] == "response-123"
    assert result.meta is not None
    assert captured["response"]["meta"]["lastUpdated"] == result.meta["lastUpdated"]
    assert captured["response"]["authored"] == result.authored
    assert captured["response"]["author"] == expected_author("user-123", "user-123")
    assert result.author is not None
    assert result.author.to_fhir_json() == captured["response"]["author"]


async def test_create_response_record_returns_400_when_required_fields_missing(monkeypatch):
    body = QuestionnaireResponseResource.model_validate({"resourceType": "QuestionnaireResponse", "status": "completed"})
    response = Response()

    result = await response_router.create_response_record(body, batch_job_id="batch-1", response=response, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


async def test_update_response_record_returns_stamped_questionnaire_response(monkeypatch):
    captured: dict = {}

    def _fake_update_response_body(response_id, body, last_updated_by, updated_at):
        captured["response_id"] = response_id
        captured["body"] = body
        captured["last_updated_by"] = last_updated_by
        captured["updated_at"] = updated_at
        return True

    monkeypatch.setattr(response_router, "update_response_body", _fake_update_response_body)
    monkeypatch.setattr(response_router, "get_response", lambda response_id: _make_record(response_id))

    result = await response_router.update_response_record(
        "response-1",
        QuestionnaireResponseResource.model_validate({"resourceType": "QuestionnaireResponse", "author": {"display": "client-supplied"}}),
        claims={"sub": "user-456"},
    )

    assert isinstance(result, QuestionnaireResponseResource)
    assert result.author is not None
    assert result.author.to_fhir_json() == captured["body"]["author"]
    assert captured["last_updated_by"] == "user-456"
    assert captured["body"]["id"] == "response-1"
    assert captured["body"]["meta"]["lastUpdated"] == captured["body"]["authored"]
    assert captured["body"]["author"] == expected_author("user-456", "user-456")


async def test_delete_response_delegates_to_job_state(monkeypatch):
    deleted = JSONResponse({"resourceType": "OperationOutcome", "issue": []}, status_code=200)
    monkeypatch.setattr(response_router, "delete_response_record", lambda response_id: deleted)

    result = await response_router.delete_response("response-1", _=None)

    assert result is deleted


def _qr_body(patient: str = "patient-123", questionnaire: str = "Questionnaire/ExampleRegistry") -> QuestionnaireResponseResource:
    return QuestionnaireResponseResource.model_validate(
        {"resourceType": "QuestionnaireResponse", "questionnaire": questionnaire, "status": "completed", "subject": {"reference": f"Patient/{patient}"}}
    )


async def test_create_response_record_returns_404_for_unknown_batch(monkeypatch):
    monkeypatch.setattr(response_router, "get_batch_job", lambda batch_id: None)
    monkeypatch.setattr(response_router, "create_response", lambda *args: (_ for _ in ()).throw(AssertionError("must not persist")))

    result = await response_router.create_response_record(_qr_body(), batch_job_id="nope", response=Response(), claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404


async def test_create_response_record_rejects_patient_mismatch_with_batch(monkeypatch):
    monkeypatch.setattr(response_router, "get_batch_job", lambda batch_id: SimpleNamespace(batch_id=batch_id, patient_id="someone-else"))
    monkeypatch.setattr(response_router, "create_response", lambda *args: (_ for _ in ()).throw(AssertionError("must not persist")))

    result = await response_router.create_response_record(_qr_body(), batch_job_id="batch-1", response=Response(), claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


async def test_update_response_record_returns_404_when_missing(monkeypatch):
    monkeypatch.setattr(response_router, "get_response", lambda response_id: None)

    result = await response_router.update_response_record("missing", QuestionnaireResponseResource.model_validate({"resourceType": "QuestionnaireResponse"}), claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404


async def test_update_response_record_rejects_relinking_subject(monkeypatch):
    monkeypatch.setattr(response_router, "get_response", lambda response_id: _make_record(response_id))
    monkeypatch.setattr(response_router, "update_response_body", lambda *args: (_ for _ in ()).throw(AssertionError("must not persist")))

    result = await response_router.update_response_record(
        "response-1",
        QuestionnaireResponseResource.model_validate({"resourceType": "QuestionnaireResponse", "subject": {"reference": "Patient/other-patient"}}),
        claims={},
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


async def test_update_response_record_preserves_stored_linkage(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(response_router, "get_response", lambda response_id: _make_record(response_id))

    def _fake_update(response_id, body, last_updated_by, updated_at):
        captured["body"] = body
        return True

    monkeypatch.setattr(response_router, "update_response_body", _fake_update)

    await response_router.update_response_record("response-1", QuestionnaireResponseResource.model_validate({"resourceType": "QuestionnaireResponse", "status": "completed"}), claims={})

    assert captured["body"]["subject"] == {"reference": "Patient/patient-123"}
    assert captured["body"]["questionnaire"] == "SyphilisRegistry"
