"""HTTP adapter for job-package Questionnaire search and CRUD operations.

This router binds request parameters, selects auth, calls `src.services.fhir_proxy`
for HAPI FHIR transport, and maps upstream data to Questionnaire or OperationOutcome
responses. Successful API Questionnaire writes invalidate this process's cached title
list; edits made directly on HAPI FHIR still require ``?refresh=true``. Questionnaire
write bodies use the canonical QuestionnaireResource model and round-trip unknown FHIR
fields. It contains no Questionnaire resolution, batch-job logic, persistence, or
transport implementation; those responsibilities belong to `src.services` and
`src.util`.
"""

from fastapi import APIRouter, Body, Query, Security
from fastapi.responses import JSONResponse

from src.models.fhir import FHIRProxyResponse, OperationOutcome, QuestionnaireResource
from src.services.errorhandler import FHIR_PROXY_ERROR_RESPONSES, config_error_for, fhir_result_response, operation_outcome_response
from src.services.fhir_proxy import fhir_delete_with_status, fhir_get_with_status, fhir_post_with_status, fhir_put_with_status
from src.services.job_package_titles import JobPackageTitle, get_job_package_titles, invalidate_job_package_titles
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


def _invalidate_title_cache_after_success(result: FHIRProxyResponse) -> None:
    """Invalidate cached titles only after HAPI FHIR confirms a successful write."""
    if 200 <= result.status_code < 300:
        invalidate_job_package_titles()


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


@router.get("/jobpackage/title", summary="List Job Package Titles", response_model=list[JobPackageTitle], responses=FHIR_PROXY_ERROR_RESPONSES)
async def list_job_package_titles(
    refresh: bool = Query(False, description="Reload the list from HAPI FHIR instead of serving the cached copy."),
    claims: dict = Security(validate_token),
) -> JSONResponse | list[JobPackageTitle] | OperationOutcome:
    """List the ``name`` and ``title`` of every job package Questionnaire, for type-ahead title search.

    The list is cached in memory: loaded at startup, then served without contacting HAPI FHIR until a
    request passes ``refresh=true``. Entries are sorted by title. Prefix matching is done by the client.
    """
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await get_job_package_titles(refresh=refresh)
    if isinstance(result, FHIRProxyResponse):
        return fhir_result_response(result, OperationOutcome)
    return result


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
    body: QuestionnaireResource = Body(..., openapi_examples={"questionnaire": {"summary": "Questionnaire job package", "value": _QUESTIONNAIRE_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Create a new Questionnaire resource on HAPI FHIR for use as a job package."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_post_with_status("Questionnaire", body.to_fhir_json())
    _invalidate_title_cache_after_success(result)
    return fhir_result_response(result, QuestionnaireResource)


@router.put("/jobpackage/{resource_id}", summary="Update Job Package", response_model=QuestionnaireResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def update_job_package(
    resource_id: str,
    body: QuestionnaireResource = Body(..., openapi_examples={"questionnaire": {"summary": "Questionnaire job package", "value": _QUESTIONNAIRE_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | QuestionnaireResult:
    """Update an existing HAPI FHIR Questionnaire used as a job package."""
    body_json = body.to_fhir_json()
    if "id" in body_json and body_json["id"] != resource_id:
        return operation_outcome_response(400, "invariant", f"Questionnaire body id {body_json['id']!r} does not match path id {resource_id!r}.")
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_put_with_status("Questionnaire", resource_id, body_json)
    _invalidate_title_cache_after_success(result)
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
    _invalidate_title_cache_after_success(result)
    return fhir_result_response(result, OperationOutcome)
