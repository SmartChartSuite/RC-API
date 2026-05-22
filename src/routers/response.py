"""CRUD /response — job package questionnaire responses stored in the DB.

Patient-linked data is never sent to HAPI FHIR — always stored in the local DB.
"""

import uuid

from fastapi import APIRouter, Response, Security
from fastapi.responses import JSONResponse

from src.models.fhir import OperationOutcome, OperationOutcomeIssue, ParametersParameter
from src.models.response import ResponseCreatedResponse, ResponseRecord, ResponseRequest
from src.services.errorhandler import operation_outcome_response, operation_outcome_responses
from src.services.job_state import (
    create_response,
    delete_response_record,
    get_response,
    get_responses,
    update_response_body,
)
from src.util.auth import require_admin, validate_token

router = APIRouter(tags=["Responses"])


def _to_response_record(record) -> ResponseRecord:
    return ResponseRecord(
        responseId=record.response_id,
        batchJobId=record.batch_job_id,
        jobPackage=record.job_package,
        patientId=record.patient_id,
        userId=record.user_id,
        response=record.response,
        createdAt=record.created_at.isoformat() if record.created_at else None,
        updatedAt=record.updated_at.isoformat() if record.updated_at else None,
    )


@router.get("/response", response_model=list[ResponseRecord])
async def search_responses(
    batch_job_id: str | None = None,
    job_package: str | None = None,
    claims: dict = Security(validate_token),
) -> list[ResponseRecord]:
    """List questionnaire responses, optionally filtered by batch_job_id or job_package.
    Results are restricted to the calling user."""
    user_id = claims.get("sub", "unknown")
    records = get_responses(batch_job_id=batch_job_id, job_package=job_package, user_id=user_id)
    return [_to_response_record(record) for record in records]


@router.get("/response/{response_id}", response_model=ResponseRecord, responses=operation_outcome_responses(404))
async def get_response_record(
    response_id: str,
    claims: dict = Security(validate_token),
) -> ResponseRecord | JSONResponse:
    """Get a specific questionnaire response by ID."""
    record = get_response(response_id)
    if not record:
        return operation_outcome_response(404, "not-found", f"Response {response_id} was not found.")
    return _to_response_record(record)


@router.post("/response", response_model=ResponseCreatedResponse, status_code=201, responses=operation_outcome_responses(400, 500))
async def create_response_record(
    body: ResponseRequest,
    response: Response,
    claims: dict = Security(validate_token),
) -> ResponseCreatedResponse | JSONResponse:
    """Save a questionnaire response tied to a batch job.

    Required Parameters: batchJobId, jobPackage, patientId, response (resource).
    """
    batch_job_id = body.get_param("batchJobId")
    job_package = body.get_param("jobPackage")
    patient_id = body.get_param("patientId")
    response_resource = body.get_resource_param("response")
    user_id = claims.get("sub", "unknown")

    missing = [
        n
        for n, v in [
            ("batchJobId", batch_job_id),
            ("jobPackage", job_package),
            ("patientId", patient_id),
            ("response", response_resource),
        ]
        if not v
    ]
    if missing:
        return operation_outcome_response(400, "required", f"Missing required parameter(s): {missing}")

    assert batch_job_id
    assert job_package
    assert patient_id
    assert response_resource

    response_id = str(uuid.uuid4())
    created = create_response(response_id, batch_job_id, job_package, patient_id, user_id, response_resource)
    if not created:
        return operation_outcome_response(500, "processing", "Failed to save response. See logs for details.")
    response.headers["Location"] = f"/response/{response_id}"
    return ResponseCreatedResponse(
        parameter=[ParametersParameter(name="responseId", valueString=response_id)],
    )


@router.put("/response/{response_id}", response_model=OperationOutcome, responses=operation_outcome_responses(404))
async def update_response_record(
    response_id: str,
    body: dict,
    claims: dict = Security(validate_token),
) -> OperationOutcome | JSONResponse:
    """Update an existing questionnaire response. Body should be a FHIR QuestionnaireResponse."""
    updated = update_response_body(response_id, body)
    if not updated:
        return operation_outcome_response(404, "not-found", f"Response {response_id} was not found.")
    return OperationOutcome(issue=[OperationOutcomeIssue(severity="information", code="informational", diagnostics=f"Response {response_id} updated.")])


@router.delete("/response/{response_id}")
async def delete_response(
    response_id: str,
    _: None = Security(require_admin),
):
    """Delete a questionnaire response. Requires 'admin' scope."""
    return delete_response_record(response_id)
