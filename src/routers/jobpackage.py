"""CRUD /jobpackage — Questionnaire resources proxied to HAPI FHIR."""

from fastapi import APIRouter, Security
from fastapi.responses import JSONResponse

from src.models.fhir import OperationOutcome, QuestionnaireResource
from src.services.errorhandler import config_error_response, operation_outcome_responses
from src.services.fhir_proxy import fhir_delete, fhir_get, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.settings import config_errors

router = APIRouter(tags=["Job Packages"])

QuestionnaireResult = QuestionnaireResource | OperationOutcome


def _check_config():
    if "HAPI_FHIR_CQL_EXECUTION_URL" in config_errors:
        return config_error_response(["HAPI_FHIR_CQL_EXECUTION_URL"])
    return None


@router.get("/jobpackage", response_model=list[QuestionnaireResource] | OperationOutcome, responses=operation_outcome_responses(503))
async def search_job_packages(
    name: str | None = None,
    version: str | None = None,
    claims: dict = Security(validate_token),
) -> JSONResponse | list[QuestionnaireResource] | OperationOutcome:
    """Search job package Questionnaires on HAPI FHIR."""
    if err := _check_config():
        return err
    params: dict = {"context": "smartchartui"}
    if name:
        params["name"] = name
    if version:
        params["version"] = version
    data = await fhir_get("Questionnaire", params=params)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    entries = data.get("entry", []) or []
    return [QuestionnaireResource.model_validate(entry.get("resource", {})) for entry in entries if entry.get("resource", {}).get("resourceType") == "Questionnaire"]


@router.get("/jobpackage/{resource_id}", response_model=QuestionnaireResult, responses=operation_outcome_responses(503))
async def get_job_package(
    resource_id: str,
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Get a specific job package Questionnaire by ID."""
    if err := _check_config():
        return err
    data = await fhir_get("Questionnaire", resource_id)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return QuestionnaireResource.model_validate(data)


@router.post("/jobpackage", response_model=QuestionnaireResult, responses=operation_outcome_responses(503))
async def create_job_package(
    body: dict,
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Create a new job package Questionnaire on HAPI FHIR."""
    if err := _check_config():
        return err
    data = await fhir_post("Questionnaire", body)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return QuestionnaireResource.model_validate(data)


@router.put("/jobpackage/{resource_id}", response_model=QuestionnaireResult, responses=operation_outcome_responses(503))
async def update_job_package(
    resource_id: str,
    body: dict,
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Update an existing job package Questionnaire."""
    if err := _check_config():
        return err
    data = await fhir_put("Questionnaire", resource_id, body)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return QuestionnaireResource.model_validate(data)


@router.delete("/jobpackage/{resource_id}", response_model=OperationOutcome, responses=operation_outcome_responses(503))
async def delete_job_package(
    resource_id: str,
    _: None = Security(require_admin),
) -> JSONResponse | OperationOutcome:
    """Delete a job package Questionnaire. Requires 'admin' scope."""
    if err := _check_config():
        return err
    return OperationOutcome.model_validate(await fhir_delete("Questionnaire", resource_id))
