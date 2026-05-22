"""CRUD /library — FHIR Library resources proxied to HAPI FHIR."""

from fastapi import APIRouter, Security
from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, LibraryResource, OperationOutcome
from src.services.errorhandler import config_error_response, operation_outcome_responses
from src.services.fhir_proxy import fhir_delete, fhir_get, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.settings import config_errors

router = APIRouter(tags=["Libraries"])

LibraryResult = LibraryResource | OperationOutcome


def _check_config():
    if "HAPI_FHIR_CQL_EXECUTION_URL" in config_errors:
        return config_error_response(["HAPI_FHIR_CQL_EXECUTION_URL"])
    return None


@router.get("/library", response_model=BundleResource | OperationOutcome, responses=operation_outcome_responses(503))
async def search_libraries(
    name: str | None = None,
    claims: dict = Security(validate_token),
) -> JSONResponse | BundleResource | OperationOutcome:
    """Search CQL Library resources on HAPI FHIR."""
    if err := _check_config():
        return err
    params = {}
    if name:
        params["name"] = name
    data = await fhir_get("Library", params=params)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return BundleResource.model_validate(data)


@router.get("/library/{resource_id}", response_model=LibraryResult, responses=operation_outcome_responses(503))
async def get_library(
    resource_id: str,
    claims: dict = Security(validate_token),
) -> JSONResponse | LibraryResult:
    """Get a specific Library resource by ID."""
    if err := _check_config():
        return err
    data = await fhir_get("Library", resource_id)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return LibraryResource.model_validate(data)


@router.post("/library", response_model=LibraryResult, responses=operation_outcome_responses(503))
async def create_library(
    body: dict,
    claims: dict = Security(validate_token),
) -> JSONResponse | LibraryResult:
    """Create a new Library resource on HAPI FHIR."""
    if err := _check_config():
        return err
    data = await fhir_post("Library", body)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return LibraryResource.model_validate(data)


@router.put("/library/{resource_id}", response_model=LibraryResult, responses=operation_outcome_responses(503))
async def update_library(
    resource_id: str,
    body: dict,
    claims: dict = Security(validate_token),
) -> JSONResponse | LibraryResult:
    """Update a Library resource."""
    if err := _check_config():
        return err
    data = await fhir_put("Library", resource_id, body)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return LibraryResource.model_validate(data)


@router.delete("/library/{resource_id}", response_model=OperationOutcome, responses=operation_outcome_responses(503))
async def delete_library(
    resource_id: str,
    _: None = Security(require_admin),
) -> JSONResponse | OperationOutcome:
    """Delete a Library resource. Requires 'admin' scope."""
    if err := _check_config():
        return err
    return OperationOutcome.model_validate(await fhir_delete("Library", resource_id))
