from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, LibraryResource, OperationOutcome
from src.routers import library


async def test_search_libraries_passes_name_filter(monkeypatch):
    captured: dict = {}

    async def _fake_fhir_get(resource_type, resource_id=None, params=None):
        captured["resource_type"] = resource_type
        captured["resource_id"] = resource_id
        captured["params"] = params
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 1,
            "entry": [{"resource": {"resourceType": "Library", "id": "lib-1", "name": "RiskLib"}}],
        }

    monkeypatch.setattr(library, "config_errors", {})
    monkeypatch.setattr(library, "fhir_get", _fake_fhir_get)

    result = await library.search_libraries(name="RiskLib", claims={})

    assert isinstance(result, BundleResource)
    assert captured == {
        "resource_type": "Library",
        "resource_id": None,
        "params": {"name": "RiskLib"},
    }
    assert result.total == 1


async def test_get_library_returns_operation_outcome(monkeypatch):
    async def _fake_fhir_get(resource_type, resource_id=None, params=None):
        return {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing library"}],
        }

    monkeypatch.setattr(library, "config_errors", {})
    monkeypatch.setattr(library, "fhir_get", _fake_fhir_get)

    result = await library.get_library("missing", claims={})

    assert isinstance(result, OperationOutcome)
    assert result.issue[0].diagnostics == "missing library"


async def test_create_library_returns_library_resource(monkeypatch):
    async def _fake_fhir_post(resource_type, body):
        assert resource_type == "Library"
        assert body["name"] == "RiskLib"
        return {"resourceType": "Library", "id": "lib-1", "name": "RiskLib"}

    monkeypatch.setattr(library, "config_errors", {})
    monkeypatch.setattr(library, "fhir_post", _fake_fhir_post)

    result = await library.create_library({"resourceType": "Library", "name": "RiskLib"}, claims={})

    assert isinstance(result, LibraryResource)
    assert result.id == "lib-1"


async def test_delete_library_returns_config_error(monkeypatch):
    monkeypatch.setattr(library, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    result = await library.delete_library("lib-1", _=None)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
