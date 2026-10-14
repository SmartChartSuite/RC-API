"""Tests HAPI FHIR proxy URL construction, CRUD requests, payload handling, validation, and safe errors.

The module patches `httpx.AsyncClient` and settings to verify headers, query/body forwarding, invalid IDs,
and sanitized transport failures; router adapters are covered by dedicated test modules."""

from typing import ClassVar, cast

import httpx

from src.services import fhir_proxy
from src.util.settings import settings


class _FakeAsyncClient:
    queued_response: httpx.Response | None = None
    calls: ClassVar[list[dict]] = []

    def __init__(self, timeout=None, transport=None, **kwargs):
        self.timeout = timeout
        self.transport = transport
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, params=None, json=None):
        call = {"method": method, "url": url, "headers": headers}
        if params is not None:
            call["params"] = params
        if json is not None:
            call["json"] = json
        _FakeAsyncClient.calls.append(call)
        assert _FakeAsyncClient.queued_response is not None
        return _FakeAsyncClient.queued_response


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


async def test_fhir_get_uses_expected_headers_and_params(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = httpx.Response(200, json={"resourceType": "Bundle", "total": 1}, request=httpx.Request("GET", "http://example.test"))
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_proxy.fhir_get("Questionnaire", params={"name": "Registry"})

    assert result == {"resourceType": "Bundle", "total": 1}
    assert _FakeAsyncClient.calls == [
        {
            "method": "GET",
            "url": "http://hapi.example/fhir/Questionnaire",
            "headers": {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
            "params": {"name": "Registry"},
        }
    ]


async def test_fhir_put_sends_json_body_to_resource_url(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = httpx.Response(200, json={"resourceType": "Questionnaire", "id": "q-1"}, request=httpx.Request("PUT", "http://example.test"))
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_proxy.fhir_put("Questionnaire", "q-1", {"resourceType": "Questionnaire", "id": "q-1"})

    assert result == {"resourceType": "Questionnaire", "id": "q-1"}
    assert _FakeAsyncClient.calls[0] == {
        "method": "PUT",
        "url": "http://hapi.example/fhir/Questionnaire/q-1",
        "headers": {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
        "json": {"resourceType": "Questionnaire", "id": "q-1"},
    }


async def test_fhir_delete_sends_no_body(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = httpx.Response(204, request=httpx.Request("DELETE", "http://example.test"))
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = cast(dict, await fhir_proxy.fhir_delete("Library", "lib-1"))

    # A 204 is surfaced as an informational OperationOutcome so delete routers can validate it.
    assert result["resourceType"] == "OperationOutcome"
    assert result["issue"][0]["severity"] == "information"
    assert _FakeAsyncClient.calls[0] == {
        "method": "DELETE",
        "url": "http://hapi.example/fhir/Library/lib-1",
        "headers": {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
    }


async def test_request_rejects_invalid_resource_id_without_calling_upstream(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_proxy.fhir_get_with_status("Library", "../Patient/123")

    data = cast(dict, result.data)
    assert result.status_code == 400
    assert data["resourceType"] == "OperationOutcome"
    assert data["issue"][0]["code"] == "invalid"
    assert _FakeAsyncClient.calls == []


async def test_request_converts_transport_errors_to_operation_outcome(monkeypatch):
    class _Unreachable(_FakeAsyncClient):
        async def request(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused to 10.1.2.3")

    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _Unreachable)

    result = await fhir_proxy.fhir_get_with_status("Library", "lib-1")

    data = cast(dict, result.data)
    assert result.status_code == 502
    assert result.error_type == "transport"
    assert data["resourceType"] == "OperationOutcome"
    assert data["issue"][0]["code"] == "transient"
    assert "10.1.2.3" not in data["issue"][0]["diagnostics"]
