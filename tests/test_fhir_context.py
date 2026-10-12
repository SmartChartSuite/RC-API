import base64

from src.services import fhir_context
from tests.fakes import FailingAsyncClient as _FailingClient
from tests.fakes import FakeAsyncClient as _FakeAsyncClient
from tests.fakes import FakeResponse as _FakeResponse


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
        {
            "id": "doc-1",
            "type": "Visit Note",
            "date": "2026-05-22T12:00:00Z",
            "text": "inline note",
            "resource": {
                "resourceType": "DocumentReference",
                "id": "doc-1",
                "date": "2026-05-22T12:00:00Z",
                "type": {"coding": [{"display": "Visit Note"}]},
                "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"inline note").decode("utf-8")}}],
            },
        },
        {
            "id": "doc-2",
            "type": "Lab Note",
            "date": "2026-05-23T12:00:00Z",
            "text": "external note",
            "resource": {
                "resourceType": "DocumentReference",
                "id": "doc-2",
                "date": "2026-05-23T12:00:00Z",
                "type": {"text": "Lab Note"},
                "content": [{"attachment": {"contentType": "text/plain", "url": "http://docs.example/doc-2.txt"}}],
            },
        },
    ]
    assert _FakeAsyncClient.calls[0] == {
        "url": "http://external.example/fhir/DocumentReference",
        "headers": {"Accept": "application/fhir+json", "Authorization": "Bearer token"},
        "params": {"subject": "Patient/patient-123", "_count": "500"},
    }
    assert _FakeAsyncClient.calls[1] == {
        "url": "http://docs.example/doc-2.txt",
        "headers": {"Accept": "application/fhir+json"},
        "params": None,
    }


async def test_fetch_patient_documents_returns_empty_list_on_search_error(monkeypatch):
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FailingClient)

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


async def test_fetch_patient_documents_skips_unsafe_attachment_url(monkeypatch):
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
                                "id": "doc-unsafe",
                                "content": [{"attachment": {"contentType": "text/plain", "url": "file:///etc/passwd"}}],
                            }
                        }
                    ],
                }
            )
        ],
        [],
    ]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "external_fhir_server_auth", "Bearer token")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result == []
    assert len(_FakeAsyncClient.calls) == 1
