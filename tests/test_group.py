"""Tests Group router search, member expansion, CRUD responses, concurrency, and upstream-error handling.

The tests patch `src.services.fhir_proxy`, `src.routers.group`, and settings; ordinary HTTP traffic uses respx,
write calls use the canonical GroupResource model, and HAPI transport details are covered in `test_fhir_proxy.py`."""

import asyncio

import httpx
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from src.models.fhir import GroupResource, PatientResource
from src.routers import group
from src.services import fhir_proxy
from src.services.fhir_proxy import FHIRProxyResponse
from src.util.settings import settings
from tests.fakes import FailingAsyncClient as _FailingClient


async def test_search_groups_returns_group_and_patients(monkeypatch, respx_mock):
    group_route = respx_mock.get("http://hapi.example/fhir/Group", params={"name": "Panel"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Group",
                            "id": "group-1",
                            "name": "Panel",
                            "member": [{"entity": {"reference": "Patient/patient-123"}}],
                        }
                    }
                ],
            },
        )
    )
    patient_route = respx_mock.get("http://external.example/fhir/Patient/patient-123").mock(return_value=httpx.Response(200, json={"resourceType": "Patient", "id": "patient-123"}))

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")

    result = await group.search_groups(name="Panel", claims={})

    assert not isinstance(result, JSONResponse)
    assert isinstance(result[0], GroupResource)
    assert isinstance(result[1], PatientResource)
    group_request = group_route.calls[0].request
    assert str(group_request.url.copy_with(query=None)) == "http://hapi.example/fhir/Group"
    assert dict(group_request.url.params) == {"name": "Panel"}
    assert {key: group_request.headers[key] for key in ("content-type", "accept")} == {
        "content-type": "application/fhir+json",
        "accept": "application/fhir+json",
    }
    patient_request = patient_route.calls[0].request
    assert str(patient_request.url) == "http://external.example/fhir/Patient/patient-123"
    assert dict(patient_request.url.params) == {}
    assert {"accept": patient_request.headers["accept"]} == {"accept": "application/fhir+json"}


async def test_get_group_returns_operation_outcome(monkeypatch, respx_mock):
    group_route = respx_mock.get("http://hapi.example/fhir/Group/missing").mock(
        return_value=httpx.Response(
            404,
            json={
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing group"}],
            },
        )
    )

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")

    result = await group.get_group("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert group_route.called
    assert result.status_code == 404
    assert bytes(result.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing group"}]}'


async def test_create_group_returns_group_resource(monkeypatch):
    async def _fake_fhir_post_with_status(resource_type, body):
        assert resource_type == "Group"
        assert body["name"] == "Panel"
        return FHIRProxyResponse(data={"resourceType": "Group", "id": "group-1", "name": "Panel"}, status_code=201)

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(group, "fhir_post_with_status", _fake_fhir_post_with_status)

    result = await group.create_group(GroupResource.model_validate({"resourceType": "Group", "name": "Panel"}), claims={})

    assert isinstance(result, GroupResource)
    assert result.id == "group-1"


async def test_search_groups_handles_request_errors(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FailingClient)

    result = await group.search_groups(name="Panel", claims={})

    assert result == []


async def test_search_groups_returns_empty_for_upstream_error(monkeypatch, respx_mock):
    group_route = respx_mock.get("http://hapi.example/fhir/Group", params={"name": "Panel"}).mock(return_value=httpx.Response(500, json={"error": "boom"}))

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")

    result = await group.search_groups(name="Panel", claims={})

    assert group_route.called
    assert result == []


async def test_delete_group_returns_config_error(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    result = await group.delete_group("group-1", claims=None)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503


async def test_search_groups_skips_cross_origin_patient_reference(monkeypatch, respx_mock):
    group_route = respx_mock.get("http://hapi.example/fhir/Group", params={"name": "Panel"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Group",
                            "id": "group-1",
                            "name": "Panel",
                            "member": [{"entity": {"reference": "https://attacker.example/Patient/patient-123"}}],
                        }
                    }
                ],
            },
        )
    )
    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))

    result = await group.search_groups(name="Panel", claims={})

    assert not isinstance(result, JSONResponse)
    assert group_route.called
    assert len(result) == 1
    assert isinstance(result[0], GroupResource)
    assert len(respx_mock.calls) == 1


async def test_search_groups_fetches_members_concurrently_and_preserves_order(monkeypatch):
    patient_ids = ["patient-1", "patient-2", "patient-3", "patient-4"]
    all_started = asyncio.Event()
    completed = {patient_id: asyncio.Event() for patient_id in patient_ids}
    started: list[str] = []
    in_flight = 0
    max_in_flight = 0

    class _ReverseCompletionClient:
        async def get(self, url, headers):
            nonlocal in_flight, max_in_flight
            del headers
            patient_id = url.rsplit("/", 1)[-1]
            started.append(patient_id)
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if len(started) == len(patient_ids):
                all_started.set()
            await all_started.wait()
            patient_index = patient_ids.index(patient_id)
            if patient_index < len(patient_ids) - 1:
                await completed[patient_ids[patient_index + 1]].wait()
            completed[patient_id].set()
            in_flight -= 1
            return httpx.Response(200, json={"resourceType": "Patient", "id": patient_id})

    client = _ReverseCompletionClient()

    async def _fake_fhir_get(resource_type, params=None):
        assert resource_type == "Group"
        assert params == {"name": "Panel"}
        return {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Group",
                        "id": "group-1",
                        "name": "Panel",
                        "member": [
                            {"entity": {"reference": "Patient/patient-1"}},
                            {"entity": {"reference": "Patient/patient-2"}},
                        ],
                    }
                },
                {
                    "resource": {
                        "resourceType": "Group",
                        "id": "group-2",
                        "name": "Panel",
                        "member": [
                            {"entity": {"reference": "Patient/patient-3"}},
                            {"entity": {"reference": "Patient/patient-4"}},
                        ],
                    }
                },
            ],
        }

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(settings, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(group, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(group, "get_http_client", lambda profile: client)

    result = await group.search_groups(name="Panel", claims={})

    assert not isinstance(result, JSONResponse)
    assert max_in_flight == len(patient_ids)
    assert started == patient_ids
    assert [resource.id for resource in result] == ["group-1", "patient-1", "patient-2", "group-2", "patient-3", "patient-4"]
