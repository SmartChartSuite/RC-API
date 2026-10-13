"""Best-effort external FHIR resource fetches shared by batch flows."""

import httpx
from loguru import logger

from src.util.outbound_url import is_valid_fhir_id, path_segment
from src.util.settings import external_fhir_server_auth, external_fhir_server_url, require_setting

_TIMEOUT = 30


async def fetch_patient(patient_id: str) -> dict | None:
    """Fetch a Patient resource, returning None for invalid ids, request, or JSON failures."""
    base_url = require_setting(external_fhir_server_url, "EXTERNAL_FHIR_SERVER_URL")
    if not is_valid_fhir_id(patient_id):
        logger.warning("Refusing to fetch Patient with an invalid FHIR id")
        return None
    url = f"{base_url.rstrip('/')}/Patient/{path_segment(patient_id)}"
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
        if resp.is_success:
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"Could not fetch Patient/{patient_id}: {type(exc).__name__}")
    return None
