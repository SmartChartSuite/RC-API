"""Tests the library router's FHIR search, read, create, and configuration-error responses.

The tests monkeypatch `src.routers.library` FHIR proxy calls and settings; write calls use the canonical
LibraryResource model, while transport details are covered in `test_fhir_proxy.py`."""

from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, LibraryResource
from src.routers import library
from src.services.fhir_proxy import FHIRProxyResponse
from src.util.settings import settings


async def test_search_libraries_passes_name_filter(monkeypatch):
    captured: dict = {}

    async def _fake_fhir_get_with_status(resource_type, resource_id=None, params=None):
        captured["resource_type"] = resource_type
        captured["resource_id"] = resource_id
        captured["params"] = params
        return FHIRProxyResponse(
            data={
                "resourceType": "Bundle",
                "type": "searchset",
                "total": 1,
                "entry": [{"resource": {"resourceType": "Library", "id": "lib-1", "name": "RiskLib"}}],
            },
            status_code=200,
        )

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(library, "fhir_get_with_status", _fake_fhir_get_with_status)

    result = await library.search_libraries(name="RiskLib", claims={})

    assert isinstance(result, BundleResource)
    assert captured == {
        "resource_type": "Library",
        "resource_id": None,
        "params": {"name": "RiskLib"},
    }
    assert result.total == 1


async def test_get_library_returns_operation_outcome(monkeypatch):
    async def _fake_fhir_get_with_status(resource_type, resource_id=None, params=None):
        return FHIRProxyResponse(
            data={
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing library"}],
            },
            status_code=404,
        )

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(library, "fhir_get_with_status", _fake_fhir_get_with_status)

    result = await library.get_library("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404
    assert bytes(result.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing library"}]}'


async def test_create_library_returns_library_resource(monkeypatch):
    async def _fake_fhir_post_with_status(resource_type, body):
        assert resource_type == "Library"
        assert body["name"] == "RiskLib"
        return FHIRProxyResponse(data={"resourceType": "Library", "id": "lib-1", "name": "RiskLib"}, status_code=201)

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(library, "fhir_post_with_status", _fake_fhir_post_with_status)

    result = await library.create_library(LibraryResource.model_validate({"resourceType": "Library", "name": "RiskLib"}), claims={})

    assert isinstance(result, LibraryResource)
    assert result.id == "lib-1"


async def test_delete_library_returns_config_error(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    result = await library.delete_library("lib-1", _=None)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
