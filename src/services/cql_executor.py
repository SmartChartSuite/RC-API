"""HAPI FHIR CQL Library evaluation service.

Wraps POST {HAPI_FHIR_CQL_EXECUTION_URL}/Library/{LibraryName}/$evaluate.
Library IDs are the CamelCase resource name — no numeric ID lookup needed.

The dataEndpoint.address is always sourced from settings.external_fhir_server_url.
The dataEndpoint.header is conditionally populated with the auth token when
settings.external_fhir_server_auth is set.
"""

import asyncio
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from loguru import logger

from src.util.http_clients import fhir_headers, get_http_client, send_with_retry
from src.util.outbound_url import path_segment
from src.util.settings import require_setting, secret_value, settings

# Asyncio synchronization primitives can bind to the loop that first contends on them.
# Keep one limiter per running loop so all jobs on the application loop share it while
# test clients using separate loops remain isolated.
_cql_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def _get_cql_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _cql_semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(settings.cql_max_concurrency)
        _cql_semaphores[loop] = semaphore
    return semaphore


@dataclass
class CqlResult:
    """Results from evaluating a single CQL library."""

    library_name: str
    patient_id: str
    # Dict of retrieve_name → list of FHIR resource dicts (Parameters entries grouped by name)
    results: dict[str, list[dict]] = field(default_factory=dict)
    error: str | None = None


def _extract_operation_outcome_diagnostics(outcome: dict) -> str:
    """Extract a readable error string from an OperationOutcome resource."""
    diagnostics = [issue.get("diagnostics") for issue in outcome.get("issue", []) if issue.get("diagnostics")]
    if diagnostics:
        return "; ".join(diagnostics)
    codes = [issue.get("code") for issue in outcome.get("issue", []) if issue.get("code")]
    return "; ".join(codes)


def _build_parameters_body(patient_id: str) -> dict:
    """Build the FHIR Parameters body for Library/$evaluate."""
    endpoint_resource: dict = {
        "resourceType": "Endpoint",
        "status": "active",
        "connectionType": {
            "system": "http://terminology.hl7.org/CodeSystem/endpoint-connection-type",
            "code": "hl7-fhir-rest",
        },
        "name": "External FHIR Server",
        "payloadType": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/endpoint-payload-type",
                        "code": "any",
                    }
                ]
            }
        ],
        "address": settings.external_fhir_server_url,
    }
    # This is body content for HAPI's dataEndpoint, not an HTTP request header.
    if auth := secret_value(settings.external_fhir_server_auth):
        endpoint_resource["header"] = [f"Authorization: {auth}"]

    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "subject", "valueString": f"Patient/{patient_id}"},
            {"name": "useServerData", "valueBoolean": False},
            {"name": "dataEndpoint", "resource": endpoint_resource},
        ],
    }


_MAX_PARAMETER_PART_DEPTH = 10


def _append_parameter_entry(grouped: dict[str, list[dict]], name: str, entry: dict) -> None:
    grouped.setdefault(name, []).append(entry)


def _parse_parameter_entry(param: dict, depth: int) -> tuple[dict | None, str | None]:
    """Return one raw resource or one typed value entry from a Parameters parameter."""
    raw_name = param.get("name")
    name = raw_name if isinstance(raw_name, str) else ""
    if name.endswith("_concept"):
        return None, None

    resource = param.get("resource")
    if resource is not None:
        if not isinstance(resource, dict):
            logger.warning(f"CQL parameter '{name}' has a non-object resource")
            return None, None
        if resource.get("resourceType") == "OperationOutcome":
            return None, _extract_operation_outcome_diagnostics(resource)
        return resource, None

    value_key = next((key for key in param if isinstance(key, str) and key.startswith("value")), None)
    if value_key is not None:
        return {"value": param[value_key], "type": value_key[5:]}, None

    if "part" in param:
        if depth >= _MAX_PARAMETER_PART_DEPTH:
            logger.warning(f"CQL parameter '{name}' exceeded nested part depth limit")
            return {"value": {}, "type": "part"}, None

        raw_parts = param.get("part")
        if not isinstance(raw_parts, list):
            logger.warning(f"CQL parameter '{name}' has malformed nested parts")
            return {"value": {}, "type": "part"}, None

        grouped_parts: dict[str, list[dict]] = {}
        for part in raw_parts:
            if not isinstance(part, dict):
                logger.warning(f"CQL parameter '{name}' has a malformed nested part")
                continue
            raw_part_name = part.get("name")
            part_name = raw_part_name if isinstance(raw_part_name, str) else ""
            part_entry, error = _parse_parameter_entry(part, depth + 1)
            if error is not None:
                return None, error
            if part_entry is not None:
                _append_parameter_entry(grouped_parts, part_name, part_entry)
        return {"value": grouped_parts, "type": "part"}, None

    logger.debug(f"CQL parameter '{name}' has no value")
    return None, None


def _parse_parameters_response(library_name: str, patient_id: str, data: dict) -> CqlResult:
    """Parse the FHIR Parameters response from Library/$evaluate.

    Groups parameter entries by name, skipping *_concept suffix entries.
    Multiple entries with the same name are collected into a list.
    """
    if data.get("resourceType") == "OperationOutcome":
        diagnostics = _extract_operation_outcome_diagnostics(data)
        logger.error(f"CQL OperationOutcome for {library_name}: {diagnostics}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error=diagnostics)

    grouped: dict[str, list[dict]] = {}
    parameters = data.get("parameter", [])
    if not isinstance(parameters, list):
        logger.warning(f"CQL Parameters response for {library_name} has malformed parameters")
        return CqlResult(library_name=library_name, patient_id=patient_id, results=grouped)

    for param in parameters:
        if not isinstance(param, dict):
            logger.warning(f"CQL Parameters response for {library_name} has a malformed parameter")
            continue
        raw_name = param.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        entry, diagnostics = _parse_parameter_entry(param, 0)
        if diagnostics is not None:
            logger.error(f"CQL evaluation error for {library_name}: {diagnostics}")
            return CqlResult(library_name=library_name, patient_id=patient_id, error=diagnostics)
        if entry is not None and not name.endswith("_concept"):
            _append_parameter_entry(grouped, name, entry)

    return CqlResult(library_name=library_name, patient_id=patient_id, results=grouped)


async def _evaluate_library(client: httpx.AsyncClient, library_name: str, patient_id: str) -> CqlResult:
    base_url = require_setting(settings.hapi_fhir_cql_execution_url, "HAPI_FHIR_CQL_EXECUTION_URL")
    url = f"{base_url.rstrip('/')}/Library/{path_segment(library_name)}/$evaluate"
    body = _build_parameters_body(patient_id)
    try:
        resp = await send_with_retry(
            client,
            "POST",
            url,
            profile="cql",
            json=body,
            headers=fhir_headers(content_type=True),
        )
    except httpx.TimeoutException:
        logger.error(f"Timeout evaluating CQL library {library_name}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error="timeout")
    except httpx.RequestError as exc:
        logger.error(f"Request error evaluating CQL library {library_name}: {type(exc).__name__}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error=f"request failed ({type(exc).__name__})")

    logger.info(f"CQL Library/{library_name}/$evaluate → {resp.status_code}")
    if resp.status_code in (504, 408):
        return CqlResult(library_name=library_name, patient_id=patient_id, error="gateway timeout")

    try:
        data = resp.json()
    except ValueError as exc:
        if not resp.is_success:
            return CqlResult(library_name=library_name, patient_id=patient_id, error=f"HTTP {resp.status_code}")
        logger.error(f"Failed to parse JSON from CQL response for {library_name}: {exc}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error="invalid json")

    if not resp.is_success:
        diagnostics = _extract_operation_outcome_diagnostics(data) if isinstance(data, dict) and data.get("resourceType") == "OperationOutcome" else None
        error = f"HTTP {resp.status_code}: {diagnostics}" if diagnostics else f"HTTP {resp.status_code}"
        logger.error(f"CQL evaluation for {library_name} failed: {error}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error=error)
    if not isinstance(data, dict):
        return CqlResult(library_name=library_name, patient_id=patient_id, error="invalid json")

    return _parse_parameters_response(library_name, patient_id, data)


async def run_cql_libraries(
    library_names: list[str],
    patient_id: str,
    on_result: Callable[[CqlResult], Awaitable[None]] | None = None,
) -> list[CqlResult]:
    """Concurrently evaluate all CQL libraries for a patient.

    Args:
        library_names: FHIR Library resource IDs (e.g. ["Demographics"]).
        patient_id: The bare FHIR Patient ID (without "Patient/" prefix).
        on_result: Optional async callback invoked as each library finishes.

    Returns:
        List of CqlResult objects in the same order as ``library_names``.
    """
    if not library_names:
        return []
    library_names = list(dict.fromkeys(library_names))

    client = get_http_client("cql")

    async def _evaluate_and_report(library_name: str) -> CqlResult:
        async with _get_cql_semaphore():
            result = await _evaluate_library(client, library_name, patient_id)
        if on_result:
            await on_result(result)
        return result

    results = await asyncio.gather(*[_evaluate_and_report(library_name) for library_name in library_names])
    logger.info(f"CQL evaluation complete for {len(library_names)} library(ies)")
    return list(results)
