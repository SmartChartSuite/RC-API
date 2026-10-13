"""Fetches patient DocumentReference plain-text content from the external FHIR server.

Returns a list of document dicts, one per DocumentReference with text/plain content.
Returns an empty list if the patient has no documents — in which case the orchestrator
skips all LLM prompt execution. Raises DocumentFetchError when the search itself fails,
so a transient outage is retried rather than mistaken for "no documents".
"""

import asyncio
import base64
import binascii
from datetime import UTC, datetime

import httpx
from loguru import logger

from src.util.outbound_url import is_allowed_attachment_url, is_same_origin, resolve_http_reference, resolves_to_internal_address
from src.util.settings import (
    attachment_allowed_hosts,
    doc_fetch_max_concurrency,
    doc_max_bytes,
    doc_max_count,
    external_fhir_server_auth,
    external_fhir_server_url,
    require_setting,
)

_TIMEOUT = 60
_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)
_PAGE_SIZE = 100


class DocumentFetchError(RuntimeError):
    """The DocumentReference search failed (network error, non-2xx, or malformed Bundle)."""


class _AttachmentTooLargeError(Exception):
    pass


def _auth_headers(target_url: str) -> dict[str, str]:
    """Build FHIR headers without forwarding credentials to another origin."""
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth and external_fhir_server_url and is_same_origin(target_url, external_fhir_server_url):
        headers["Authorization"] = external_fhir_server_auth
    return headers


async def _read_capped_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Stream an attachment body, aborting once it exceeds doc_max_bytes."""
    async with client.stream("GET", url, headers=_auth_headers(url), follow_redirects=False) as resp:
        if not resp.is_success:
            logger.warning(f"Attachment fetch returned HTTP {resp.status_code}")
            return None
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > doc_max_bytes:
            raise _AttachmentTooLargeError
        chunks: list[bytes] = []
        received = 0
        async for chunk in resp.aiter_bytes():
            received += len(chunk)
            if received > doc_max_bytes:
                raise _AttachmentTooLargeError
            chunks.append(chunk)
    return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")


async def _search_document_entries(client: httpx.AsyncClient, base_url: str, patient_id: str) -> list[dict]:
    """Collect DocumentReference Bundle entries, following same-origin `next` links up to doc_max_count."""
    url: str | None = f"{base_url.rstrip('/')}/DocumentReference"
    params: dict[str, str] | None = {"subject": f"Patient/{patient_id}", "_count": str(min(_PAGE_SIZE, doc_max_count))}
    entries: list[dict] = []
    while url and len(entries) < doc_max_count:
        try:
            resp = await client.get(url, headers=_auth_headers(url), params=params)
        except httpx.RequestError as exc:
            raise DocumentFetchError(f"DocumentReference search request failed ({type(exc).__name__})") from exc
        if not resp.is_success:
            raise DocumentFetchError(f"DocumentReference search returned HTTP {resp.status_code}")
        try:
            bundle = resp.json()
        except ValueError as exc:
            raise DocumentFetchError("DocumentReference search returned invalid JSON") from exc
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            raise DocumentFetchError("DocumentReference search did not return a Bundle")

        page_entries = bundle.get("entry") or []
        entries.extend(entry for entry in page_entries if isinstance(entry, dict))

        next_url = next((link.get("url") for link in bundle.get("link") or [] if isinstance(link, dict) and link.get("relation") == "next"), None)
        url, params = None, None
        if isinstance(next_url, str) and page_entries:
            resolved = resolve_http_reference(base_url, next_url)
            if resolved and is_same_origin(resolved, base_url):
                url = resolved
            else:
                logger.warning("Ignoring DocumentReference `next` link that is not on the FHIR server origin")

    if len(entries) > doc_max_count or url:
        logger.warning(f"Patient/{patient_id} has more DocumentReferences than DOC_MAX_COUNT={doc_max_count}; processing only the first {doc_max_count}.")
    return entries[:doc_max_count]


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
    Raises DocumentFetchError if the search cannot be completed.
    """
    base_url = require_setting(external_fhir_server_url, "EXTERNAL_FHIR_SERVER_URL")

    documents: list[dict] = []
    semaphore = asyncio.Semaphore(doc_fetch_max_concurrency)
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
        entries = await _search_document_entries(client, base_url, patient_id)
        if not entries:
            logger.info(f"No DocumentReferences found for Patient/{patient_id}")
            return []

        async def _process_entry(entry: dict) -> dict | None:
            doc_ref = entry.get("resource", {})
            if doc_ref.get("resourceType") != "DocumentReference":
                return None
            doc_id = doc_ref.get("id", "unknown")
            doc_date = doc_ref.get("date", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

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
                    data = attachment["data"]
                    # base64 expands by 4/3; reject before decoding to bound memory.
                    if isinstance(data, str) and len(data) > (doc_max_bytes * 4) // 3 + 4:
                        logger.warning(f"Skipping inline attachment for DocRef {doc_id}: exceeds DOC_MAX_BYTES")
                        continue
                    try:
                        plain_text = base64.b64decode(data).decode("utf-8")
                    except (binascii.Error, TypeError, UnicodeError, ValueError) as exc:
                        logger.warning(f"Failed to decode base64 data for DocRef {doc_id}: {exc}")

                # External URL
                elif "url" in attachment:
                    attachment_url = resolve_http_reference(base_url, attachment["url"])
                    if attachment_url is None:
                        logger.warning(f"Skipping unsafe attachment URL for DocRef {doc_id}")
                        continue
                    if not is_allowed_attachment_url(attachment_url, base_url, attachment_allowed_hosts):
                        logger.warning(f"Skipping attachment for DocRef {doc_id}: host is not the FHIR server or in ATTACHMENT_ALLOWED_HOSTS")
                        continue
                    if not is_same_origin(attachment_url, base_url) and await resolves_to_internal_address(attachment_url):
                        logger.warning(f"Skipping attachment for DocRef {doc_id}: allowlisted host resolves to a non-public address")
                        continue
                    try:
                        async with semaphore:
                            plain_text = await _read_capped_text(client, attachment_url)
                    except _AttachmentTooLargeError:
                        logger.warning(f"Skipping attachment for DocRef {doc_id}: exceeds DOC_MAX_BYTES")
                    except httpx.RequestError as exc:
                        logger.warning(f"Request error fetching attachment for DocRef {doc_id}: {type(exc).__name__}")

                if plain_text:
                    return {
                        "id": doc_id,
                        "type": doc_type,
                        "date": doc_date,
                        "text": plain_text,
                        "resource": doc_ref,
                    }
            return None

        results = await asyncio.gather(*[_process_entry(entry) for entry in entries])
        documents = [doc for doc in results if doc is not None]

    logger.info(f"Fetched {len(documents)} text/plain DocumentReference(s) for Patient/{patient_id}")
    return documents
