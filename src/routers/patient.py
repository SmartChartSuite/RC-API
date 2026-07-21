"""GET /patient — Patient reads and search proxied to the external FHIR server."""

from collections.abc import Sequence
from typing import Any, cast

import httpx
from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import BundleResource, OperationOutcome, PatientResource
from src.services.errorhandler import config_error_response, make_operation_outcome, operation_outcome_responses
from src.util.auth import validate_token
from src.util.settings import config_errors, external_fhir_server_auth, external_fhir_server_url

router = APIRouter(tags=["Patients"])

PatientSearchResult = BundleResource | OperationOutcome
PatientReadResult = PatientResource | OperationOutcome


def _check_config():
    if "EXTERNAL_FHIR_SERVER_URL" in config_errors:
        return config_error_response(["EXTERNAL_FHIR_SERVER_URL"])
    return None


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    return headers


def _base_url(resource_id: str | None = None) -> str:
    assert external_fhir_server_url
    base = f"{external_fhir_server_url.rstrip('/')}/Patient"
    if resource_id:
        base += f"/{resource_id}"
    return base


async def _external_patient_get(resource_id: str | None = None, params: Sequence[tuple[str, str]] | None = None) -> dict:
    url = _base_url(resource_id)
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.get(url, headers=_headers(), params=cast(Any, list(params) if params is not None else None))
            logger.info(f"FHIR GET {url} → {resp.status_code}")
        except httpx.RequestError as exc:
            logger.error(f"Patient GET {url} failed: {exc}")
            return make_operation_outcome("transient", f"External FHIR server request failed for GET {url}")

    try:
        data = resp.json()
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    if resp.is_success:
        return data

    if data.get("resourceType") == "OperationOutcome":
        return data

    logger.error(f"Patient GET {url} returned {resp.status_code}: {resp.text}")
    return make_operation_outcome("transient", f"External FHIR server returned HTTP {resp.status_code} for GET {url}")


@router.get("/patient", summary="Search Patients", response_model=PatientSearchResult, responses=operation_outcome_responses(503))
async def search_patients(request: Request, claims: dict = Security(validate_token)) -> JSONResponse | PatientSearchResult:
    """Search Patient resources on the external FHIR server.

    All query parameters are forwarded as-is to the upstream FHIR ``Patient``
    search endpoint and the resulting Bundle is returned.
    """
    if err := _check_config():
        return err
    data = await _external_patient_get(params=list(request.query_params.multi_items()))
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return BundleResource.model_validate(data)


@router.get("/patient/{resource_id}", summary="Get Patient", response_model=PatientReadResult, responses=operation_outcome_responses(503))
async def get_patient(resource_id: str, claims: dict = Security(validate_token)) -> JSONResponse | PatientReadResult:
    """Get a specific Patient resource by ID from the external FHIR server."""
    if err := _check_config():
        return err
    data = await _external_patient_get(resource_id=resource_id)
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return PatientResource.model_validate(data)
