from typing import ClassVar

import httpx

from src.services import external_fhir
from src.util.settings import settings


class _FakeAsyncClient:
    queued_response: ClassVar[httpx.Response | Exception | None] = None
    calls: ClassVar[list[dict]] = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        _FakeAsyncClient.calls.append({"url": url, "headers": headers})
        queued_response = _FakeAsyncClient.queued_response
        if isinstance(queued_response, Exception):
            raise queued_response
        assert queued_response is not None
        return queued_response


def _set_client(monkeypatch, response):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_response = response
    monkeypatch.setattr(external_fhir.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(settings, "external_fhir_server_url", "https://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", "Bearer test-token")


async def test_fetch_patient_returns_resource_with_auth_headers(monkeypatch):
    _set_client(
        monkeypatch,
        httpx.Response(200, json={"resourceType": "Patient", "id": "patient-1"}, request=httpx.Request("GET", "https://example.test")),
    )

    result = await external_fhir.fetch_patient("patient-1")

    assert result == {"resourceType": "Patient", "id": "patient-1"}
    assert _FakeAsyncClient.calls == [
        {
            "url": "https://external.example/fhir/Patient/patient-1",
            "headers": {"Accept": "application/fhir+json", "Authorization": "Bearer test-token"},
        }
    ]


async def test_fetch_patient_returns_none_for_non_success_response(monkeypatch):
    _set_client(monkeypatch, httpx.Response(404, json={"resourceType": "OperationOutcome"}, request=httpx.Request("GET", "https://example.test")))

    assert await external_fhir.fetch_patient("missing") is None


async def test_fetch_patient_returns_none_for_invalid_json(monkeypatch):
    _set_client(monkeypatch, httpx.Response(200, content=b"not-json", request=httpx.Request("GET", "https://example.test")))

    assert await external_fhir.fetch_patient("patient-1") is None


async def test_fetch_patient_returns_none_for_request_error(monkeypatch):
    _set_client(monkeypatch, httpx.RequestError("connection failed"))

    assert await external_fhir.fetch_patient("patient-1") is None
