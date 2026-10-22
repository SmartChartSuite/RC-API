"""Best-effort access to Patient resources on the configured external FHIR server.

This module validates a FHIR Patient ID and fetches one Patient for batch-job enrichment and
result assembly, returning ``None`` for invalid IDs, unsuccessful requests, or JSON failures. This
narrow external-FHIR collaborator leaves DocumentReference search/content handling to ``fhir_context``
and HAPI CRUD proxying to ``fhir_proxy``; batch use cases remain in
``batch_job_service``/``job_orchestrator``.
"""

import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx
from loguru import logger

from src.util.http_clients import fhir_headers, get_http_client, send_with_retry
from src.util.outbound_url import is_valid_fhir_id, path_segment
from src.util.settings import require_setting, settings

_PATIENT_SUMMARY_FIELDS = ("id", "name", "birthDate", "gender")


class _PatientSummaryCacheEntry:
    def __init__(self, cached_at: float, summary: dict[str, Any]) -> None:
        self.cached_at = cached_at
        self.summary = summary


_patient_summary_cache: OrderedDict[str, _PatientSummaryCacheEntry] = OrderedDict()
_patient_summary_cache_lock = asyncio.Lock()
_patient_summary_in_flight: dict[str, asyncio.Task[dict[str, Any] | None]] = {}


def _project_patient_summary(patient: dict[str, Any]) -> dict[str, Any]:
    """Keep only Patient fields needed for list display and identity disambiguation."""
    return {field: patient[field] for field in _PATIENT_SUMMARY_FIELDS if field in patient}


def clear_patient_summary_cache() -> None:
    """Clear cached Patient summaries; intended for tests and process lifecycle control."""
    _patient_summary_cache.clear()


def _trim_patient_summary_cache() -> None:
    max_entries = max(0, settings.patient_summary_cache_max_entries)
    while len(_patient_summary_cache) > max_entries:
        _patient_summary_cache.popitem(last=False)


async def fetch_patient_summary(patient_id: str) -> dict[str, Any] | None:
    """Return a cached projected Patient summary, fetching the full resource only on a miss."""
    ttl = settings.patient_summary_cache_ttl_seconds
    now = time.monotonic()
    if ttl > 0:
        async with _patient_summary_cache_lock:
            cached = _patient_summary_cache.get(patient_id)
            if cached is not None:
                if now - cached.cached_at < ttl:
                    _patient_summary_cache.move_to_end(patient_id)
                    return dict(cached.summary)
                _patient_summary_cache.pop(patient_id, None)

    async with _patient_summary_cache_lock:
        in_flight = _patient_summary_in_flight.get(patient_id)
        if in_flight is None:
            in_flight = asyncio.create_task(fetch_patient(patient_id))
            _patient_summary_in_flight[patient_id] = in_flight

    try:
        patient = await in_flight
    finally:
        async with _patient_summary_cache_lock:
            if _patient_summary_in_flight.get(patient_id) is in_flight:
                _patient_summary_in_flight.pop(patient_id, None)

    if not isinstance(patient, dict):
        return None

    summary = _project_patient_summary(patient)
    if ttl > 0 and settings.patient_summary_cache_max_entries > 0:
        async with _patient_summary_cache_lock:
            _patient_summary_cache[patient_id] = _PatientSummaryCacheEntry(time.monotonic(), summary)
            _patient_summary_cache.move_to_end(patient_id)
            _trim_patient_summary_cache()
    return dict(summary)


async def fetch_patient(patient_id: str) -> dict | None:
    """Fetch a Patient resource, returning None for invalid ids, request, or JSON failures."""
    base_url = require_setting(settings.external_fhir_server_url, "EXTERNAL_FHIR_SERVER_URL")
    if not is_valid_fhir_id(patient_id):
        logger.warning("Refusing to fetch Patient with an invalid FHIR id")
        return None
    url = f"{base_url.rstrip('/')}/Patient/{path_segment(patient_id)}"
    client = get_http_client("external_fhir")
    try:
        resp = await send_with_retry(client, "GET", url, profile="external_fhir", headers=fhir_headers(url, with_auth=True))
        if resp.is_success:
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"Could not fetch Patient/{patient_id}: {type(exc).__name__}")
    return None
