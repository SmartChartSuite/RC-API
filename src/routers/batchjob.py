"""POST/GET/DELETE /batchjob — batch job submission and status/results retrieval."""

import uuid
from datetime import datetime, timezone
from typing import Any, cast

import httpx
from fastapi import APIRouter, BackgroundTasks, Response, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import BundleJSON, ParametersParameter, ParametersResponse
from src.models.job_request import JobRequest
from src.models.job_response import BatchJobAcceptedResponse
from src.services.errorhandler import config_error_response, operation_outcome_response, operation_outcome_responses
from src.services.job_orchestrator import run_batch_job
from src.services.job_state import (
    BatchJobs,
    create_batch_job,
    delete_batch_job_record,
    get_all_batch_jobs,
    get_batch_job,
)
from src.util.auth import require_admin, validate_token
from src.util.settings import (
    config_errors,
    external_fhir_server_auth,
    external_fhir_server_url,
)

router = APIRouter(tags=["Batch Jobs"])


def _to_batch_job_parameters(job: BatchJobs, patient: dict[str, Any] | None = None) -> ParametersResponse:
    parameters = [
        ParametersParameter(name="batchId", valueString=job.batch_id),
        ParametersParameter(name="patientId", valueString=job.patient_id),
        ParametersParameter(name="jobPackage", valueString=job.job_package),
        ParametersParameter(name="batchJobStatus", valueString=cast(Any, job.status)),
        ParametersParameter(name="jobStartDateTime", valueDateTime=job.created_at.isoformat()),
    ]
    if job.completed_at:
        parameters.append(ParametersParameter(name="jobCompletedDateTime", valueDateTime=job.completed_at.isoformat()))
    if patient is not None:
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


@router.post("/batchjob", response_model=BatchJobAcceptedResponse, responses=operation_outcome_responses(500, 503), response_model_exclude_none=True)
async def post_batch_job(
    body: JobRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    claims: dict = Security(validate_token),
) -> BatchJobAcceptedResponse | JSONResponse:
    """Submit a job package run for a patient.

    Returns a FHIR Parameters resource with the batch job metadata (v0-compatible).
    Execution runs asynchronously in the background.
    """
    missing = _required_config()
    if missing:
        return config_error_response(missing)

    patient_id: str | None = body.get_param("patientId")
    job_package: str | None = body.get_param("jobPackage")
    job_package_version: str | None = body.get_param("jobPackageVersion")
    job_names = body.get_params("job")

    assert patient_id
    assert job_package

    batch_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    created = create_batch_job(batch_id, patient_id, job_package)
    if not created:
        return operation_outcome_response(
            500,
            "processing",
            "The batch job could not be saved to the database. See logs for details.",
        )

    background_tasks.add_task(run_batch_job, batch_id, job_id, patient_id, job_package, job_package_version, job_names)

    response.headers["Location"] = f"/batchjob/{batch_id}"
    return BatchJobAcceptedResponse(
        parameter=[
            ParametersParameter(name="batchId", valueString=batch_id),
            ParametersParameter(name="jobStartDateTime", valueDateTime=start_time),
            ParametersParameter(name="patientId", valueString=patient_id),
            ParametersParameter(name="jobPackage", valueString=job_package),
            ParametersParameter(name="status", valueString="pending"),
        ]
    )


@router.get("/batchjob", response_model=list[ParametersResponse], responses=operation_outcome_responses(503), response_model_exclude_none=True)
async def list_batch_jobs(include_patient: bool = False, claims: dict = Security(validate_token)) -> list[ParametersResponse]:
    """List all batch job runs as FHIR Parameters resources. Optionally embed the Patient resource."""
    jobs: list[BatchJobs] = get_all_batch_jobs()
    patients_by_id: dict[str, dict[str, Any] | None] = {}
    results = []
    for job in jobs:
        patient = None
        if include_patient:
            if job.patient_id not in patients_by_id:
                patients_by_id[job.patient_id] = await _fetch_patient(job.patient_id)
            patient = patients_by_id[job.patient_id]
        results.append(_to_batch_job_parameters(job, patient=patient))
    return results


@router.get("/batchjob/{batch_id}", response_model=dict[str, Any], responses=operation_outcome_responses(202, 404, 500))
async def get_batch_job_results(batch_id: str, claims: dict = Security(validate_token)) -> BundleJSON | JSONResponse:
    """Get the full FHIR result Bundle for a completed batch job.

    The Bundle is stored during background job execution — this is a pure DB read.
    Returns the stored Bundle as-is, or an OperationOutcome if not yet available.
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


@router.get("/batchjob/{batch_id}/status", response_model=ParametersResponse, responses=operation_outcome_responses(404), response_model_exclude_none=True)
async def get_batch_job_status(
    batch_id: str,
    include_patient: bool = False,
    claims: dict = Security(validate_token),
) -> ParametersResponse | JSONResponse:
    """Get the status of a batch job (lightweight polling endpoint).

    Returns a FHIR Parameters resource — no Bundle assembly. Use /batchjob/{id}/results to retrieve the full FHIR Bundle once status is 'complete'.
    """
    job = get_batch_job(batch_id)
    if not job:
        return operation_outcome_response(404, "not-found", f"Batch Job ID {batch_id} was not found.")
    patient = await _fetch_patient(job.patient_id) if include_patient else None
    return _to_batch_job_parameters(job, patient=patient)


@router.delete("/batchjob/{batch_id}")
async def delete_batch_job(batch_id: str, claims: None = Security(require_admin)):
    """Delete a batch job and its child jobs. Requires 'admin' scope."""
    return delete_batch_job_record(batch_id)
