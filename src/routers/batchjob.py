"""POST/GET/DELETE /batchjob — batch job submission and status/results retrieval."""

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.job_request import JobRequest
from src.services.errorhandler import config_error_response, make_operation_outcome
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


@router.post("/batchjob")
async def post_batch_job(
    body: JobRequest,
    background_tasks: BackgroundTasks,
    claims: dict = Security(validate_token),
):
    """Submit a job package run for a patient.

    Returns a FHIR Parameters resource with the batch job metadata (v0-compatible).
    Execution runs asynchronously in the background.
    """
    missing = _required_config()
    if missing:
        return config_error_response(missing)

    patient_id: str = body.get_param("patientId")  # type: ignore
    job_package: str = body.get_param("jobPackage")  # type: ignore
    job_package_version: str | None = body.get_param("jobPackageVersion")

    batch_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    created = create_batch_job(batch_id, patient_id, job_package)
    if not created:
        return JSONResponse(
            make_operation_outcome(
                "processing",
                "The batch job could not be saved to the database. See logs for details.",
            ),
            status_code=500,
        )

    background_tasks.add_task(run_batch_job, batch_id, job_id, patient_id, job_package, job_package_version)

    # Return full Parameters response body — v0-compatible convention
    response_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "batchId", "valueString": batch_id},
            {"name": "jobStartDateTime", "valueDateTime": start_time},
            {"name": "patientId", "valueString": patient_id},
            {"name": "jobPackage", "valueString": job_package},
            {"name": "status", "valueString": "pending"},
        ],
    }
    return JSONResponse(
        content=response_body,
        headers={"Location": f"/batchjob/{batch_id}"},
    )


@router.get("/batchjob")
async def list_batch_jobs(
    include_patient: bool = False,
    claims: dict = Security(validate_token),
):
    """List all batch job runs. Optionally embed the Patient resource inline."""
    jobs: list[BatchJobs] = get_all_batch_jobs()
    results = []
    for job in jobs:
        item = {
            "batchId": job.batch_id,
            "patientId": job.patient_id,
            "jobPackage": job.job_package,
            "status": job.status,
            "createdAt": job.created_at.isoformat() if job.created_at else None,
            "completedAt": job.completed_at.isoformat() if job.completed_at else None,
        }
        if include_patient:
            item["patient"] = await _fetch_patient(job.patient_id)
        results.append(item)
    return results


@router.get("/batchjob/{batch_id}")
async def get_batch_job_results(
    batch_id: str,
    claims: dict = Security(validate_token),
):
    """Get the full FHIR result Bundle for a completed batch job.

    The Bundle is stored during background job execution — this is a pure DB read.
    Returns the stored Bundle as-is, or an OperationOutcome if not yet available.
    """
    job = get_batch_job(batch_id)
    if not job:
        return JSONResponse(
            make_operation_outcome("not-found", f"Batch Job ID {batch_id} was not found."),
            status_code=404,
        )
    if job.status in ("pending", "running"):
        return JSONResponse(
            make_operation_outcome(
                "informational",
                f"Batch job {batch_id} is still {job.status}. Poll GET /batchjob/{{id}} and retry when status is 'complete'.",
                severity="information",
            ),
            status_code=202,
        )
    if not job.result_bundle:
        return JSONResponse(
            make_operation_outcome(
                "transient",
                f"Batch job {batch_id} has no result bundle. The job may have encountered an error — check job status.",
            ),
            status_code=500,
        )
    return job.result_bundle


@router.get("/batchjob/{batch_id}/status")
async def get_batch_job_status(
    batch_id: str,
    include_patient: bool = False,
    claims: dict = Security(validate_token),
):
    """Get the status of a batch job (lightweight polling endpoint).

    Returns status-only JSON — no Bundle assembly. Use /batchjob/{id}/results to retrieve the full FHIR Bundle once status is 'complete'.
    """
    job = get_batch_job(batch_id)
    if not job:
        return JSONResponse(
            make_operation_outcome("not-found", f"Batch Job ID {batch_id} was not found."),
            status_code=404,
        )
    result = {
        "batchId": job.batch_id,
        "patientId": job.patient_id,
        "jobPackage": job.job_package,
        "status": job.status,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    }
    if include_patient:
        result["patient"] = await _fetch_patient(job.patient_id)
    return result


@router.delete("/batchjob/{batch_id}")
async def delete_batch_job(
    batch_id: str,
    _: None = Security(require_admin),
):
    """Delete a batch job and its child jobs. Requires 'admin' scope."""
    return delete_batch_job_record(batch_id)
