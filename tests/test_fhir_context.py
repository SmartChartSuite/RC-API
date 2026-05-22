import base64

import httpx

from src.services import fhir_context


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class _FakeAsyncClient:
    queued_responses: list[list[_FakeResponse]] = []
    calls: list[dict] = []

    def __init__(self, timeout=None, transport=None):
        self._responses = _FakeAsyncClient.queued_responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        _FakeAsyncClient.calls.append({"url": url, "headers": headers, "params": params})
        return self._responses.pop(0)


async def test_fetch_patient_documents_returns_inline_and_external_text(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "DocumentReference",
                                "id": "doc-1",
                                "date": "2026-05-22T12:00:00Z",
                                "type": {"coding": [{"display": "Visit Note"}]},
                                "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"inline note").decode("utf-8")}}],
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "DocumentReference",
                                "id": "doc-2",
                                "date": "2026-05-23T12:00:00Z",
                                "type": {"text": "Lab Note"},
                                "content": [{"attachment": {"contentType": "text/plain", "url": "http://docs.example/doc-2.txt"}}],
                            }
                        },
                    ],
                }
            )
        ],
        [_FakeResponse(text="external note")],
    ]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "external_fhir_server_auth", "Bearer token")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result == [
        {"id": "doc-1", "type": "Visit Note", "date": "2026-05-22T12:00:00Z", "text": "inline note"},
        {"id": "doc-2", "type": "Lab Note", "date": "2026-05-23T12:00:00Z", "text": "external note"},
    ]
    assert _FakeAsyncClient.calls[0] == {
        "url": "http://external.example/fhir/DocumentReference",
        "headers": {"Accept": "application/fhir+json", "Authorization": "Bearer token"},
        "params": {"subject": "Patient/patient-123", "_count": "500"},
    }


async def test_fetch_patient_documents_returns_empty_list_on_search_error(monkeypatch):
    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, params=None):
            raise httpx.RequestError("boom")

    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", lambda timeout=None, transport=None: _FailingClient())

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result == []


async def test_fetch_patient_documents_ignores_non_text_attachments(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "DocumentReference",
                                "id": "doc-1",
                                "content": [{"attachment": {"contentType": "application/pdf", "url": "http://docs.example/doc-1.pdf"}}],
                            }
                        }
                    ],
                }
            )
        ],
        [],
    ]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result == []
