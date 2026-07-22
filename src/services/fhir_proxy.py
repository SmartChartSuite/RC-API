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


async def fhir_get(
    resource_type: str,
    resource_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> FHIRProxyResult:
    """GET a FHIR resource or execute a search."""
    url = _base_url(resource_type, resource_id)
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        resp = await client.get(url, headers=_headers(), params=params or {})
    logger.debug(f"FHIR GET {url} → {resp.status_code}")
    return _handle(resp, "GET", url)


async def fhir_post(resource_type: str, body: dict[str, Any]) -> FHIRProxyResult:
    """POST (create) a FHIR resource."""
    url = _base_url(resource_type)
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        resp = await client.post(url, headers=_headers(), json=body)
    logger.debug(f"FHIR POST {url} → {resp.status_code}")
    return _handle(resp, "POST", url)


async def fhir_put(resource_type: str, resource_id: str, body: dict[str, Any]) -> FHIRProxyResult:
    """PUT (update) a FHIR resource."""
    url = _base_url(resource_type, resource_id)
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        resp = await client.put(url, headers=_headers(), json=body)
    logger.debug(f"FHIR PUT {url} → {resp.status_code}")
    return _handle(resp, "PUT", url)


async def fhir_delete(resource_type: str, resource_id: str) -> FHIRProxyResult:
    """DELETE a FHIR resource."""
    url = _base_url(resource_type, resource_id)
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        resp = await client.delete(url, headers=_headers())
    logger.debug(f"FHIR DELETE {url} → {resp.status_code}")
    return _handle(resp, "DELETE", url)


def _handle(resp: httpx.Response, method: str, url: str) -> FHIRProxyResult:
    """Parse the FHIR response, wrapping errors as OperationOutcome."""
    try:
        data = resp.json()
    except Exception:
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
