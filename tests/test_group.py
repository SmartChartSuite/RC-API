from fastapi.responses import JSONResponse

from src.models.fhir import GroupResource, OperationOutcome, PatientResource
from src.routers import group
from src.services import fhir_proxy
from tests.fakes import FailingAsyncClient as _FailingClient
from tests.fakes import FakeAsyncClient as _FakeAsyncClient
from tests.fakes import FakeResponse as _FakeResponse


async def test_search_groups_returns_group_and_patients(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
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
                }
            )
        ],
        [_FakeResponse({"resourceType": "Patient", "id": "patient-123"})],
    ]

    monkeypatch.setattr(group, "config_errors", {})
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(group, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await group.search_groups(name="Panel", claims={})

    assert not isinstance(result, JSONResponse)
    assert isinstance(result[0], GroupResource)
    assert isinstance(result[1], PatientResource)
    assert _FakeAsyncClient.calls == [
        {
            "url": "http://hapi.example/fhir/Group",
            "params": {"name": "Panel"},
            "headers": {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
        },
        {
            "url": "http://external.example/fhir/Patient/patient-123",
            "params": None,
            "headers": {"Accept": "application/fhir+json"},
        },
    ]


async def test_get_group_returns_operation_outcome(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
                    "resourceType": "OperationOutcome",
                    "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing group"}],
                }
            )
        ]
    ]

    monkeypatch.setattr(group, "config_errors", {})
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await group.get_group("missing", claims={})

    assert isinstance(result, OperationOutcome)
    assert result.issue[0].diagnostics == "missing group"


async def test_create_group_returns_group_resource(monkeypatch):
    async def _fake_fhir_post(resource_type, body):
        assert resource_type == "Group"
        assert body["name"] == "Panel"
        return {"resourceType": "Group", "id": "group-1", "name": "Panel"}

    monkeypatch.setattr(group, "config_errors", {})
    monkeypatch.setattr(group, "fhir_post", _fake_fhir_post)

    result = await group.create_group({"resourceType": "Group", "name": "Panel"}, claims={})

    assert isinstance(result, GroupResource)
    assert result.id == "group-1"


async def test_search_groups_handles_request_errors(monkeypatch):
    monkeypatch.setattr(group, "config_errors", {})
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(group, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FailingClient)

    result = await group.search_groups(name="Panel", claims={})

    assert result == []


async def test_search_groups_returns_empty_for_upstream_error(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [[_FakeResponse({"error": "boom"}, status_code=500)]]

    monkeypatch.setattr(group, "config_errors", {})
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(group, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await group.search_groups(name="Panel", claims={})

    assert result == []


async def test_delete_group_returns_config_error(monkeypatch):
    monkeypatch.setattr(group, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    result = await group.delete_group("group-1", claims=None)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503


async def test_search_groups_skips_cross_origin_patient_reference(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queued_responses = [
        [
            _FakeResponse(
                {
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
                }
            )
        ],
        [],
    ]
    monkeypatch.setattr(group, "config_errors", {})
    monkeypatch.setattr(fhir_proxy, "hapi_fhir_cql_execution_url", "http://hapi.example/fhir")
    monkeypatch.setattr(group, "external_fhir_server_url", "http://external.example/fhir")
    monkeypatch.setattr(group, "external_fhir_server_auth", "Bearer token")
    monkeypatch.setattr(fhir_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await group.search_groups(name="Panel", claims={})

    assert not isinstance(result, JSONResponse)
    assert len(result) == 1
    assert isinstance(result[0], GroupResource)
    assert len(_FakeAsyncClient.calls) == 1
