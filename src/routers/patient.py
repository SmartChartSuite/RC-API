"""GET /patient — Patient reads and search proxied to the external FHIR server."""

from collections.abc import Sequence
from typing import Any, cast

import httpx
from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from loguru import logger

from src.models.fhir import BundleResource, FHIRProxyResult, OperationOutcome, OperationOutcomeJSON, PatientResource
from src.services.errorhandler import FHIR_PROXY_ERROR_RESPONSES, config_error_response, fhir_result_response, make_operation_outcome, operation_outcome_response
from src.services.fhir_proxy import FHIRProxyResponse
from src.util.auth import validate_token
from src.util.http_clients import get_http_client
from src.util.outbound_url import is_valid_fhir_id, path_segment
from src.util.settings import require_setting, secret_value, settings

router = APIRouter(tags=["Patients"])

PatientSearchResult = BundleResource | OperationOutcome
PatientReadResult = PatientResource | OperationOutcome


def _check_config():
    if "EXTERNAL_FHIR_SERVER_URL" in settings.config_errors:
        return config_error_response(["EXTERNAL_FHIR_SERVER_URL"])
    return None


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/fhir+json"}
    if auth := secret_value(settings.external_fhir_server_auth):
        headers["Authorization"] = auth
    return headers


def _base_url(resource_id: str | None = None) -> str:
    base = f"{require_setting(settings.external_fhir_server_url, 'EXTERNAL_FHIR_SERVER_URL').rstrip('/')}/Patient"
    if resource_id:
        base += f"/{path_segment(resource_id)}"
    return base


async def _external_patient_get(resource_id: str | None = None, params: Sequence[tuple[str, str]] | None = None) -> FHIRProxyResponse:
    url = _base_url(resource_id)
    client = get_http_client("external_patient")
    try:
        resp = await client.get(url, headers=_headers(), params=cast(Any, list(params) if params is not None else None))
        logger.info(f"FHIR GET Patient → {resp.status_code}")
    except httpx.TimeoutException as exc:
        logger.error(f"Patient GET failed: {type(exc).__name__}")
        return FHIRProxyResponse(
            data=cast(OperationOutcomeJSON, make_operation_outcome("transient", "External FHIR server request failed for GET Patient")),
            status_code=504,
            error_type="timeout",
        )
    except httpx.RequestError as exc:
        logger.error(f"Patient GET failed: {type(exc).__name__}")
        return FHIRProxyResponse(
            data=cast(OperationOutcomeJSON, make_operation_outcome("transient", "External FHIR server request failed for GET Patient")),
            status_code=502,
            error_type="transport",
        )

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    if resp.is_success:
        return FHIRProxyResponse(data=cast(FHIRProxyResult, data), status_code=resp.status_code)

    if data.get("resourceType") == "OperationOutcome":
        return FHIRProxyResponse(data=cast(OperationOutcomeJSON, data), status_code=resp.status_code)

    logger.error(f"Patient GET returned HTTP {resp.status_code}")
    logger.debug(f"Patient GET error body (truncated): {resp.text[:500]}")
    return FHIRProxyResponse(
        data=cast(OperationOutcomeJSON, make_operation_outcome("transient", f"External FHIR server returned HTTP {resp.status_code} for GET Patient")),
        status_code=resp.status_code,
    )


@router.get("/patient", summary="Search Patients", response_model=PatientSearchResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def search_patients(request: Request, claims: dict = Security(validate_token)) -> JSONResponse | PatientSearchResult:
    """Search Patient resources on the external FHIR server.

    All query parameters are forwarded as-is to the upstream FHIR ``Patient``
    search endpoint and the resulting Bundle is returned.
    """
    if err := _check_config():
        return err
    result = await _external_patient_get(params=list(request.query_params.multi_items()))
    return fhir_result_response(result, BundleResource)


@router.get("/patient/{resource_id}", summary="Get Patient", response_model=PatientReadResult, responses=FHIR_PROXY_ERROR_RESPONSES)
async def get_patient(resource_id: str, claims: dict = Security(validate_token)) -> JSONResponse | PatientReadResult:
    """Get a specific Patient resource by ID from the external FHIR server."""
    if err := _check_config():
        return err
    if not is_valid_fhir_id(resource_id):
        return operation_outcome_response(400, "invalid", "Invalid Patient id. FHIR ids must be 1-64 characters of [A-Za-z0-9-.].")
    result = await _external_patient_get(resource_id=resource_id)
    return fhir_result_response(result, PatientResource)
