"""Tests external-FHIR Patient fetching and non-success, malformed-response, and transport-error handling.

Respx routes intercept the pooled HTTP client; settings provide the base URL and auth, while router behavior
belongs in `test_patient.py`."""

import asyncio

import httpx
import pytest
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


async def test_fetch_patient_summary_caches_only_projected_fields(monkeypatch, respx_mock):
    external_fhir.clear_patient_summary_cache()
    monkeypatch.setattr(settings, "patient_summary_cache_ttl_seconds", 900.0)
    monkeypatch.setattr(settings, "patient_summary_cache_max_entries", 1000)
    route = _set_client(
        monkeypatch,
        respx_mock,
        "patient-1",
        httpx.Response(
            200,
            json={
                "resourceType": "Patient",
                "id": "patient-1",
                "name": [{"family": "Doe", "given": ["Jane"]}],
                "birthDate": "2020-01-01",
                "gender": "female",
                "identifier": [{"value": "sensitive"}],
            },
        ),
    )

    first = await external_fhir.fetch_patient_summary("patient-1")
    second = await external_fhir.fetch_patient_summary("patient-1")

    assert first == {
        "id": "patient-1",
        "name": [{"family": "Doe", "given": ["Jane"]}],
        "birthDate": "2020-01-01",
        "gender": "female",
    }
    assert second == first
    assert route.call_count == 1
    external_fhir.clear_patient_summary_cache()


async def test_fetch_patient_summary_coalesces_concurrent_misses(monkeypatch):
    external_fhir.clear_patient_summary_cache()
    monkeypatch.setattr(settings, "patient_summary_cache_ttl_seconds", 900.0)
    calls = 0

    async def fake_fetch_patient(patient_id: str):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"resourceType": "Patient", "id": patient_id, "gender": "female"}

    monkeypatch.setattr(external_fhir, "fetch_patient", fake_fetch_patient)

    results = await asyncio.gather(
        external_fhir.fetch_patient_summary("patient-1"),
        external_fhir.fetch_patient_summary("patient-1"),
    )

    assert results == [{"id": "patient-1", "gender": "female"}] * 2
    assert calls == 1
    external_fhir.clear_patient_summary_cache()


async def test_cancelled_summary_waiter_does_not_cancel_shared_fetch(monkeypatch):
    external_fhir.clear_patient_summary_cache()
    monkeypatch.setattr(settings, "patient_summary_cache_ttl_seconds", 900.0)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_fetch_patient(patient_id: str):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"resourceType": "Patient", "id": patient_id, "gender": "female"}

    monkeypatch.setattr(external_fhir, "fetch_patient", fake_fetch_patient)
    first = asyncio.create_task(external_fhir.fetch_patient_summary("patient-1"))
    await started.wait()
    second = asyncio.create_task(external_fhir.fetch_patient_summary("patient-1"))
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    release.set()
    assert await second == {"id": "patient-1", "gender": "female"}
    assert calls == 1
    external_fhir.clear_patient_summary_cache()


async def test_unshielded_shared_fetch_reproduces_waiter_cancellation_bug():
    started = asyncio.Event()
    release = asyncio.Event()

    async def shared_fetch():
        started.set()
        await release.wait()
        return "patient-summary"

    in_flight = asyncio.create_task(shared_fetch())

    async def unshielded_waiter():
        return await in_flight

    first = asyncio.create_task(unshielded_waiter())
    second = asyncio.create_task(unshielded_waiter())
    await started.wait()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(asyncio.CancelledError):
        await second

    assert in_flight.cancelled()
