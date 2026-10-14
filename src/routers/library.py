"""HTTP adapter for CQL Library search and CRUD operations.

This router binds request parameters, selects auth, calls `src.services.fhir_proxy`
for HAPI FHIR transport, and maps upstream data to Library, Bundle, or
OperationOutcome responses. It contains no CQL execution, persistence, or
transport implementation; those responsibilities belong to `src.services` and
`src.util`.
"""

from fastapi import APIRouter, Body, Security
from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, LibraryResource, OperationOutcome
from src.services.errorhandler import FHIR_PROXY_ERROR_RESPONSES, config_error_for, fhir_result_response
from src.services.fhir_proxy import fhir_delete_with_status, fhir_get_with_status, fhir_post_with_status, fhir_put_with_status
from src.util.auth import require_admin, validate_token
from src.util.settings import settings

router = APIRouter(tags=["Libraries"])

_LIBRARY_EXAMPLE = {
    "resourceType": "Library",
    "id": "SyphilisHistory",
    "name": "SyphilisHistory",
    "version": "1.0.0",
    "status": "active",
    "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/library-type", "code": "logic-library"}]},
}

LibraryResult = LibraryResource | OperationOutcome


@router.get("/library", summary="Search Libraries", response_model=BundleResource | OperationOutcome, responses=FHIR_PROXY_ERROR_RESPONSES)
async def search_libraries(
    name: str | None = None,
    claims: dict = Security(validate_token),
) -> JSONResponse | BundleResource | OperationOutcome:
    """Search CQL Library resources on HAPI FHIR.

    When ``name`` is supplied, the query is forwarded as a standard FHIR search
    parameter and the raw search Bundle is returned.
    """
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    params = {}
    if name:
        params["name"] = name
    result = await fhir_get_with_status("Library", params=params)
    return fhir_result_response(result, BundleResource)


@router.get("/library/{resource_id}", summary="Get Library", response_model=LibraryResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def get_library(
    resource_id: str,
    claims: dict = Security(validate_token),
) -> JSONResponse | LibraryResult:
    """Get a specific Library resource by ID."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_get_with_status("Library", resource_id)
    return fhir_result_response(result, LibraryResource)


@router.post("/library", summary="Create Library", response_model=LibraryResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def create_library(
    body: dict = Body(..., openapi_examples={"library": {"summary": "FHIR Library", "value": _LIBRARY_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | LibraryResult:
    """Create a new Library resource on HAPI FHIR."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_post_with_status("Library", body)
    return fhir_result_response(result, LibraryResource)


@router.put("/library/{resource_id}", summary="Update Library", response_model=LibraryResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def update_library(
    resource_id: str,
    body: dict = Body(..., openapi_examples={"library": {"summary": "FHIR Library", "value": _LIBRARY_EXAMPLE}}),
    claims: dict = Security(validate_token),
) -> JSONResponse | LibraryResult:
    """Update a Library resource."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_put_with_status("Library", resource_id, body)
    return fhir_result_response(result, LibraryResource)


@router.delete("/library/{resource_id}", summary="Delete Library", response_model=OperationOutcome, responses=FHIR_PROXY_ERROR_RESPONSES)
async def delete_library(
    resource_id: str,
    _: None = Security(require_admin),
) -> JSONResponse | OperationOutcome:
    """Delete a Library resource. Requires 'admin' scope."""
    if err := config_error_for(settings.config_errors, "HAPI_FHIR_CQL_EXECUTION_URL"):
        return err
    result = await fhir_delete_with_status("Library", resource_id)
    return fhir_result_response(result, OperationOutcome)
