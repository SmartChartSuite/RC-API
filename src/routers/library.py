"""CRUD /library — FHIR Library resources proxied to HAPI FHIR."""

from fastapi import APIRouter, Security

from src.services.errorhandler import config_error_response
from src.services.fhir_proxy import fhir_delete, fhir_get, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.settings import config_errors

router = APIRouter(tags=["Libraries"])


def _check_config():
    if "HAPI_FHIR_CQL_EXECUTION_URL" in config_errors:
        return config_error_response(["HAPI_FHIR_CQL_EXECUTION_URL"])
    return None


@router.get("/library")
async def search_libraries(
    name: str | None = None,
    claims: dict = Security(validate_token),
):
    """Search CQL Library resources on HAPI FHIR."""
    if err := _check_config():
        return err
    params = {}
    if name:
        params["name"] = name
    return await fhir_get("Library", params=params)


@router.get("/library/{resource_id}")
async def get_library(
    resource_id: str,
    claims: dict = Security(validate_token),
):
    """Get a specific Library resource by ID."""
    if err := _check_config():
        return err
    return await fhir_get("Library", resource_id)


@router.post("/library")
async def create_library(
    body: dict,
    claims: dict = Security(validate_token),
):
    """Create a new Library resource on HAPI FHIR."""
    if err := _check_config():
        return err
    return await fhir_post("Library", body)


@router.put("/library/{resource_id}")
async def update_library(
    resource_id: str,
    body: dict,
    claims: dict = Security(validate_token),
):
    """Update a Library resource."""
    if err := _check_config():
        return err
    return await fhir_put("Library", resource_id, body)


@router.delete("/library/{resource_id}")
async def delete_library(
    resource_id: str,
    _: None = Security(require_admin),
):
    """Delete a Library resource. Requires 'admin' scope."""
    if err := _check_config():
        return err
    return await fhir_delete("Library", resource_id)
