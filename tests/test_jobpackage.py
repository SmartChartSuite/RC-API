"""Tests the job-package router's Questionnaire search, read, create, and configuration-error responses.

The tests monkeypatch `src.routers.jobpackage` FHIR proxy calls and settings; write calls use the canonical
QuestionnaireResource model, while transport details are covered in `test_fhir_proxy.py`."""

from fastapi.responses import JSONResponse

from src.models.fhir import QuestionnaireResource
from src.routers import jobpackage
from src.services.fhir_proxy import FHIRProxyResponse
from src.util.settings import settings


async def test_search_job_packages_passes_filters(monkeypatch):
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
                "entry": [{"resource": {"resourceType": "Questionnaire", "id": "q-1", "name": "Registry"}}],
            },
            status_code=200,
        )

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(jobpackage, "fhir_get_with_status", _fake_fhir_get_with_status)

    result = await jobpackage.search_job_packages(name="Registry", version="1.0", claims={})

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], QuestionnaireResource)
    assert captured == {
        "resource_type": "Questionnaire",
        "resource_id": None,
        "params": {"context": "smartchartui", "name": "Registry", "version": "1.0"},
    }
    assert result[0].id == "q-1"


async def test_get_job_package_returns_operation_outcome(monkeypatch):
    async def _fake_fhir_get_with_status(resource_type, resource_id=None, params=None):
        return FHIRProxyResponse(
            data={
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": "not-found", "diagnostics": "missing questionnaire"}],
            },
            status_code=404,
        )

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(jobpackage, "fhir_get_with_status", _fake_fhir_get_with_status)

    result = await jobpackage.get_job_package("missing", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 404
    assert bytes(result.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing questionnaire"}]}'


async def test_create_job_package_returns_questionnaire(monkeypatch):
    async def _fake_fhir_post_with_status(resource_type, body):
        assert resource_type == "Questionnaire"
        assert body["name"] == "Registry"
        return FHIRProxyResponse(data={"resourceType": "Questionnaire", "id": "q-1", "name": "Registry"}, status_code=201)

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(jobpackage, "fhir_post_with_status", _fake_fhir_post_with_status)

    result = await jobpackage.create_job_package(QuestionnaireResource.model_validate({"resourceType": "Questionnaire", "name": "Registry"}), claims={})

    assert isinstance(result, QuestionnaireResource)
    assert result.id == "q-1"


async def test_delete_job_package_returns_config_error(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    result = await jobpackage.delete_job_package("q-1", _=None)

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
