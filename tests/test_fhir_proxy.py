"""Tests HAPI FHIR proxy URL construction, CRUD requests, payload handling, validation, and safe errors.

The module uses respx and settings to verify headers, query/body forwarding, invalid IDs, and sanitized
transport failures; router adapters are covered by dedicated test modules."""

import json
from typing import cast

import httpx

from src.services import fhir_proxy
from src.util import http_clients
from src.util.settings import settings


def _response(status_code: int, payload, text: str | None = None) -> httpx.Response:
    request = httpx.Request("GET", "http://example.test")
    if isinstance(payload, dict):
        return httpx.Response(status_code, json=payload, request=request)
    return httpx.Response(status_code, content=payload, text=text, request=request)


def test_base_url_builds_resource_path(monkeypatch):
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")

    assert fhir_proxy._base_url("Library") == "http://hapi.example/fhir/Library"
    assert fhir_proxy._base_url("Library", "lib-1") == "http://hapi.example/fhir/Library/lib-1"


def test_handle_passes_through_successful_dict_payload():
    response = httpx.Response(200, json={"resourceType": "Library", "id": "lib-1"}, request=httpx.Request("GET", "http://example.test"))

    result = fhir_proxy._handle(response, "GET", "http://example.test")

    assert result.status_code == 200
    assert result.data == {"resourceType": "Library", "id": "lib-1"}


def test_handle_passes_through_operation_outcome_errors():
    response = httpx.Response(
        404,
        json={"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing"}]},
        request=httpx.Request("GET", "http://example.test"),
    )

    result = fhir_proxy._handle(response, "GET", "http://example.test")

    data = cast(dict, result.data)
    assert result.status_code == 404
    assert data["resourceType"] == "OperationOutcome"
    assert data["issue"][0]["diagnostics"] == "missing"


def test_handle_wraps_non_fhir_errors_without_leaking_upstream_details():
    response = _response(500, b"internal stack trace with secrets", text=None)

    result = fhir_proxy._handle(response, "GET", "Library")

    data = cast(dict, result.data)
    assert result.status_code == 500
    assert data["resourceType"] == "OperationOutcome"
    diagnostics = data["issue"][0]["diagnostics"]
    assert "FHIR server returned HTTP 500" in diagnostics
    assert "http://" not in diagnostics
    assert "secrets" not in diagnostics


async def test_fhir_get_uses_expected_headers_and_params(monkeypatch, respx_mock):
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    route = respx_mock.get("http://hapi.example/fhir/Questionnaire").mock(return_value=httpx.Response(200, json={"resourceType": "Bundle", "total": 1}))

    result = await fhir_proxy.fhir_get("Questionnaire", params={"name": "Registry"})

    assert result == {"resourceType": "Bundle", "total": 1}
    assert route.called
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == "http://hapi.example/fhir/Questionnaire?name=Registry"
    assert dict(request.url.params) == {"name": "Registry"}
    assert request.headers["Content-Type"] == "application/fhir+json"
    assert request.headers["Accept"] == "application/fhir+json"


async def test_fhir_put_sends_json_body_to_resource_url(monkeypatch, respx_mock):
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    route = respx_mock.put("http://hapi.example/fhir/Questionnaire/q-1").mock(return_value=httpx.Response(200, json={"resourceType": "Questionnaire", "id": "q-1"}))

    result = await fhir_proxy.fhir_put("Questionnaire", "q-1", {"resourceType": "Questionnaire", "id": "q-1"})

    assert result == {"resourceType": "Questionnaire", "id": "q-1"}
    assert route.called
    request = route.calls.last.request
    assert request.method == "PUT"
    assert str(request.url) == "http://hapi.example/fhir/Questionnaire/q-1"
    assert request.headers["Content-Type"] == "application/fhir+json"
    assert request.headers["Accept"] == "application/fhir+json"
    assert json.loads(request.content) == {"resourceType": "Questionnaire", "id": "q-1"}


async def test_fhir_delete_sends_no_body(monkeypatch, respx_mock):
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    route = respx_mock.delete("http://hapi.example/fhir/Library/lib-1").mock(return_value=httpx.Response(204))

    result = cast(dict, await fhir_proxy.fhir_delete("Library", "lib-1"))

    # A 204 is surfaced as an informational OperationOutcome so delete routers can validate it.
    assert result["resourceType"] == "OperationOutcome"
    assert result["issue"][0]["severity"] == "information"
    assert route.called
    request = route.calls.last.request
    assert request.method == "DELETE"
    assert str(request.url) == "http://hapi.example/fhir/Library/lib-1"
    assert request.headers["Content-Type"] == "application/fhir+json"
    assert request.headers["Accept"] == "application/fhir+json"
    assert request.content == b""


async def test_request_rejects_invalid_resource_id_without_calling_upstream(monkeypatch, respx_mock):
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")

    result = await fhir_proxy.fhir_get_with_status("Library", "../Patient/123")

    data = cast(dict, result.data)
    assert result.status_code == 400
    assert data["resourceType"] == "OperationOutcome"
    assert data["issue"][0]["code"] == "invalid"
    assert len(respx_mock.calls) == 0


async def test_request_converts_transport_errors_to_operation_outcome(monkeypatch, respx_mock):
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(http_clients, "_sleep", _sleep)
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    route = respx_mock.get("http://hapi.example/fhir/Library/lib-1").mock(side_effect=httpx.ConnectError("connection refused to 10.1.2.3"))

    result = await fhir_proxy.fhir_get_with_status("Library", "lib-1")

    data = cast(dict, result.data)
    assert result.status_code == 502
    assert result.error_type == "transport"
    assert data["resourceType"] == "OperationOutcome"
    assert data["issue"][0]["code"] == "transient"
    assert "10.1.2.3" not in data["issue"][0]["diagnostics"]
    assert len(route.calls) == 3
    assert len(delays) == 2
