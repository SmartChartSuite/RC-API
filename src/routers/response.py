"""CRUD /response — job package questionnaire responses stored in the DB.

Patient-linked data is never sent to HAPI FHIR — always stored in the local DB.
"""

import uuid

from fastapi import APIRouter, Body, Query, Response, Security
from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, OperationOutcome, OperationOutcomeIssue, QuestionnaireResponseResource
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

_QUESTIONNAIRE_RESPONSE_EXAMPLE = {
    "resourceType": "QuestionnaireResponse",
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "questionnaire": "Questionnaire/987e6543-e21b-12d3-a456-426614174999",
    "status": "completed",
    "subject": {"reference": "Patient/patient-123"},
    "item": [{"linkId": "1.1", "answer": [{"valueString": "Yes"}]}],
}


def _normalize_questionnaire_response(response_id: str, resource: dict) -> dict:
    normalized = dict(resource)
    normalized["resourceType"] = "QuestionnaireResponse"
    normalized["id"] = response_id
    return normalized


def _extract_patient_id(resource: dict) -> str | None:
    subject = resource.get("subject")
    if not isinstance(subject, dict):
        return None
    reference = subject.get("reference")
    if not isinstance(reference, str) or not reference:
        return None
    if reference.startswith("Patient/"):
        return reference.split("/", 1)[1] or None
    return reference


def _derive_job_package(resource: dict) -> str | None:
    questionnaire = resource.get("questionnaire")
    if not isinstance(questionnaire, str) or not questionnaire:
        return None
    return questionnaire


def _to_response_bundle(records: list) -> BundleResource:
    entries = []
    for record in records:
        resource = _normalize_questionnaire_response(record.response_id, record.response)
        entries.append({"fullUrl": f"QuestionnaireResponse/{record.response_id}", "resource": resource})

    return BundleResource(resourceType="Bundle", type="searchset", total=len(entries), entry=entries)


@router.get("/response", summary="List Responses", response_model=BundleResource)
async def search_responses(
    batch_job_id: str | None = None,
    job_package: str | None = None,
    claims: dict = Security(validate_token),
) -> BundleResource:
    """List stored QuestionnaireResponses.

    Results can be filtered by ``batch_job_id`` and/or ``job_package`` and are
    returned from the local application database rather than proxied to HAPI FHIR.
    """
    records = get_responses(batch_job_id=batch_job_id, job_package=job_package)
    return _to_response_bundle(records)


@router.get(
    "/response/{response_id}",
    summary="Get Response",
    response_model=QuestionnaireResponseResource,
    responses=operation_outcome_responses(404),
)
async def get_response_record(
    response_id: str,
    claims: dict = Security(validate_token),
) -> QuestionnaireResponseResource | JSONResponse:
    """Get a stored QuestionnaireResponse resource by local response ID."""
    record = get_response(response_id)
    if not record:
        return operation_outcome_response(404, "not-found", f"Response {response_id} was not found.")
    return QuestionnaireResponseResource.model_validate(_normalize_questionnaire_response(record.response_id, record.response))


@router.post(
    "/response",
    summary="Create Response",
    response_model=QuestionnaireResponseResource,
    status_code=201,
    responses=operation_outcome_responses(400, 500),
)
async def create_response_record(
    body: QuestionnaireResponseResource = Body(
        ...,
        openapi_examples={"questionnaireResponse": {"summary": "FHIR QuestionnaireResponse", "value": _QUESTIONNAIRE_RESPONSE_EXAMPLE}},
    ),
    batch_job_id: str = Query(..., description="Local batch job ID associated with this QuestionnaireResponse."),
    response: Response = None,  # type: ignore[assignment]
    claims: dict = Security(validate_token),
) -> QuestionnaireResponseResource | JSONResponse:
    """Create a stored QuestionnaireResponse.

    The request body is the FHIR ``QuestionnaireResponse`` resource to persist.
    The related local ``batch_job_id`` is supplied as a query parameter.
    ``patient_id`` is derived from ``subject.reference`` and ``job_package`` is
    derived from ``questionnaire``.
    """
    last_updated_by = claims.get("sub", "unknown")

    response_resource = body.model_dump(mode="json", exclude_none=True)
    patient_id = _extract_patient_id(response_resource)
    job_package = _derive_job_package(response_resource)

    missing = [n for n, v in [("batch_job_id", batch_job_id), ("questionnaire", job_package), ("subject.reference", patient_id)] if not v]
    if missing:
        return operation_outcome_response(400, "required", f"Missing required parameter(s): {missing}")

    assert job_package
    assert patient_id

    response_id = str(uuid.uuid4())
    normalized_response = _normalize_questionnaire_response(response_id, response_resource)
    created = create_response(
        response_id,
        batch_job_id,
        job_package,
        patient_id,
        last_updated_by,
        normalized_response,
    )
    if not created:
        return operation_outcome_response(500, "processing", "Failed to save response. See logs for details.")
    response.headers["Location"] = f"/response/{response_id}"
    return QuestionnaireResponseResource.model_validate(normalized_response)


@router.put("/response/{response_id}", summary="Update Response", response_model=OperationOutcome, responses=operation_outcome_responses(404))
async def update_response_record(
    response_id: str,
    body: dict = Body(..., openapi_examples={"questionnaireResponse": {"summary": "FHIR QuestionnaireResponse", "value": _QUESTIONNAIRE_RESPONSE_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> OperationOutcome | JSONResponse:
    """Update an existing stored QuestionnaireResponse.

    The request body should be the full FHIR ``QuestionnaireResponse`` resource
    to persist for the given local ``response_id``.
    """
    updated = update_response_body(response_id, _normalize_questionnaire_response(response_id, body), claims.get("sub", "unknown"))
    if not updated:
        return operation_outcome_response(404, "not-found", f"Response {response_id} was not found.")
    return OperationOutcome(issue=[OperationOutcomeIssue(severity="information", code="informational", diagnostics=f"Response {response_id} updated.")])


@router.delete("/response/{response_id}", summary="Delete Response")
async def delete_response(
    response_id: str,
    _: None = Security(require_admin),
):
    """Delete a questionnaire response. Requires 'admin' scope."""
    return delete_response_record(response_id)
