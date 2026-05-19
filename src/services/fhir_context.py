"""Fetches patient DocumentReference plain-text content from the external FHIR server.

Returns a list of document dicts, one per DocumentReference with text/plain content.
Returns an empty list if the patient has no documents — in which case the orchestrator
skips all LLM prompt execution.
"""

import base64
from datetime import datetime

import httpx
from loguru import logger

from src.util.settings import external_fhir_server_auth, external_fhir_server_url

_TIMEOUT = 60
_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)


def _auth_headers() -> dict:
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    return headers


async def fetch_patient_documents(patient_id: str) -> list[dict]:
    """Fetch all text/plain DocumentReferences for a patient.

    Each returned dict has the shape:
        {
            "id":   str,  # DocumentReference.id on the FHIR server
            "type": str,  # DocumentReference.type display text (or "Unknown")
            "date": str,  # DocumentReference.date (ISO8601) or today
            "text": str,  # Base64-decoded plain text content
        }

    Returns [] if the patient has no documents or none have text/plain attachments.
    """
    assert external_fhir_server_url
    url = f"{external_fhir_server_url.rstrip('/')}/DocumentReference"
    params = {"subject": f"Patient/{patient_id}", "_count": "500"}

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        try:
            resp = await client.get(url, headers=_auth_headers(), params=params)
        except httpx.RequestError as exc:
            logger.error(f"Failed to reach external FHIR server for DocumentReferences: {exc}")
            return []

    if not resp.is_success:
        logger.error(f"DocumentReference search for Patient/{patient_id} returned {resp.status_code}")
        return []

    bundle = resp.json()
    entries = bundle.get("entry", [])
    if not entries:
        logger.info(f"No DocumentReferences found for Patient/{patient_id}")
        return []

    documents: list[dict] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        for entry in entries:
            doc_ref = entry.get("resource", {})
            if doc_ref.get("resourceType") != "DocumentReference":
                continue
            doc_id = doc_ref.get("id", "unknown")
            doc_date = doc_ref.get("date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))

            # Best-effort type display
            try:
                doc_type = doc_ref["type"]["coding"][0].get("display", "Unknown")
            except (KeyError, IndexError):
                doc_type = doc_ref.get("type", {}).get("text", "Unknown")

            # Find text/plain content entries
            for content_entry in doc_ref.get("content", []):
                attachment = content_entry.get("attachment", {})
                if attachment.get("contentType") != "text/plain":
                    continue

                plain_text: str | None = None

                # Inline base64-encoded data
                if "data" in attachment:
                    try:
                        plain_text = base64.b64decode(attachment["data"]).decode("utf-8")
                    except Exception as exc:
                        logger.warning(f"Failed to decode base64 data for DocRef {doc_id}: {exc}")

                # External URL
                elif "url" in attachment:
                    try:
                        url_resp = await client.get(attachment["url"], headers=_auth_headers())
                        if url_resp.is_success:
                            plain_text = url_resp.text
                        else:
                            logger.warning(f"Failed to fetch attachment URL for DocRef {doc_id}: {url_resp.status_code}")
                    except httpx.RequestError as exc:
                        logger.warning(f"Request error fetching attachment for DocRef {doc_id}: {exc}")

                if plain_text:
                    documents.append(
                        {
                            "id": doc_id,
                            "type": doc_type,
                            "date": doc_date,
                            "text": plain_text,
                        }
                    )
                    break  # one text/plain entry per DocumentReference is enough

    logger.info(f"Fetched {len(documents)} text/plain DocumentReference(s) for Patient/{patient_id}")
    return documents
