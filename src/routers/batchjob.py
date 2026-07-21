"""POST/GET/DELETE /batchjob — batch job submission and status/results retrieval."""

import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, cast

import httpx
from fastapi import APIRouter, BackgroundTasks, Query, Response, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import BundleJSON, BundleResource, ParametersParameter, ParametersResponse
from src.models.job_request import JobRequest
from src.models.job_response import BatchJobAcceptedResponse
from src.services.errorhandler import config_error_response, operation_outcome_response, operation_outcome_responses
from src.services.fhir_proxy import fhir_get
from src.services.job_orchestrator import run_batch_job
from src.services.job_state import (
    BatchJobs,
    create_batch_job_with_response,
    delete_batch_job_record,
    get_batch_job,
    get_responses,
    query_batch_jobs,
)
from src.util.auth import require_admin, validate_token
from src.util.settings import (
    config_errors,
    external_fhir_server_auth,
    external_fhir_server_url,
)

router = APIRouter(tags=["Batch Jobs"])


def _patient_name(patient: dict[str, Any] | None) -> str | None:
    if not patient:
        return None
    names = patient.get("name")
    if not isinstance(names, list) or not names:
        return None
    primary = names[0]
    if not isinstance(primary, dict):
        return None
    family = primary.get("family")
    given = primary.get("given")
    first = given[0] if isinstance(given, list) and given else None
    if isinstance(family, str) and family and isinstance(first, str) and first:
        return f"{family}, {first}"
    return family or first


def _questionnaire_response_status(batch_id: str) -> str | None:
    responses = get_responses(batch_job_id=batch_id)
    if not responses:
        return None
    response_resource = responses[0].response
    status = response_resource.get("status") if isinstance(response_resource, dict) else None
    return status if isinstance(status, str) and status else None


def _matches_filter(value: str | None, expected: str | None, *, partial: bool = False) -> bool:
    if not expected:
        return True
    if not value:
        return False
    if partial:
        return expected.casefold() in value.casefold()
    return value.casefold() == expected.casefold()


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_date_filter(value: str | None, param_name: str) -> tuple[date | None, JSONResponse | None]:
    parsed = _parse_iso_date(value)
    if value and parsed is None:
        return None, operation_outcome_response(400, "value", f"Query parameter '{param_name}' must be a valid ISO date in YYYY-MM-DD format.")
    return parsed, None


def _matches_date_range(value: date | None, start: date | None, end: date | None) -> bool:
    if start is None and end is None:
        return True
    if value is None:
        return False
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


def _to_batch_job_parameters(job: BatchJobs, patient: dict[str, Any] | None = None, include_patient_resource: bool = False) -> ParametersResponse:
    parameters = [
        ParametersParameter(name="batchId", valueString=job.batch_id),
        ParametersParameter(name="patientId", valueString=job.patient_id),
        ParametersParameter(name="jobPackage", valueString=job.job_package),
        ParametersParameter(name="startedBy", valueString=job.started_by),
        ParametersParameter(name="batchJobStatus", valueString=cast(Any, job.status)),
        ParametersParameter(name="jobStartDateTime", valueDateTime=job.created_at.isoformat()),
    ]
    form_status = _questionnaire_response_status(job.batch_id)
    if form_status:
        parameters.append(ParametersParameter(name="questionnaireResponseStatus", valueString=form_status))
    patient_name = _patient_name(patient)
    if patient_name:
        parameters.append(ParametersParameter(name="patientName", valueString=patient_name))
    birth_date = patient.get("birthDate") if isinstance(patient, dict) else None
    if isinstance(birth_date, str) and birth_date:
        parameters.append(ParametersParameter(name="patientDob", valueDate=birth_date))
    gender = patient.get("gender") if isinstance(patient, dict) else None
    if isinstance(gender, str) and gender:
        parameters.append(ParametersParameter(name="patientGender", valueCode=gender))
    if job.completed_at:
        parameters.append(ParametersParameter(name="jobCompletedDateTime", valueDateTime=job.completed_at.isoformat()))
    if include_patient_resource and patient is not None:
        parameters.append(ParametersParameter(name="patientResource", resource=patient))
    return ParametersResponse(parameter=parameters)


def _required_config() -> list[str]:
    return [k for k in ("EXTERNAL_FHIR_SERVER_URL", "HAPI_FHIR_CQL_EXECUTION_URL") if k in config_errors]


async def _fetch_patient(patient_id: str) -> dict | None:
    """Fetch Patient resource from external FHIR server for include_patient support."""
    assert external_fhir_server_url
    url = f"{external_fhir_server_url.rstrip('/')}/Patient/{patient_id}"
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.is_success:
            return resp.json()
    except Exception as exc:
        logger.warning(f"Could not fetch Patient/{patient_id}: {exc}")
    return None


def _questionnaire_reference(questionnaire: dict[str, Any]) -> str | None:
    canonical = questionnaire.get("url")
    if isinstance(canonical, str) and canonical:
        return canonical
    questionnaire_id = questionnaire.get("id")
    if isinstance(questionnaire_id, str) and questionnaire_id:
        return f"Questionnaire/{questionnaire_id}"
    return None


def _prefill_questionnaire_response_items(questionnaire_items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    prefilled_items: list[dict[str, Any]] = []
    for item in questionnaire_items or []:
        prefilled_item: dict[str, Any] = {}
        for field in ("linkId", "text", "definition"):
            value = item.get(field)
            if isinstance(value, str) and value:
                prefilled_item[field] = value

        child_items = _prefill_questionnaire_response_items(item.get("item"))
        if child_items:
            prefilled_item["item"] = child_items

        if prefilled_item:
            prefilled_items.append(prefilled_item)

    return prefilled_items


async def _resolve_questionnaire(job_package: str, job_package_version: str | None) -> dict[str, Any] | JSONResponse:
    params: dict[str, str] = {"name": job_package}
    if job_package_version:
        params["version"] = job_package_version

    questionnaire_bundle = await fhir_get("Questionnaire", params=params)
    if questionnaire_bundle.get("resourceType") == "OperationOutcome":
        return operation_outcome_response(503, "transient", "Could not resolve Questionnaire for the requested job package.")

    entries = questionnaire_bundle.get("entry", [])
    if not entries:
        details = f"No Questionnaire found with name '{job_package}'"
        if job_package_version:
            details += f" and version '{job_package_version}'"
        return operation_outcome_response(404, "not-found", f"{details}.")

    if len(entries) > 1:
        return operation_outcome_response(
            409,
            "multiple-matches",
            f"Multiple Questionnaires matched jobPackage '{job_package}'. Specify both jobPackage and jobPackageVersion to select a single Questionnaire.",
        )

    questionnaire = entries[0].get("resource", {})
    if questionnaire.get("resourceType") != "Questionnaire":
        return operation_outcome_response(503, "transient", "FHIR server returned an unexpected resource while resolving the Questionnaire.")
    return questionnaire


@router.post(
    "/batchjob",
    summary="Start Batch Job",
    response_model=BatchJobAcceptedResponse,
    responses=operation_outcome_responses(404, 409, 500, 503),
    response_model_exclude_none=True,
)
async def post_batch_job(
    body: JobRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    claims: dict = Security(validate_token),
) -> BatchJobAcceptedResponse | JSONResponse:
    """Submit a job package run for a patient.

    The request body must be a FHIR ``Parameters`` resource containing at least
    ``patientId`` and ``jobPackage``. ``jobPackageVersion`` may be supplied to
    resolve an exact Questionnaire version, and repeated ``job`` parameters can
    limit execution to a subset of the tasks defined on that Questionnaire.

    On success, the API creates an initial local ``QuestionnaireResponse`` with
    ``status = in-progress`` and returns a FHIR ``Parameters`` resource with the
    new ``batchId`` and a ``batchJobQuestionnaireResponse`` reference.
    Execution then continues asynchronously in the background.
    """
    missing = _required_config()
    if missing:
        return config_error_response(missing)

    patient_id: str | None = body.get_param("patientId")
    job_package: str | None = body.get_param("jobPackage")
    job_package_version: str | None = body.get_param("jobPackageVersion")
    job_names = body.get_params("job")
    started_by = claims.get("sub", "unknown")

    assert patient_id
    assert job_package

    questionnaire = await _resolve_questionnaire(job_package, job_package_version)
    if isinstance(questionnaire, JSONResponse):
        return questionnaire

    questionnaire_reference = _questionnaire_reference(questionnaire)
    if not questionnaire_reference:
        return operation_outcome_response(500, "processing", "Resolved Questionnaire is missing both url and id, so a QuestionnaireResponse could not be created.")

    batch_id = str(uuid.uuid4())
    response_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    questionnaire_id = questionnaire.get("id")

    if not questionnaire_id:
        return operation_outcome_response(500, "processing", "Resolved Questionnaire is missing an id, so the batch job could not be started.")

    response_resource = {
        "resourceType": "QuestionnaireResponse",
        "id": response_id,
        "status": "in-progress",
        "questionnaire": questionnaire_reference,
        "subject": {"reference": f"Patient/{patient_id}"},
        "item": _prefill_questionnaire_response_items(questionnaire.get("item")),
    }

    created = create_batch_job_with_response(batch_id, patient_id, job_package, started_by, response_id, response_resource)
    if not created:
        return operation_outcome_response(
            500,
            "processing",
            "The batch job and QuestionnaireResponse could not be saved to the database. See logs for details.",
        )

    background_tasks.add_task(run_batch_job, batch_id, patient_id, job_package, questionnaire_id, job_package_version, job_names)

    response.headers["Location"] = f"/batchjob/{batch_id}"
    return BatchJobAcceptedResponse(
        parameter=[
            ParametersParameter(name="batchId", valueString=batch_id),
            ParametersParameter(name="jobStartDateTime", valueDateTime=start_time),
            ParametersParameter(name="patientId", valueString=patient_id),
            ParametersParameter(name="jobPackage", valueString=job_package),
            ParametersParameter(
                name="batchJobQuestionnaireResponse",
                valueReference={"reference": f"QuestionnaireResponse/{response_id}"},
            ),
            ParametersParameter(name="batchJobStatus", valueString="pending"),
        ]
    )


@router.get(
    "/batchjob",
    summary="List Batch Jobs",
    response_model=BundleResource,
    responses=operation_outcome_responses(400, 503),
    response_model_exclude_none=True,
)
async def list_batch_jobs(
    include_patient: bool = False,
    page: Annotated[int, Query(ge=0, description="Zero-based page number.")] = 0,
    size: Annotated[int, Query(ge=1, description="Number of batch jobs to return per page.")] = 10,
    questionnaire_response_status: Annotated[str | None, Query(alias="questionnaireResponseStatus")] = None,
    patient_name: Annotated[str | None, Query(alias="patientName")] = None,
    patient_gender: Annotated[str | None, Query(alias="patientGender")] = None,
    job_package_filter: Annotated[str | None, Query(alias="jobPackage")] = None,
    batch_job_status: Annotated[str | None, Query(alias="batchJobStatus")] = None,
    dob_start_date: Annotated[str | None, Query(alias="dobStartDate")] = None,
    dob_end_date: Annotated[str | None, Query(alias="dobEndDate")] = None,
    job_run_start_date: Annotated[str | None, Query(alias="jobRunStartDate")] = None,
    job_run_end_date: Annotated[str | None, Query(alias="jobRunEndDate")] = None,
    claims: dict = Security(validate_token),
) -> BundleJSON | JSONResponse:
    """List batch jobs as FHIR ``Parameters`` resources.

    Set ``include_patient=true`` to embed each Patient resource from the
    external FHIR server as ``patientResource``. Results are paginated with
    zero-based ``page`` and ``size`` query parameters.
    """
    dob_start, error = _parse_iso_date_filter(dob_start_date, "dobStartDate")
    if error:
        return error
    dob_end, error = _parse_iso_date_filter(dob_end_date, "dobEndDate")
    if error:
        return error
    run_start, error = _parse_iso_date_filter(job_run_start_date, "jobRunStartDate")
    if error:
        return error
    run_end, error = _parse_iso_date_filter(job_run_end_date, "jobRunEndDate")
    if error:
        return error

    all_jobs: list[BatchJobs] = query_batch_jobs(
        status=batch_job_status,
        job_package=job_package_filter,
        questionnaire_response_status=questionnaire_response_status,
        run_start_date=run_start,
        run_end_date=run_end,
    )

    patients_by_id: dict[str, dict[str, Any] | None] = {}
    filtered_jobs: list[tuple[BatchJobs, dict[str, Any] | None]] = []
    for job in all_jobs:
        if job.patient_id not in patients_by_id:
            patients_by_id[job.patient_id] = await _fetch_patient(job.patient_id)
        patient = patients_by_id[job.patient_id]
        derived_patient_name = _patient_name(patient)
        derived_patient_gender = patient.get("gender") if isinstance(patient, dict) else None
        derived_patient_dob = _parse_iso_date(patient.get("birthDate") if isinstance(patient, dict) else None)

        if not _matches_filter(derived_patient_name, patient_name, partial=True):
            continue
        if not _matches_filter(cast(str | None, derived_patient_gender), patient_gender):
            continue
        if not _matches_date_range(derived_patient_dob, dob_start, dob_end):
            continue

        filtered_jobs.append((job, patient))

    start = page * size
    end = start + size
    results = []
    for job, patient in filtered_jobs[start:end]:
        results.append({"resource": _to_batch_job_parameters(job, patient=patient, include_patient_resource=include_patient).model_dump(exclude_none=True)})

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(filtered_jobs),
        "entry": results,
    }


@router.get("/batchjob/{batch_id}", summary="Get Batch Job Results", response_model=BundleResource, responses=operation_outcome_responses(202, 404, 500))
async def get_batch_job_results(batch_id: str, claims: dict = Security(validate_token)) -> BundleJSON | JSONResponse:
    """Get the full FHIR result Bundle for a completed batch job.

    The Bundle is stored during background job execution, so this endpoint is a
    pure DB read. Returns the stored Bundle as-is, or an ``OperationOutcome`` if
    the batch job does not exist, is still running, or completed without a
    stored result Bundle.
    """
    job = get_batch_job(batch_id)
    if not job:
        return operation_outcome_response(
            404,
            "not-found",
            f"Batch Job ID {batch_id} was not found.",
        )
    if job.status in ("pending", "running"):
        return operation_outcome_response(
            202,
            "informational",
            f"Batch job {batch_id} is still {job.status}. Poll GET /batchjob/{{id}} and retry when status is 'complete'.",
            severity="information",
        )
    if not job.result_bundle:
        return operation_outcome_response(
            500,
            "transient",
            f"Batch job {batch_id} has no result bundle. The job may have encountered an error - check job status.",
        )
    return BundleJSON(**job.result_bundle)


@router.get(
    "/batchjob/{batch_id}/status",
    summary="Get Batch Job Status",
    response_model=ParametersResponse,
    responses=operation_outcome_responses(404),
    response_model_exclude_none=True,
)
async def get_batch_job_status(
    batch_id: str,
    include_patient: bool = False,
    claims: dict = Security(validate_token),
) -> ParametersResponse | JSONResponse:
    """Get the status of a batch job (lightweight polling endpoint).

    Returns batch job metadata as a FHIR ``Parameters`` resource without loading
    the full result Bundle. Use ``GET /batchjob/{id}`` to retrieve the stored
    Bundle once ``batchJobStatus`` is ``complete``.
    """
    job = get_batch_job(batch_id)
    if not job:
        return operation_outcome_response(404, "not-found", f"Batch Job ID {batch_id} was not found.")
    patient = await _fetch_patient(job.patient_id)
    return _to_batch_job_parameters(job, patient=patient, include_patient_resource=include_patient)


@router.delete("/batchjob/{batch_id}", summary="Delete Batch Job")
async def delete_batch_job(batch_id: str, claims: None = Security(require_admin)):
    """Delete a batch job and its child jobs. Requires 'admin' scope."""
    return delete_batch_job_record(batch_id)
