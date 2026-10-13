import base64

import pytest
from pydantic import SecretStr

from src.services import fhir_context
from src.util.settings import settings
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
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))
    monkeypatch.setattr(settings, "attachment_allowed_hosts", ["docs.example"])
    monkeypatch.setattr(fhir_context, "resolves_to_internal_address", _public_dns)
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result.documents == [
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
    assert result.excluded == []
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
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
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
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result.documents == []
    assert result.excluded == []


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
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "attachment_allowed_hosts", [])
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert result.documents == []
    assert result.excluded == [{"doc_id": "doc-1", "reason": "host_not_allowed"}]
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
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))
    monkeypatch.setattr(settings, "attachment_allowed_hosts", [])
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert [doc["text"] for doc in result.documents] == ["binary note"]
    assert _FakeAsyncClient.calls[1]["url"] == "http://external.example/fhir/Binary/abc"
    assert _FakeAsyncClient.calls[1]["headers"]["Authorization"] == "Bearer token"


async def test_fetch_patient_documents_reports_exclusions_and_returns_normal_documents(monkeypatch):
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
                                "id": "oversized",
                                "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"x" * 100).decode()}}],
                            }
                        },
                        {"resource": {"resourceType": "DocumentReference", "id": "blocked", "content": [{"attachment": {"contentType": "text/plain", "url": "https://blocked.example/note.txt"}}]}},
                        {"resource": {"resourceType": "DocumentReference", "id": "normal", "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"note").decode()}}]}},
                    ],
                }
            )
        ],
    ]
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "doc_max_bytes", 10)
    monkeypatch.setattr(settings, "attachment_allowed_hosts", [])
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert [doc["id"] for doc in result.documents] == ["normal"]
    assert result.documents[0]["text"] == "note"
    assert result.excluded == [
        {"doc_id": "oversized", "reason": "exceeds_max_bytes"},
        {"doc_id": "blocked", "reason": "host_not_allowed"},
    ]
    assert "blocked.example" not in str(result.excluded)
    assert "https://" not in str(result.excluded)


async def test_fetch_patient_documents_caps_document_count(monkeypatch):
    entries = [
        {"resource": {"resourceType": "DocumentReference", "id": f"doc-{i}", "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"note").decode()}}]}} for i in range(5)
    ]
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [[_FakeResponse({"resourceType": "Bundle", "entry": entries})], []]
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "doc_max_count", 2)
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FakeAsyncClient)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert [doc["id"] for doc in result.documents] == ["doc-0", "doc-1"]
    assert result.excluded == [{"reason": "exceeds_max_count", "count": 3}]
