from typing import ClassVar, cast

import httpx

from src.services import fhir_proxy


class _FakeAsyncClient:
    queued_response: httpx.Response | None = None
    calls: ClassVar[list[dict]] = []

    def __init__(self, timeout=None, transport=None):
        self.timeout = timeout
        self.transport = transport

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
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")

    assert fhir_proxy._base_url("Library") == "http://hapi.example/fhir/Library"
    assert fhir_proxy._base_url("Library", "lib-1") == "http://hapi.example/fhir/Library/lib-1"


def test_handle_passes_through_successful_dict_payload():
    response = httpx.Response(200, json={"resourceType": "Library", "id": "lib-1"}, request=httpx.Request("GET", "http://example.test"))

    result = fhir_proxy._handle(response, "GET", "http://example.test")

    assert result == {"resourceType": "Library", "id": "lib-1"}


def test_handle_passes_through_operation_outcome_errors():
    response = httpx.Response(
        404,
        json={"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing"}]},
        request=httpx.Request("GET", "http://example.test"),
    )

    result = cast(dict, fhir_proxy._handle(response, "GET", "http://example.test"))

    assert result["resourceType"] == "OperationOutcome"
    assert result["issue"][0]["diagnostics"] == "missing"


def test_handle_wraps_non_fhir_errors_in_operation_outcome():
    response = httpx.Response(500, json={"error": "boom"}, request=httpx.Request("GET", "http://example.test"))

    result = cast(dict, fhir_proxy._handle(response, "GET", "http://example.test"))

    assert result["resourceType"] == "OperationOutcome"
    assert "FHIR server returned HTTP 500" in result["issue"][0]["diagnostics"]


async def test_fhir_get_uses_expected_headers_and_params(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = httpx.Response(200, json={"resourceType": "Bundle", "total": 1}, request=httpx.Request("GET", "http://example.test"))
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
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


async def test_fhir_post_sends_json_body(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = httpx.Response(201, json={"resourceType": "Library", "id": "lib-1"}, request=httpx.Request("POST", "http://example.test"))
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_proxy.fhir_post("Library", {"resourceType": "Library", "name": "RiskLib"})

    assert result == {"resourceType": "Library", "id": "lib-1"}
    assert _FakeAsyncClient.calls[0]["json"] == {"resourceType": "Library", "name": "RiskLib"}


async def test_fhir_put_sends_json_body(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = httpx.Response(200, json={"resourceType": "Questionnaire", "id": "q-1"}, request=httpx.Request("PUT", "http://example.test"))
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
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
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_proxy.fhir_delete("Library", "lib-1")

    assert result == {}
    assert _FakeAsyncClient.calls[0] == {
        "method": "DELETE",
        "url": "http://hapi.example/fhir/Library/lib-1",
        "headers": {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
    }
