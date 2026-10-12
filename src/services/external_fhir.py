"""Best-effort external FHIR resource fetches shared by batch flows."""

import httpx
from loguru import logger

from src.util.settings import external_fhir_server_auth, external_fhir_server_url

_TIMEOUT = 30


async def fetch_patient(patient_id: str) -> dict | None:
    """Fetch a Patient resource, returning None for request or JSON failures."""
    assert external_fhir_server_url
    url = f"{external_fhir_server_url.rstrip('/')}/Patient/{patient_id}"
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
        if resp.is_success:
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"Could not fetch Patient/{patient_id}: {exc}")
    return None
