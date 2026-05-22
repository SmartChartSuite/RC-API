"""CRUD /group — FHIR Group resources proxied to HAPI FHIR.

GET /group implicitly includes all Patient resources referenced as members,
mirroring v0's search_group() logic.
"""

import httpx
from fastapi import APIRouter, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import GroupResource, OperationOutcome, PatientResource
from src.services.errorhandler import config_error_response, operation_outcome_responses
from src.services.fhir_proxy import fhir_delete, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.settings import (
    config_errors,
    external_fhir_server_auth,
    external_fhir_server_url,
    hapi_fhir_cql_execution_url,
)

router = APIRouter(tags=["Groups"])

GroupSearchResult = GroupResource | PatientResource
GroupWriteResult = GroupResource | OperationOutcome


def _check_config():
    if "HAPI_FHIR_CQL_EXECUTION_URL" in config_errors:
        return config_error_response(["HAPI_FHIR_CQL_EXECUTION_URL"])
    return None


def _patient_headers() -> dict:
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    return headers


@router.get("/group", response_model=list[GroupSearchResult], responses=operation_outcome_responses(503))
async def search_groups(name: str | None = None, claims: dict = Security(validate_token)) -> JSONResponse | list[GroupSearchResult]:
    """Search Groups on HAPI FHIR and fetch referenced Patient resources.

    Returns a flat list: [Group, Patient, Patient, ..., Group, ...] mirroring v0's search_group() implicit include behaviour.
    """
    if err := _check_config():
        return err

    # Fetch groups from HAPI FHIR
    assert hapi_fhir_cql_execution_url
    assert external_fhir_server_url
    url = f"{hapi_fhir_cql_execution_url.rstrip('/')}/Group"
    params = {}
    if name:
        params["name"] = name

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.get(
                url,
                params=params,
                headers={"Accept": "application/fhir+json"},
            )
        except httpx.RequestError as exc:
            logger.error(f"Failed to search Groups: {exc}")
            return []

    if not resp.is_success:
        logger.error(f"Group search returned {resp.status_code}")
        return []

    bundle = resp.json()
    entries = bundle.get("entry", [])

    result: list[GroupSearchResult] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for entry in entries:
            group = entry.get("resource", {})
            if group.get("resourceType") != "Group":
                continue
            result.append(GroupResource.model_validate(group))

            # Fetch each member Patient from external FHIR server
            for member in group.get("member", []):
                patient_ref: str = member.get("entity", {}).get("reference", "")
                if not patient_ref:
                    continue
                # Build absolute URL — if reference is already absolute, use as-is
                if patient_ref.startswith("http"):
                    patient_url = patient_ref
                else:
                    patient_url = f"{external_fhir_server_url.rstrip('/')}/{patient_ref.lstrip('/')}"
                try:
                    patient_resp = await client.get(patient_url, headers=_patient_headers())
                    if patient_resp.is_success:
                        patient = patient_resp.json()
                        if patient.get("resourceType") == "Patient":
                            result.append(PatientResource.model_validate(patient))
                    else:
                        logger.warning(f"Could not fetch {patient_ref}: {patient_resp.status_code}")
                except httpx.RequestError as exc:
                    logger.warning(f"Request error fetching {patient_ref}: {exc}")

    return result


@router.get("/group/{resource_id}", response_model=GroupResource | OperationOutcome, responses=operation_outcome_responses(503))
async def get_group(resource_id: str, claims: dict = Security(validate_token)) -> JSONResponse | GroupResource | OperationOutcome:
    """Get a specific Group resource by ID from HAPI FHIR."""
    if err := _check_config():
        return err
    assert hapi_fhir_cql_execution_url
    async with httpx.AsyncClient(timeout=60) as client:
        url = f"{hapi_fhir_cql_execution_url.rstrip('/')}/Group/{resource_id}"
        resp = await client.get(url, headers={"Accept": "application/fhir+json"})
    data = resp.json()
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return GroupResource.model_validate(data)


@router.post("/group", response_model=GroupWriteResult, responses=operation_outcome_responses(503))
async def create_group(body: dict, claims: dict = Security(validate_token)) -> JSONResponse | GroupWriteResult:
    """Create a new Group resource on HAPI FHIR."""
    if err := _check_config():
        return err
    data = await fhir_post("Group", body)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return GroupResource.model_validate(data)


@router.put("/group/{resource_id}", response_model=GroupWriteResult, responses=operation_outcome_responses(503))
async def update_group(resource_id: str, body: dict, claims: dict = Security(validate_token)) -> JSONResponse | GroupWriteResult:
    """Update an existing Group resource."""
    if err := _check_config():
        return err
    data = await fhir_put("Group", resource_id, body)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return GroupResource.model_validate(data)


@router.delete("/group/{resource_id}", response_model=OperationOutcome, responses=operation_outcome_responses(503))
async def delete_group(resource_id: str, claims: None = Security(require_admin)) -> JSONResponse | OperationOutcome:
    """Delete a Group resource. Requires 'admin' scope."""
    if err := _check_config():
        return err
    return OperationOutcome.model_validate(await fhir_delete("Group", resource_id))
