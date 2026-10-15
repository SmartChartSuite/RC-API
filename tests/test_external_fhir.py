"""Tests external-FHIR Patient fetching and non-success, malformed-response, and transport-error handling.

Respx routes intercept the pooled HTTP client; settings provide the base URL and auth, while router behavior
belongs in `test_patient.py`."""

import httpx
from pydantic import SecretStr

from src.services import external_fhir
from src.util.settings import settings


def _set_client(monkeypatch, respx_mock, patient_id: str, response: httpx.Response | Exception):
    monkeypatch.setattr(settings, "external_fhir_server_url", "https://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer test-token"))
    route = respx_mock.get(f"https://external.example/fhir/Patient/{patient_id}")
    if isinstance(response, Exception):
        return route.mock(side_effect=response)
    return route.mock(return_value=response)


async def test_fetch_patient_returns_resource_with_auth_headers(monkeypatch, respx_mock):
    route = _set_client(
        monkeypatch,
        respx_mock,
        "patient-1",
        httpx.Response(200, json={"resourceType": "Patient", "id": "patient-1"}),
    )

    result = await external_fhir.fetch_patient("patient-1")

    assert result == {"resourceType": "Patient", "id": "patient-1"}
    assert route.called
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == "https://external.example/fhir/Patient/patient-1"
    assert request.headers["Accept"] == "application/fhir+json"
    assert request.headers["Authorization"] == "Bearer test-token"


async def test_fetch_patient_returns_none_for_non_success_response(monkeypatch, respx_mock):
    route = _set_client(
        monkeypatch,
        respx_mock,
        "missing",
        httpx.Response(404, json={"resourceType": "OperationOutcome"}),
    )

    assert await external_fhir.fetch_patient("missing") is None
    assert route.called


async def test_fetch_patient_returns_none_for_invalid_json(monkeypatch, respx_mock):
    route = _set_client(monkeypatch, respx_mock, "patient-1", httpx.Response(200, content=b"not-json"))

    assert await external_fhir.fetch_patient("patient-1") is None
    assert route.called


async def test_fetch_patient_returns_none_for_request_error(monkeypatch, respx_mock):
    route = _set_client(monkeypatch, respx_mock, "patient-1", httpx.RequestError("connection failed"))

    assert await external_fhir.fetch_patient("patient-1") is None
    assert len(route.calls) == 1
