"""CRUD /group — FHIR Group resources proxied to HAPI FHIR.

GET /group implicitly includes all Patient resources referenced as members,
mirroring v0's search_group() logic.
"""

import httpx
from fastapi import APIRouter, Body, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import BundleResource, GroupResource, OperationOutcome, PatientResource
from src.services.errorhandler import FHIR_PROXY_ERROR_RESPONSES, config_error_for, fhir_result_response, model_or_operation_outcome, operation_outcome_responses
from src.services.fhir_proxy import fhir_delete_with_status, fhir_get, fhir_get_with_status, fhir_post_with_status, fhir_put_with_status
from src.util.auth import require_admin, validate_token
from src.util.http_clients import get_http_client
from src.util.outbound_url import is_same_origin, resolve_http_reference
from src.util.settings import require_setting, secret_value, settings

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
    if auth := secret_value(settings.external_fhir_server_auth):
        headers["Authorization"] = auth
    return headers


@router.get("/group", summary="Search Groups", response_model=list[GroupSearchResult], responses=operation_outcome_responses(503))
async def search_groups(name: str | None = None, claims: dict = Security(validate_token)) -> JSONResponse | list[GroupSearchResult]:
    """Search Groups on HAPI FHIR and fetch referenced Patient resources.

    Returns a flat list such as ``[Group, Patient, Patient, ..., Group, ...]``
    mirroring the legacy implicit-include behavior used by this API.
    """
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL", "EXTERNAL_FHIR_SERVER_URL"):
        return err

    # Fetch groups from HAPI FHIR using the shared proxy transport.
    external_base = require_setting(settings.external_fhir_server_url, "EXTERNAL_FHIR_SERVER_URL")
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
    client = get_http_client("external_patient")
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


@router.get("/group/{resource_id}", summary="Get Group", response_model=GroupResource | OperationOutcome, responses=FHIR_PROXY_ERROR_RESPONSES)
async def get_group(resource_id: str, claims: dict = Security(validate_token)) -> JSONResponse | GroupResource | OperationOutcome:
    """Get a specific Group resource by ID from HAPI FHIR."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_get_with_status("Group", resource_id)
    return fhir_result_response(result, GroupResource)


@router.post("/group", summary="Create Group", response_model=GroupWriteResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def create_group(
    body: dict = Body(..., openapi_examples={"group": {"summary": "FHIR Group", "value": _GROUP_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | GroupWriteResult:
    """Create a new Group resource on HAPI FHIR."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_post_with_status("Group", body)
    return fhir_result_response(result, GroupResource)


@router.put("/group/{resource_id}", summary="Update Group", response_model=GroupWriteResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def update_group(
    resource_id: str,
    body: dict = Body(..., openapi_examples={"group": {"summary": "FHIR Group", "value": _GROUP_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | GroupWriteResult:
    """Update an existing Group resource."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_put_with_status("Group", resource_id, body)
    return fhir_result_response(result, GroupResource)


@router.delete("/group/{resource_id}", summary="Delete Group", response_model=OperationOutcome, responses=FHIR_PROXY_ERROR_RESPONSES)
async def delete_group(resource_id: str, claims: None = Security(require_admin)) -> JSONResponse | OperationOutcome:
    """Delete a Group resource. Requires 'admin' scope."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_delete_with_status("Group", resource_id)
    return fhir_result_response(result, OperationOutcome)
