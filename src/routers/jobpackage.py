"""CRUD /jobpackage — Questionnaire resources proxied to HAPI FHIR."""

from fastapi import APIRouter, Body, Security
from fastapi.responses import JSONResponse

from src.models.fhir import OperationOutcome, QuestionnaireResource
from src.services.errorhandler import FHIR_PROXY_ERROR_RESPONSES, config_error_for, fhir_result_response
from src.services.fhir_proxy import fhir_delete_with_status, fhir_get_with_status, fhir_post_with_status, fhir_put_with_status
from src.util.auth import require_admin, validate_token
from src.util.fhir_bundle import get_bundle_resources
from src.util.settings import settings

router = APIRouter(tags=["Job Packages"])

_QUESTIONNAIRE_EXAMPLE = {
    "resourceType": "Questionnaire",
    "id": "setnet-syphilis-infant-follow-up",
    "name": "SETNETSyphilisInfantFollowUp",
    "version": "2025.08",
    "status": "active",
    "url": "http://example.org/fhir/Questionnaire/setnet-syphilis-infant-follow-up|2025.08",
    "subjectType": ["Patient"],
}

QuestionnaireResult = QuestionnaireResource | OperationOutcome


@router.get("/jobpackage", summary="Search Job Packages", response_model=list[QuestionnaireResource] | OperationOutcome, responses=FHIR_PROXY_ERROR_RESPONSES)
async def search_job_packages(
    name: str | None = None,
    version: str | None = None,
    claims: dict = Security(validate_token),
) -> JSONResponse | list[QuestionnaireResource] | OperationOutcome:
    """Search job package Questionnaires on HAPI FHIR.

    Searches are constrained to the ``smartchartui`` Questionnaire context and
    may also be filtered by ``name`` and ``version``.
    """
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    params: dict = {"context": "smartchartui"}
    if name:
        params["name"] = name
    if version:
        params["version"] = version
    result = await fhir_get_with_status("Questionnaire", params=params)
    if not 200 <= result.status_code < 300:
        return fhir_result_response(result, OperationOutcome)
    data = result.data
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return [QuestionnaireResource.model_validate(resource) for resource in get_bundle_resources(data, "Questionnaire")]


@router.get("/jobpackage/{resource_id}", summary="Get Job Package", response_model=QuestionnaireResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def get_job_package(
    resource_id: str,
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Get a specific job package Questionnaire by ID."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_get_with_status("Questionnaire", resource_id)
    return fhir_result_response(result, QuestionnaireResource)


@router.post("/jobpackage", summary="Create Job Package", response_model=QuestionnaireResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def create_job_package(
    body: dict = Body(..., openapi_examples={"questionnaire": {"summary": "Questionnaire job package", "value": _QUESTIONNAIRE_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Create a new Questionnaire resource on HAPI FHIR for use as a job package."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_post_with_status("Questionnaire", body)
    return fhir_result_response(result, QuestionnaireResource)


@router.put("/jobpackage/{resource_id}", summary="Update Job Package", response_model=QuestionnaireResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def update_job_package(
    resource_id: str,
    body: dict = Body(..., openapi_examples={"questionnaire": {"summary": "Questionnaire job package", "value": _QUESTIONNAIRE_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Update an existing HAPI FHIR Questionnaire used as a job package."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_put_with_status("Questionnaire", resource_id, body)
    return fhir_result_response(result, QuestionnaireResource)


@router.delete("/jobpackage/{resource_id}", summary="Delete Job Package", response_model=OperationOutcome, responses=FHIR_PROXY_ERROR_RESPONSES)
async def delete_job_package(
    resource_id: str,
    _: None = Security(require_admin),
) -> JSONResponse | OperationOutcome:
    """Delete a job package Questionnaire. Requires 'admin' scope."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_delete_with_status("Questionnaire", resource_id)
    return fhir_result_response(result, OperationOutcome)
