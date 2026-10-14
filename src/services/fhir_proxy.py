"""Shared thin transport proxy for HAPI FHIR CRUD operations.

Used by the ``group``, ``jobpackage``, and ``library`` routers (and service-level Questionnaire
lookups), this module validates FHIR IDs, sends retried GET/POST/PUT/DELETE requests to
``HAPI_FHIR_CQL_EXECUTION_URL``, and normalizes transport, timeout, and upstream failures into
``FHIRProxyResponse``/OperationOutcome data. CRUD requests carry no patient data. It has no domain
logic or persistence: parameter binding, auth and OpenAPI metadata belong in ``src/routers``, use
cases in the calling services, and database access in the ``job_state`` package.
"""

from typing import Any, cast

import httpx
from loguru import logger

from src.models.fhir import FHIRJSON, FHIRProxyResponse, FHIRProxyResult
from src.services.errorhandler import make_operation_outcome
from src.util.http_clients import fhir_headers, get_http_client, send_with_retry
from src.util.outbound_url import is_valid_fhir_id, path_segment
from src.util.settings import require_setting, settings


def _base_url(resource_type: str, resource_id: str | None = None) -> str:
    base_url = require_setting(settings.hapi_fhir_cql_execution_url, "HAPI_FHIR_CQL_EXECUTION_URL")
    base = f"{base_url.rstrip('/')}/{path_segment(resource_type)}"
    if resource_id:
        base += f"/{path_segment(resource_id)}"
    return base


def _invalid_id_outcome(resource_type: str) -> FHIRProxyResponse:
    return FHIRProxyResponse(
        data=cast(FHIRJSON, make_operation_outcome("invalid", f"Invalid {resource_type} id. FHIR ids must be 1-64 characters of [A-Za-z0-9-.].")),
        status_code=400,
    )


async def _request(
    method: str,
    resource_type: str,
    resource_id: str | None = None,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> FHIRProxyResponse:
    """Send one HAPI FHIR request using the shared proxy transport policy."""
    if resource_id is not None and not is_valid_fhir_id(resource_id):
        return _invalid_id_outcome(resource_type)
    url = _base_url(resource_type, resource_id)
    request_kwargs: dict[str, Any] = {"headers": fhir_headers(content_type=True)}
    if method == "GET":
        request_kwargs["params"] = params or {}
    elif method in {"POST", "PUT"}:
        request_kwargs["json"] = body

    client = get_http_client("fhir")
    try:
        resp = await send_with_retry(client, method, url, profile="fhir", **request_kwargs)
    except httpx.TimeoutException:
        logger.error(f"FHIR {method} {resource_type} timed out")
        return FHIRProxyResponse(
            data=cast(FHIRJSON, make_operation_outcome("timeout", f"The FHIR server did not respond in time for {method} {resource_type}.")),
            status_code=504,
            error_type="timeout",
        )
    except httpx.RequestError as exc:
        logger.error(f"FHIR {method} {resource_type} request failed: {type(exc).__name__}")
        return FHIRProxyResponse(
            data=cast(FHIRJSON, make_operation_outcome("transient", f"The FHIR server could not be reached for {method} {resource_type}.")),
            status_code=502,
            error_type="transport",
        )
    logger.debug(f"FHIR {method} {url} → {resp.status_code}")
    return _handle(resp, method, resource_type)


async def fhir_get_with_status(
    resource_type: str,
    resource_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> FHIRProxyResponse:
    """GET a FHIR resource or search while retaining the HTTP status."""
    return await _request("GET", resource_type, resource_id, params=params)


async def fhir_get(
    resource_type: str,
    resource_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> FHIRProxyResult:
    """GET a FHIR resource or execute a search."""
    return (await fhir_get_with_status(resource_type, resource_id, params)).data


async def fhir_post_with_status(resource_type: str, body: dict[str, Any]) -> FHIRProxyResponse:
    """POST (create) a FHIR resource while retaining the HTTP status."""
    return await _request("POST", resource_type, body=body)


async def fhir_post(resource_type: str, body: dict[str, Any]) -> FHIRProxyResult:
    """POST (create) a FHIR resource."""
    return (await fhir_post_with_status(resource_type, body)).data


async def fhir_put_with_status(resource_type: str, resource_id: str, body: dict[str, Any]) -> FHIRProxyResponse:
    """PUT (update) a FHIR resource while retaining the HTTP status."""
    return await _request("PUT", resource_type, resource_id, body=body)


async def fhir_put(resource_type: str, resource_id: str, body: dict[str, Any]) -> FHIRProxyResult:
    """PUT (update) a FHIR resource."""
    return (await fhir_put_with_status(resource_type, resource_id, body)).data


async def fhir_delete_with_status(resource_type: str, resource_id: str) -> FHIRProxyResponse:
    """DELETE a FHIR resource while retaining the HTTP status."""
    return await _request("DELETE", resource_type, resource_id)


async def fhir_delete(resource_type: str, resource_id: str) -> FHIRProxyResult:
    """DELETE a FHIR resource."""
    return (await fhir_delete_with_status(resource_type, resource_id)).data


def _handle(resp: httpx.Response, method: str, target: str) -> FHIRProxyResponse:
    """Parse the FHIR response, wrapping errors as OperationOutcome.

    ``target`` is a client-safe label (resource type), never a full upstream URL.
    """
    try:
        data = resp.json()
    except ValueError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    if resp.is_success:
        # HAPI commonly answers DELETE with 204 No Content (or 200 with no body); routers
        # validate the result as an OperationOutcome, so synthesize an informational one.
        if method == "DELETE" and data.get("resourceType") != "OperationOutcome":
            data = cast(FHIRJSON, make_operation_outcome("informational", f"Successfully deleted {target}.", severity="information"))
        return FHIRProxyResponse(data=cast(FHIRProxyResult, data), status_code=resp.status_code)

    # Pass through FHIR OperationOutcomes from the server as-is
    if data.get("resourceType") == "OperationOutcome":
        return FHIRProxyResponse(data=cast(FHIRJSON, data), status_code=resp.status_code)

    # Upstream bodies may contain sensitive detail; keep a truncated copy at debug level only.
    logger.error(f"FHIR {method} {target} failed with HTTP {resp.status_code}")
    logger.debug(f"FHIR {method} {target} error body (truncated): {resp.text[:500]}")
    return FHIRProxyResponse(
        data=cast(
            FHIRJSON,
            make_operation_outcome(
                "transient",
                f"FHIR server returned HTTP {resp.status_code} for {method} {target}",
            ),
        ),
        status_code=resp.status_code,
    )
