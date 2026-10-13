import base64

import pytest

from src.services import fhir_context
from tests.fakes import FailingAsyncClient as _FailingClient
from tests.fakes import FakeAsyncClient as _FakeAsyncClient
from tests.fakes import FakeResponse as _FakeResponse


async def _public_dns(url: str) -> bool:
    return False


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
            ),
            _FakeResponse(text="external note"),
        ],
    ]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "external_fhir_server_auth", "Bearer token")
    monkeypatch.setattr(fhir_context, "attachment_allowed_hosts", ["docs.example"])
    monkeypatch.setattr(fhir_context, "resolves_to_internal_address", _public_dns)
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
        "params": {"subject": "Patient/patient-123", "_count": "100"},
    }
    assert _FakeAsyncClient.calls[1] == {
        "url": "http://docs.example/doc-2.txt",
        "headers": {"Accept": "application/fhir+json"},
        "params": None,
    }


async def test_fetch_patient_documents_raises_on_search_error(monkeypatch):
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FailingClient)

    with pytest.raises(fhir_context.DocumentFetchError):
        await fhir_context.fetch_patient_documents("patient-123")


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


async def test_fetch_patient_documents_blocks_internal_attachment_hosts(monkeypatch):
    bundle = _FakeResponse(
        {
            "resourceType": "Bundle",
            "entry": [
                {"resource": {"resourceType": "DocumentReference", "id": "doc-1", "content": [{"attachment": {"contentType": "text/plain", "url": "http://169.254.169.254/latest/meta-data/"}}]}}
            ],
        }
    )
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [[bundle], []]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "attachment_allowed_hosts", [])
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result == []
    assert len(_FakeAsyncClient.calls) == 1  # only the DocumentReference search


async def test_fetch_patient_documents_allows_same_origin_relative_attachment(monkeypatch):
    bundle = _FakeResponse(
        {
            "resourceType": "Bundle",
            "entry": [{"resource": {"resourceType": "DocumentReference", "id": "doc-1", "content": [{"attachment": {"contentType": "text/plain", "url": "Binary/abc"}}]}}],
        }
    )
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [[bundle, _FakeResponse(text="binary note")]]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "external_fhir_server_auth", "Bearer token")
    monkeypatch.setattr(fhir_context, "attachment_allowed_hosts", [])
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert [doc["text"] for doc in result] == ["binary note"]
    assert _FakeAsyncClient.calls[1]["url"] == "http://external.example/fhir/Binary/abc"
    assert _FakeAsyncClient.calls[1]["headers"]["Authorization"] == "Bearer token"


async def test_fetch_patient_documents_skips_oversized_attachments(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {"resource": {"resourceType": "DocumentReference", "id": "inline", "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"x" * 100).decode()}}]}},
                        {"resource": {"resourceType": "DocumentReference", "id": "remote", "content": [{"attachment": {"contentType": "text/plain", "url": "Binary/big"}}]}},
                    ],
                }
            ),
            _FakeResponse(text="y" * 100),
        ],
    ]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "doc_max_bytes", 10)
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result == []


async def test_fetch_patient_documents_caps_document_count(monkeypatch):
    entries = [
        {"resource": {"resourceType": "DocumentReference", "id": f"doc-{i}", "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"note").decode()}}]}} for i in range(5)
    ]
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [[_FakeResponse({"resourceType": "Bundle", "entry": entries})], []]
    monkeypatch.setattr(fhir_context, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context, "doc_max_count", 2)
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert [doc["id"] for doc in result] == ["doc-0", "doc-1"]
