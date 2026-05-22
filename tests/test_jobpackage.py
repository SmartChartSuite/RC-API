from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, OperationOutcome, QuestionnaireResource
from src.routers import jobpackage


async def test_search_job_packages_passes_filters(monkeypatch):
    captured: dict = {}

    async def _fake_fhir_get(resource_type, resource_id=None, params=None):
        captured["resource_type"] = resource_type
        captured["resource_id"] = resource_id
        captured["params"] = params
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 1,
            "entry": [{"resource": {"resourceType": "Questionnaire", "id": "q-1", "name": "Registry"}}],
        }

    monkeypatch.setattr(jobpackage, "config_errors", {})
    monkeypatch.setattr(jobpackage, "fhir_get", _fake_fhir_get)

    result = await jobpackage.search_job_packages(name="Registry", version="1.0", claims={})

    assert isinstance(result, BundleResource)
    assert captured == {
        "resource_type": "Questionnaire",
        "resource_id": None,
        "params": {"context": "smartchartui", "name": "Registry", "version": "1.0"},
    }
    assert result.total == 1


async def test_get_job_package_returns_operation_outcome(monkeypatch):
    async def _fake_fhir_get(resource_type, resource_id=None, params=None):
        return {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing questionnaire"}],
        }

    monkeypatch.setattr(jobpackage, "config_errors", {})
    monkeypatch.setattr(jobpackage, "fhir_get", _fake_fhir_get)

    result = await jobpackage.get_job_package("missing", claims={})

    assert isinstance(result, OperationOutcome)
    assert result.issue[0].diagnostics == "missing questionnaire"


async def test_create_job_package_returns_questionnaire(monkeypatch):
    async def _fake_fhir_post(resource_type, body):
        assert resource_type == "Questionnaire"
        assert body["name"] == "Registry"
        return {"resourceType": "Questionnaire", "id": "q-1", "name": "Registry"}

    monkeypatch.setattr(jobpackage, "config_errors", {})
    monkeypatch.setattr(jobpackage, "fhir_post", _fake_fhir_post)

    result = await jobpackage.create_job_package({"resourceType": "Questionnaire", "name": "Registry"}, claims={})

    assert isinstance(result, QuestionnaireResource)
    assert result.id == "q-1"


async def test_delete_job_package_returns_config_error(monkeypatch):
    monkeypatch.setattr(jobpackage, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    result = await jobpackage.delete_job_package("q-1", _=None)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
