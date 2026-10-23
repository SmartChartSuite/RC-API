"""Tests the Patient router's external-FHIR search/get behavior, query forwarding, and error responses.

The tests use respx routes for external FHIR traffic and patch settings; raw external FHIR fetching is covered
in `test_external_fhir.py`."""

import httpx
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from starlette.requests import Request

from src.models.fhir import BundleResource, PatientResource
from src.routers import patient
from src.util.settings import settings
from tests.fakes import FailingAsyncClient as _FailingClient


def _make_request(query_string: str = "") -> Request:
    return Request({"type": "http", "query_string": query_string.encode()})


async def test_search_patients_proxies_query_params(monkeypatch, respx_mock):
    patient_route = respx_mock.get(
        "http://external.example/fhir/Patient",
        params=[("name", "Smith"), ("birthdate", "1990-01-01")],
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [
                    {"resource": {"resourceType": "Patient", "id": "patient-123"}},
                ],
            },
        )
    )

    request = _make_request("name=Smith&birthdate=1990-01-01")
    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))

    result = await patient.search_patients(request, claims={})

    assert isinstance(result, BundleResource)
    request = patient_route.calls[0].request
    assert str(request.url.copy_with(query=None)) == "http://external.example/fhir/Patient"
    assert list(request.url.params.multi_items()) == [("name", "Smith"), ("birthdate", "1990-01-01")]
    assert {key: request.headers[key] for key in ("accept", "authorization")} == {"accept": "application/fhir+json", "authorization": "Bearer token"}


async def test_get_patient_returns_patient_resource(monkeypatch, respx_mock):
    patient_route = respx_mock.get("http://external.example/fhir/Patient/patient-123").mock(return_value=httpx.Response(200, json={"resourceType": "Patient", "id": "patient-123"}))

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")

    result = await patient.get_patient("patient-123", claims={})

    assert isinstance(result, PatientResource)
    assert patient_route.called
    assert result.id == "patient-123"


async def test_get_patient_returns_operation_outcome(monkeypatch, respx_mock):
    patient_route = respx_mock.get("http://external.example/fhir/Patient/missing").mock(
        return_value=httpx.Response(
            404,
            json={
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing patient"}],
            },
        )
    )

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")

    result = await patient.get_patient("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert patient_route.called
    assert result.status_code == 404
    assert bytes(result.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing patient"}]}'


async def test_search_patients_returns_config_error(monkeypatch):
    request = _make_request()
    monkeypatch.setattr(settings, "config_errors", {"EXTERNAL_FHIR_SERVER_URL": "missing"})

    result = await patient.search_patients(request, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503


async def test_search_patients_handles_request_errors(monkeypatch):
    request = _make_request("name=Smith")
    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(patient.httpx, "AsyncClient", _FailingClient)

    result = await patient.search_patients(request, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 502
    assert (
        bytes(result.body).decode("utf-8")
        == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"transient","diagnostics":"External FHIR server request failed for GET Patient"}]}'
    )
