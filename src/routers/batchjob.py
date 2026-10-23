"""HTTP adapter for batch-job submission, listing, streaming, polling, results retrieval, and deletion.

This router binds `/batchjob` parameters, selects `validate_token` or
`require_admin`, delegates use cases to `src.services.batch_job_service`, and maps
service outcomes to FHIR `Parameters`, Bundles, or OperationOutcome responses.
Questionnaire resolution, filtering, persistence, worker execution, and domain logic
belong to the service layer (`batch_job_service`, `batch_job_filters`, the `job_state` package,
and `batch_worker`), not this router.
"""

import asyncio
import hashlib
import json
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Query, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse

from src.models.fhir import FHIRJSON, BundleResource, ParametersParameter, ParametersResponse
from src.models.job_request import JobRequest
from src.models.job_response import BatchJobAcceptedResponse
from src.services import batch_job_service
from src.services.errorhandler import config_error_response, operation_outcome_response, operation_outcome_responses
from src.util.auth import require_admin, validate_token
from src.util.current_user import CurrentUser, get_current_user
from src.util.settings import settings

router = APIRouter(tags=["Batch Jobs"])


class _DisconnectAwareRequest(Protocol):
    async def is_disconnected(self) -> bool: ...


def _service_error_response(error: batch_job_service.ServiceFailure) -> JSONResponse:
    if isinstance(error, batch_job_service.MissingConfig):
        return config_error_response(list(error.names))
    if isinstance(error, batch_job_service.StoredErrorBundle):
        return JSONResponse(content=error.bundle, status_code=500)
    return operation_outcome_response(error.status_code, error.code, error.diagnostics, error.severity)


@router.post(
    "/batchjob",
    summary="Start Batch Job",
    response_model=BatchJobAcceptedResponse,
    responses=operation_outcome_responses(400, 404, 409, 500, 503),
    response_model_exclude_none=True,
)
async def post_batch_job(
    body: JobRequest,
    response: Response,
    claims: dict = Security(validate_token),
    current_user: CurrentUser | None = Security(get_current_user),
) -> BatchJobAcceptedResponse | JSONResponse:
    """Submit a job package run for a patient.

    The request body must be a FHIR ``Parameters`` resource containing at least
    ``patientId`` and ``jobPackage``. ``jobPackageVersion`` may be supplied to
    resolve an exact Questionnaire version, and repeated ``job`` parameters can
    limit execution to a subset of the tasks defined on that Questionnaire.

    On success, the API creates an initial local ``QuestionnaireResponse`` with
    ``status = in-progress`` and returns a FHIR ``Parameters`` resource with the
    new ``batchId`` and a ``batchJobQuestionnaireResponse`` reference.
    Execution is persisted to the database and claimed asynchronously by the
    embedded durable batch worker.
    """
    resolved_user = current_user if isinstance(current_user, CurrentUser) else None
    submission = await batch_job_service.submit_batch_job(body, claims, resolved_user)
    if not isinstance(submission, batch_job_service.BatchJobSubmission):
        return _service_error_response(submission)

    response.headers["Location"] = f"/batchjob/{submission.batch_id}"
    return BatchJobAcceptedResponse(
        parameter=[
            ParametersParameter(name="batchId", valueString=submission.batch_id),
            ParametersParameter(name="jobStartDateTime", valueDateTime=submission.start_time),
            ParametersParameter(name="patientId", valueString=submission.patient_id),
            ParametersParameter(name="jobPackage", valueString=submission.job_package),
            ParametersParameter(
                name="batchJobQuestionnaireResponse",
                valueReference={"reference": f"QuestionnaireResponse/{submission.response_id}"},
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
    size: Annotated[int, Query(ge=1, le=100, description="Number of batch jobs to return per page (maximum 100).")] = 10,
    questionnaire_response_status: Annotated[str | None, Query(alias="questionnaireResponseStatus")] = None,
    patient_name: Annotated[str | None, Query(alias="patientName")] = None,
    patient_gender: Annotated[str | None, Query(alias="patientGender")] = None,
    job_package_filter: Annotated[str | None, Query(alias="jobPackage")] = None,
    batch_job_status: Annotated[
        str | None,
        Query(alias="batchJobStatus", description="Comma-separated batch status values: pending, running, complete, partial, or error."),
    ] = None,
    dob_start_date: Annotated[str | None, Query(alias="dobStartDate")] = None,
    dob_end_date: Annotated[str | None, Query(alias="dobEndDate")] = None,
    job_run_start_date: Annotated[str | None, Query(alias="jobRunStartDate")] = None,
    job_run_end_date: Annotated[str | None, Query(alias="jobRunEndDate")] = None,
    claims: dict = Security(validate_token),
) -> FHIRJSON | JSONResponse:
    """List batch jobs as FHIR ``Parameters`` resources.

    Set ``include_patient=true`` to embed each Patient resource from the
    external FHIR server as ``patientResource``. Results are paginated with
    zero-based ``page`` and ``size`` query parameters. ``batchJobStatus`` accepts
    ``pending``, ``running``, ``complete``, ``partial``, or ``error`` (including
    comma-separated values).
    """
    result = await batch_job_service.list_batch_jobs(
        include_patient=include_patient,
        page=page,
        size=size,
        questionnaire_response_status=questionnaire_response_status,
        patient_name_filter=patient_name,
        patient_gender=patient_gender,
        job_package_filter=job_package_filter,
        batch_job_status=batch_job_status,
        dob_start_date=dob_start_date,
        dob_end_date=dob_end_date,
        job_run_start_date=job_run_start_date,
        job_run_end_date=job_run_end_date,
    )
    if isinstance(result, batch_job_service.BatchJobError):
        return _service_error_response(result)
    return result


def _sse_event(bundle: FHIRJSON) -> str:
    payload = json.dumps(bundle, separators=(",", ":"), sort_keys=True)
    event_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"event: form-list\nid: {event_id}\ndata: {payload}\n\n"


def _sse_error(error: batch_job_service.ServiceFailure) -> str:
    if isinstance(error, batch_job_service.MissingConfig):
        outcome: dict[str, Any] = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "required", "diagnostics": ", ".join(error.names)}]}
    elif isinstance(error, batch_job_service.StoredErrorBundle):
        outcome = error.bundle
    else:
        outcome = {"resourceType": "OperationOutcome", "issue": [{"severity": error.severity, "code": error.code, "diagnostics": error.diagnostics}]}
    payload = json.dumps(outcome, separators=(",", ":"), sort_keys=True)
    return f"event: error\ndata: {payload}\n\n"


async def _stream_batch_jobs(
    request: _DisconnectAwareRequest,
    initial: FHIRJSON,
    list_kwargs: dict[str, Any],
):
    previous = json.dumps(initial, separators=(",", ":"), sort_keys=True)
    yield _sse_event(initial)
    while not await request.is_disconnected():
        await asyncio.sleep(settings.batch_job_stream_poll_interval_seconds)
        if await request.is_disconnected():
            break
        result = await batch_job_service.list_batch_jobs(**list_kwargs)
        if isinstance(result, batch_job_service.ServiceFailure):
            yield _sse_error(result)
            continue
        serialized = json.dumps(result, separators=(",", ":"), sort_keys=True)
        if serialized != previous:
            previous = serialized
            yield _sse_event(result)
        else:
            yield ": keepalive\n\n"


@router.get(
    "/batchjob/stream",
    summary="Stream Batch Jobs",
    response_model=None,
    responses=operation_outcome_responses(400, 503),
)
async def stream_batch_jobs(
    request: Request,
    page: Annotated[int, Query(ge=0, description="Zero-based page number.")] = 0,
    size: Annotated[int, Query(ge=1, le=100, description="Number of batch jobs per update (maximum 100).")] = 10,
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
) -> StreamingResponse | JSONResponse:
    if request.query_params.get("include_patient", "").casefold() in {"1", "true", "yes", "on"}:
        return operation_outcome_response(400, "not-supported", "The batch-job stream does not support include_patient=true.")

    list_kwargs = {
        "include_patient": False,
        "page": page,
        "size": size,
        "questionnaire_response_status": questionnaire_response_status,
        "patient_name_filter": patient_name,
        "patient_gender": patient_gender,
        "job_package_filter": job_package_filter,
        "batch_job_status": batch_job_status,
        "dob_start_date": dob_start_date,
        "dob_end_date": dob_end_date,
        "job_run_start_date": job_run_start_date,
        "job_run_end_date": job_run_end_date,
    }
    result = await batch_job_service.list_batch_jobs(**list_kwargs)
    if isinstance(result, batch_job_service.ServiceFailure):
        return _service_error_response(result)
    return StreamingResponse(
        _stream_batch_jobs(request, result, list_kwargs),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/batchjob/{batch_id}", summary="Get Batch Job Results", response_model=BundleResource, responses=operation_outcome_responses(202, 404, 500))
async def get_batch_job_results(batch_id: str, claims: dict = Security(validate_token)) -> FHIRJSON | JSONResponse:
    """Get the latest stored FHIR result Bundle for a batch job.

    Running jobs return HTTP 200 once a preliminary Bundle has been persisted;
    that Bundle contains the results of tasks completed so far. A terminal
    ``partial`` job returns its stored Bundle in the same way as ``complete``.
    Pending or running jobs without a stored snapshot return HTTP 202.
    """
    result = await batch_job_service.get_batch_job_results(batch_id)
    if isinstance(result, batch_job_service.ServiceFailure):
        return _service_error_response(result)
    return result


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
    the result Bundle. Use ``GET /batchjob/{id}`` to retrieve the latest stored
    partial or final Bundle.
    """
    result = await batch_job_service.get_batch_job_status(batch_id, include_patient)
    if isinstance(result, batch_job_service.BatchJobError):
        return _service_error_response(result)
    return result


@router.delete("/batchjob/{batch_id}", summary="Delete Batch Job")
async def delete_batch_job(batch_id: str, claims: None = Security(require_admin)):
    """Delete a batch job and its child jobs. Requires 'admin' scope."""
    return await batch_job_service.delete_batch_job(batch_id)
