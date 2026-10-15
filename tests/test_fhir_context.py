"""Tests patient-document fetching, attachment decoding, SSRF filtering, exclusion reporting, and limits.

The tests use respx routes for FHIR and attachment HTTP traffic, patch DNS resolution and settings, and
leave LLM execution and batch orchestration to other modules."""

import base64

import httpx
import pytest
from pydantic import SecretStr

from src.services import fhir_context
from src.util.settings import settings
from tests.fakes import FailingAsyncClient as _FailingClient


async def _public_dns(url: str) -> bool:
    return False


async def test_fetch_patient_documents_returns_inline_and_external_text(monkeypatch, respx_mock):
    search_route = respx_mock.get(
        "http://external.example/fhir/DocumentReference",
        params={"subject": "Patient/patient-123", "_count": "100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
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
            },
        )
    )
    attachment_route = respx_mock.get("http://docs.example/doc-2.txt").mock(return_value=httpx.Response(200, text="external note"))
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))
    monkeypatch.setattr(settings, "attachment_allowed_hosts", ["docs.example"])
    monkeypatch.setattr(fhir_context, "resolves_to_internal_address", _public_dns)

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
    search_request = search_route.calls[0].request
    assert str(search_request.url.copy_with(query=None)) == "http://external.example/fhir/DocumentReference"
    assert dict(search_request.url.params) == {"subject": "Patient/patient-123", "_count": "100"}
    assert {key: search_request.headers[key] for key in ("accept", "authorization")} == {"accept": "application/fhir+json", "authorization": "Bearer token"}
    attachment_request = attachment_route.calls[0].request
    assert str(attachment_request.url) == "http://docs.example/doc-2.txt"
    assert dict(attachment_request.url.params) == {}
    assert {"accept": attachment_request.headers["accept"]} == {"accept": "application/fhir+json"}


async def test_fetch_patient_documents_raises_on_search_error(monkeypatch):
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_context.httpx, "AsyncClient", _FailingClient)

    with pytest.raises(fhir_context.DocumentFetchError):
        await fhir_context.fetch_patient_documents("patient-123")


async def test_fetch_patient_documents_ignores_non_text_attachments(monkeypatch, respx_mock):
    search_route = respx_mock.get(
        "http://external.example/fhir/DocumentReference",
        params={"subject": "Patient/patient-123", "_count": "100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
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
            },
        )
    )
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert search_route.called
    assert result.documents == []
    assert result.excluded == []


async def test_fetch_patient_documents_blocks_internal_attachment_hosts(monkeypatch, respx_mock):
    search_route = respx_mock.get(
        "http://external.example/fhir/DocumentReference",
        params={"subject": "Patient/patient-123", "_count": "100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [
                    {"resource": {"resourceType": "DocumentReference", "id": "doc-1", "content": [{"attachment": {"contentType": "text/plain", "url": "http://169.254.169.254/latest/meta-data/"}}]}}
                ],
            },
        )
    )
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "attachment_allowed_hosts", [])

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert search_route.called
    assert result.documents == []
    assert result.excluded == [{"doc_id": "doc-1", "reason": "host_not_allowed"}]
    assert len(respx_mock.calls) == 1  # only the DocumentReference search


async def test_fetch_patient_documents_allows_same_origin_relative_attachment(monkeypatch, respx_mock):
    search_route = respx_mock.get(
        "http://external.example/fhir/DocumentReference",
        params={"subject": "Patient/patient-123", "_count": "100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [{"resource": {"resourceType": "DocumentReference", "id": "doc-1", "content": [{"attachment": {"contentType": "text/plain", "url": "Binary/abc"}}]}}],
            },
        )
    )
    attachment_route = respx_mock.get("http://external.example/fhir/Binary/abc").mock(return_value=httpx.Response(200, text="binary note"))
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))
    monkeypatch.setattr(settings, "attachment_allowed_hosts", [])

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert [doc["text"] for doc in result.documents] == ["binary note"]
    assert search_route.called
    attachment_request = attachment_route.calls[0].request
    assert str(attachment_request.url) == "http://external.example/fhir/Binary/abc"
    assert attachment_request.headers["authorization"] == "Bearer token"


async def test_fetch_patient_documents_reports_exclusions_and_returns_normal_documents(monkeypatch, respx_mock):
    search_route = respx_mock.get(
        "http://external.example/fhir/DocumentReference",
        params={"subject": "Patient/patient-123", "_count": "100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
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
            },
        )
    )
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "doc_max_bytes", 10)
    monkeypatch.setattr(settings, "attachment_allowed_hosts", [])

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert search_route.called
    assert [doc["id"] for doc in result.documents] == ["normal"]
    assert result.documents[0]["text"] == "note"
    assert result.excluded == [
        {"doc_id": "oversized", "reason": "exceeds_max_bytes"},
        {"doc_id": "blocked", "reason": "host_not_allowed"},
    ]
    assert "blocked.example" not in str(result.excluded)
    assert "https://" not in str(result.excluded)


async def test_fetch_patient_documents_caps_document_count(monkeypatch, respx_mock):
    entries = [
        {"resource": {"resourceType": "DocumentReference", "id": f"doc-{i}", "content": [{"attachment": {"contentType": "text/plain", "data": base64.b64encode(b"note").decode()}}]}} for i in range(5)
    ]
    search_route = respx_mock.get(
        "http://external.example/fhir/DocumentReference",
        params={"subject": "Patient/patient-123", "_count": "2"},
    ).mock(return_value=httpx.Response(200, json={"resourceType": "Bundle", "entry": entries}))
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "doc_max_count", 2)

    result = await fhir_context.fetch_patient_documents("patient-123")

    assert search_route.called
    assert [doc["id"] for doc in result.documents] == ["doc-0", "doc-1"]
    assert result.excluded == [{"reason": "exceeds_max_count", "count": 3}]
