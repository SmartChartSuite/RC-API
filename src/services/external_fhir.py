"""Best-effort external FHIR resource fetches shared by batch flows."""

import httpx
from loguru import logger

from src.util.http_clients import fhir_headers, get_http_client, send_with_retry
from src.util.outbound_url import is_valid_fhir_id, path_segment
from src.util.settings import require_setting, settings


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
