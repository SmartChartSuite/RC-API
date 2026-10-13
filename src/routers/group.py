"""CRUD /group — FHIR Group resources proxied to HAPI FHIR.

GET /group implicitly includes all Patient resources referenced as members,
mirroring v0's search_group() logic.
"""

import httpx
from fastapi import APIRouter, Body, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import BundleResource, GroupResource, OperationOutcome, PatientResource
from src.services.errorhandler import config_error_for, model_or_operation_outcome, operation_outcome_responses
from src.services.fhir_proxy import fhir_delete, fhir_get, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.outbound_url import is_same_origin, resolve_http_reference
from src.util.settings import (
    config_errors,
    external_fhir_server_auth,
    external_fhir_server_url,
    require_setting,
)

router = APIRouter(tags=["Groups"])

_GROUP_EXAMPLE = {
    "resourceType": "Group",
    "id": "setnet-follow-up-cohort",
    "name": "SETNET Follow Up Cohort",
    "type": "person",
    "actual": True,
    "member": [{"entity": {"reference": "Patient/627902"}}],
}

GroupSearchResult = GroupResource | PatientResource
GroupWriteResult = GroupResource | OperationOutcome


def _patient_headers() -> dict:
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    return headers


@router.get("/group", summary="Search Groups", response_model=list[GroupSearchResult], responses=operation_outcome_responses(503))
async def search_groups(name: str | None = None, claims: dict = Security(validate_token)) -> JSONResponse | list[GroupSearchResult]:
    """Search Groups on HAPI FHIR and fetch referenced Patient resources.

    Returns a flat list such as ``[Group, Patient, Patient, ..., Group, ...]``
    mirroring the legacy implicit-include behavior used by this API.
    """
    if err := config_error_for(config_errors, "HAPI_FHIR_CQL_EXECUTION_URL", "EXTERNAL_FHIR_SERVER_URL"):
        return err

    # Fetch groups from HAPI FHIR using the shared proxy transport.
    external_base = require_setting(external_fhir_server_url, "EXTERNAL_FHIR_SERVER_URL")
    params = {}
    if name:
        params["name"] = name

    try:
        bundle = await fhir_get("Group", params=params)
    except httpx.RequestError as exc:
        logger.error(f"Failed to search Groups: {exc}")
        return []

    bundle_response = model_or_operation_outcome(bundle, BundleResource)
    if isinstance(bundle_response, OperationOutcome):
        logger.error("Group search returned an OperationOutcome")
        return []

    entries = bundle_response.entry or []

    result: list[GroupSearchResult] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for entry in entries:
            group = entry.resource
            if group.get("resourceType") != "Group":
                continue
            result.append(GroupResource.model_validate(group))

            # Fetch each member Patient from external FHIR server
            for member in group.get("member", []):
                patient_ref: str = member.get("entity", {}).get("reference", "")
                if not patient_ref:
                    continue
                patient_url = resolve_http_reference(external_base, patient_ref)
                if patient_url is None or not is_same_origin(patient_url, external_base):
                    logger.warning(f"Skipping unsafe cross-origin Patient reference: {patient_ref}")
                    continue
                try:
                    patient_resp = await client.get(patient_url, headers=_patient_headers())
                    logger.info(f"FHIR GET {patient_url} → {patient_resp.status_code}")
                    if patient_resp.is_success:
                        patient = patient_resp.json()
                        if patient.get("resourceType") == "Patient":
                            result.append(PatientResource.model_validate(patient))
                    else:
                        logger.warning(f"Could not fetch {patient_ref}: {patient_resp.status_code}")
                except httpx.RequestError as exc:
                    logger.warning(f"Request error fetching {patient_ref}: {exc}")

    return result


@router.get("/group/{resource_id}", summary="Get Group", response_model=GroupResource | OperationOutcome, responses=operation_outcome_responses(503))
async def get_group(resource_id: str, claims: dict = Security(validate_token)) -> JSONResponse | GroupResource | OperationOutcome:
    """Get a specific Group resource by ID from HAPI FHIR."""
    if err := config_error_for(config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    data = await fhir_get("Group", resource_id)
    return model_or_operation_outcome(data, GroupResource)


@router.post("/group", summary="Create Group", response_model=GroupWriteResult, responses=operation_outcome_responses(503))
async def create_group(
    body: dict = Body(..., openapi_examples={"group": {"summary": "FHIR Group", "value": _GROUP_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | GroupWriteResult:
    """Create a new Group resource on HAPI FHIR."""
    if err := config_error_for(config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    data = await fhir_post("Group", body)
    return model_or_operation_outcome(data, GroupResource)


@router.put("/group/{resource_id}", summary="Update Group", response_model=GroupWriteResult, responses=operation_outcome_responses(503))
async def update_group(
    resource_id: str,
    body: dict = Body(..., openapi_examples={"group": {"summary": "FHIR Group", "value": _GROUP_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | GroupWriteResult:
    """Update an existing Group resource."""
    if err := config_error_for(config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    data = await fhir_put("Group", resource_id, body)
    return model_or_operation_outcome(data, GroupResource)


@router.delete("/group/{resource_id}", summary="Delete Group", response_model=OperationOutcome, responses=operation_outcome_responses(503))
async def delete_group(resource_id: str, claims: None = Security(require_admin)) -> JSONResponse | OperationOutcome:
    """Delete a Group resource. Requires 'admin' scope."""
    if err := config_error_for(config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    return OperationOutcome.model_validate(await fhir_delete("Group", resource_id))
