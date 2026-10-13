from fastapi.responses import JSONResponse
from starlette.requests import Request

from src.models.fhir import BundleResource, PatientResource
from src.routers import patient
from tests.fakes import FailingAsyncClient as _FailingClient
from tests.fakes import FakeAsyncClient as _FakeAsyncClient
from tests.fakes import FakeResponse as _FakeResponse


def _make_request(query_string: str = "") -> Request:
    return Request({"type": "http", "query_string": query_string.encode()})


async def test_search_patients_proxies_query_params(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {"resource": {"resourceType": "Patient", "id": "patient-123"}},
                    ],
                }
            )
        ]
    ]

    request = _make_request("name=Smith&birthdate=1990-01-01")
    monkeypatch.setattr(patient, "config_errors", {})
    monkeypatch.setattr(patient, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(patient, "external_fhir_server_auth", "Bearer token")
    monkeypatch.setattr(patient.httpx, "AsyncClient", _FakeAsyncClient)

    result = await patient.search_patients(request, claims={})

    assert isinstance(result, BundleResource)
    assert _FakeAsyncClient.calls[0]["url"] == "http://external.example/fhir/Patient"
    assert _FakeAsyncClient.calls[0]["params"] == [("name", "Smith"), ("birthdate", "1990-01-01")]
    assert _FakeAsyncClient.calls[0]["headers"] == {"Accept": "application/fhir+json", "Authorization": "Bearer token"}


async def test_get_patient_returns_patient_resource(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [[_FakeResponse({"resourceType": "Patient", "id": "patient-123"})]]

    monkeypatch.setattr(patient, "config_errors", {})
    monkeypatch.setattr(patient, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(patient.httpx, "AsyncClient", _FakeAsyncClient)

    result = await patient.get_patient("patient-123", claims={})

    assert isinstance(result, PatientResource)
    assert result.id == "patient-123"


async def test_get_patient_returns_operation_outcome(monkeypatch):
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
                    "resourceType": "OperationOutcome",
                    "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing patient"}],
                },
                status_code=404,
            )
        ]
    ]

    monkeypatch.setattr(patient, "config_errors", {})
    monkeypatch.setattr(patient, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(patient.httpx, "AsyncClient", _FakeAsyncClient)

    result = await patient.get_patient("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404
    assert bytes(result.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing patient"}]}'


async def test_search_patients_returns_config_error(monkeypatch):
    request = _make_request()
    monkeypatch.setattr(patient, "config_errors", {"EXTERNAL_FHIR_SERVER_URL": "missing"})

    result = await patient.search_patients(request, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503


async def test_search_patients_handles_request_errors(monkeypatch):
    request = _make_request("name=Smith")
    monkeypatch.setattr(patient, "config_errors", {})
    monkeypatch.setattr(patient, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(patient.httpx, "AsyncClient", _FailingClient)

    result = await patient.search_patients(request, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 502
    assert (
        bytes(result.body).decode("utf-8")
        == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"transient","diagnostics":"External FHIR server request failed for GET Patient"}]}'
    )
