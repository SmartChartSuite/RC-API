"""Shared thin-proxy for HAPI FHIR CRUD operations.

Used by jobpackage, group, and library routers. All operations target
HAPI_FHIR_CQL_EXECUTION_URL and carry no patient data.
"""

from typing import Any, cast

import httpx
from loguru import logger

from src.models.fhir import FHIRProxyResult, OperationOutcomeJSON
from src.services.errorhandler import make_operation_outcome
from src.util.settings import hapi_fhir_cql_execution_url

_TIMEOUT = 60
_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)


def _headers() -> dict:
    return {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}


def _base_url(resource_type: str, resource_id: str | None = None) -> str:
    assert hapi_fhir_cql_execution_url
    base = f"{hapi_fhir_cql_execution_url.rstrip('/')}/{resource_type}"
    if resource_id:
        base += f"/{resource_id}"
    return base


async def _request(
    method: str,
    resource_type: str,
    resource_id: str | None = None,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> FHIRProxyResult:
    """Send one HAPI FHIR request using the shared proxy transport policy."""
    url = _base_url(resource_type, resource_id)
    request_kwargs: dict[str, Any] = {"headers": _headers()}
    if method == "GET":
        request_kwargs["params"] = params or {}
    elif method in {"POST", "PUT"}:
        request_kwargs["json"] = body

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        resp = await client.request(method, url, **request_kwargs)
    logger.debug(f"FHIR {method} {url} → {resp.status_code}")
    return _handle(resp, method, url)


async def fhir_get(
    resource_type: str,
    resource_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> FHIRProxyResult:
    """GET a FHIR resource or execute a search."""
    return await _request("GET", resource_type, resource_id, params=params)


async def fhir_post(resource_type: str, body: dict[str, Any]) -> FHIRProxyResult:
    """POST (create) a FHIR resource."""
    return await _request("POST", resource_type, body=body)


async def fhir_put(resource_type: str, resource_id: str, body: dict[str, Any]) -> FHIRProxyResult:
    """PUT (update) a FHIR resource."""
    return await _request("PUT", resource_type, resource_id, body=body)


async def fhir_delete(resource_type: str, resource_id: str) -> FHIRProxyResult:
    """DELETE a FHIR resource."""
    return await _request("DELETE", resource_type, resource_id)


def _handle(resp: httpx.Response, method: str, url: str) -> FHIRProxyResult:
    """Parse the FHIR response, wrapping errors as OperationOutcome."""
    try:
        data = resp.json()
    except ValueError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    if resp.is_success:
        return cast(FHIRProxyResult, data)

    # Pass through FHIR OperationOutcomes from the server as-is
    if data.get("resourceType") == "OperationOutcome":
        return cast(OperationOutcomeJSON, data)

    logger.error(f"FHIR {method} {url} failed with {resp.status_code}: {resp.text}")
    return cast(
        OperationOutcomeJSON,
        make_operation_outcome(
            "transient",
            f"FHIR server returned HTTP {resp.status_code} for {method} {url}",
        ),
    )
