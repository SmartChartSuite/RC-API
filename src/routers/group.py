"""CRUD /group — FHIR Group resources proxied to HAPI FHIR.

GET /group implicitly includes all Patient resources referenced as members,
mirroring v0's search_group() logic.
"""

import httpx
from fastapi import APIRouter, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.services.errorhandler import config_error_response
from src.services.fhir_proxy import fhir_delete, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.settings import (
    config_errors,
    external_fhir_server_auth,
    external_fhir_server_url,
    hapi_fhir_cql_execution_url,
)

router = APIRouter(tags=["Groups"])


def _check_config():
    if "HAPI_FHIR_CQL_EXECUTION_URL" in config_errors:
        return config_error_response(["HAPI_FHIR_CQL_EXECUTION_URL"])
    return None


def _patient_headers() -> dict:
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    return headers


@router.get("/group", response_model=dict)
async def search_groups(name: str | None = None, claims: dict = Security(validate_token)) -> JSONResponse | list | list[dict]:
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

    result: list[dict] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for entry in entries:
            group = entry.get("resource", {})
            if group.get("resourceType") != "Group":
                continue
            result.append(group)

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
                        result.append(patient_resp.json())
                    else:
                        logger.warning(f"Could not fetch {patient_ref}: {patient_resp.status_code}")
                except httpx.RequestError as exc:
                    logger.warning(f"Request error fetching {patient_ref}: {exc}")

    return result


@router.get("/group/{resource_id}", response_model=dict)
async def get_group(resource_id: str, claims: dict = Security(validate_token)) -> JSONResponse | dict:
    """Get a specific Group resource by ID from HAPI FHIR."""
    if err := _check_config():
        return err
    assert hapi_fhir_cql_execution_url
    async with httpx.AsyncClient(timeout=60) as client:
        url = f"{hapi_fhir_cql_execution_url.rstrip('/')}/Group/{resource_id}"
        resp = await client.get(url, headers={"Accept": "application/fhir+json"})
    return resp.json()


@router.post("/group", response_model=dict)
async def create_group(body: dict, claims: dict = Security(validate_token)) -> JSONResponse | dict:
    """Create a new Group resource on HAPI FHIR."""
    if err := _check_config():
        return err
    return await fhir_post("Group", body)


@router.put("/group/{resource_id}", response_model=dict)
async def update_group(resource_id: str, body: dict, claims: dict = Security(validate_token)) -> JSONResponse | dict:
    """Update an existing Group resource."""
    if err := _check_config():
        return err
    return await fhir_put("Group", resource_id, body)


@router.delete("/group/{resource_id}", response_model=dict)
async def delete_group(resource_id: str, claims: None = Security(require_admin)) -> JSONResponse | dict:
    """Delete a Group resource. Requires 'admin' scope."""
    if err := _check_config():
        return err
    return await fhir_delete("Group", resource_id)
