"""HAPI FHIR CQL Library evaluation service.

Wraps POST {HAPI_FHIR_CQL_EXECUTION_URL}/Library/{LibraryName}/$evaluate.
Library IDs are the CamelCase resource name — no numeric ID lookup needed.

The dataEndpoint.address is always sourced from settings.external_fhir_server_url.
The dataEndpoint.header is conditionally populated with the auth token when
settings.external_fhir_server_auth is set.
"""

import asyncio
from dataclasses import dataclass, field

import httpx
from loguru import logger

from src.util.settings import (
    external_fhir_server_auth,
    external_fhir_server_url,
    hapi_fhir_cql_execution_url,
)

_TIMEOUT = 300
_TRANSPORT = httpx.AsyncHTTPTransport(retries=5)


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
        "address": external_fhir_server_url,
    }
    # Inject auth header only when configured
    if external_fhir_server_auth:
        endpoint_resource["header"] = [f"Authorization: {external_fhir_server_auth}"]

    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "subject", "valueString": f"Patient/{patient_id}"},
            {"name": "useServerData", "valueBoolean": False},
            {"name": "dataEndpoint", "resource": endpoint_resource},
        ],
    }


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
    for param in data.get("parameter", []):
        name: str = param.get("name", "")
        # Skip terminology/concept filter entries
        if name.endswith("_concept"):
            continue
        resource = param.get("resource")
        if resource is not None:
            if resource.get("resourceType") == "OperationOutcome":
                diagnostics = _extract_operation_outcome_diagnostics(resource)
                logger.error(f"CQL evaluation error for {library_name}: {diagnostics}")
                return CqlResult(library_name=library_name, patient_id=patient_id, error=diagnostics)
            grouped.setdefault(name, []).append(resource)
        elif "valueString" in param:
            grouped.setdefault(name, []).append({"value": param["valueString"]})
        elif "valueBoolean" in param:
            grouped.setdefault(name, []).append({"value": param["valueBoolean"]})
        elif "valueDateTime" in param:
            grouped.setdefault(name, []).append({"value": param["valueDateTime"]})

    return CqlResult(library_name=library_name, patient_id=patient_id, results=grouped)


async def _evaluate_library(client: httpx.AsyncClient, library_name: str, patient_id: str) -> CqlResult:
    assert hapi_fhir_cql_execution_url
    url = f"{hapi_fhir_cql_execution_url.rstrip('/')}/Library/{library_name}/$evaluate"
    body = _build_parameters_body(patient_id)
    try:
        resp = await client.post(
            url,
            json=body,
            headers={"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
        )
    except httpx.TimeoutException:
        logger.error(f"Timeout evaluating CQL library {library_name}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error="timeout")
    except httpx.RequestError as exc:
        logger.error(f"Request error evaluating CQL library {library_name}: {exc}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error=str(exc))

    logger.info(f"CQL Library/{library_name}/$evaluate → {resp.status_code}")
    if resp.status_code in (504, 408):
        return CqlResult(library_name=library_name, patient_id=patient_id, error="gateway timeout")

    try:
        data = resp.json()
    except Exception as exc:
        logger.error(f"Failed to parse JSON from CQL response for {library_name}: {exc}")
        return CqlResult(library_name=library_name, patient_id=patient_id, error="invalid json")

    return _parse_parameters_response(library_name, patient_id, data)


async def run_cql_libraries(library_names: list[str], patient_id: str) -> list[CqlResult]:
    """Concurrently evaluate all CQL libraries for a patient.

    Args:
        library_names: FHIR Library resource IDs (e.g. ["Demographics"]).
        patient_id: The bare FHIR Patient ID (without "Patient/" prefix).

    Returns:
        List of CqlResult objects, one per library.
    """
    if not library_names:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        tasks = [_evaluate_library(client, lib, patient_id) for lib in library_names]
        results = await asyncio.gather(*tasks)
    logger.info(f"CQL evaluation complete for {len(library_names)} library(ies)")
    return list(results)
