"""Fetch and filter patient DocumentReferences for LLM context on the external FHIR server.

This module searches and paginates same-origin DocumentReferences, accepts text/plain inline or safe
attachments, bounds attachment size/count/concurrency, and records safe metadata for excluded
documents. It returns ``DocumentFetchResult`` or raises ``DocumentFetchError`` when the search
fails, so the orchestrator can distinguish "no documents" (skip LLM execution) from a retryable
outage. It does not execute prompts, build result Bundles, or persist documents. Those responsibilities
belong in ``llm_executor``, ``job_orchestrator``, and the ``job_state`` package.
"""

import asyncio
import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
from loguru import logger

from src.util.fhir_bundle import get_bundle_next_link, get_bundle_resources
from src.util.http_clients import fhir_headers, get_http_client, send_with_retry
from src.util.outbound_url import is_allowed_attachment_url, is_same_origin, resolve_http_reference, resolves_to_internal_address
from src.util.settings import require_setting, settings

_PAGE_SIZE = 100


class DocumentFetchError(RuntimeError):
    """The DocumentReference search failed (network error, non-2xx, or malformed Bundle)."""


@dataclass
class DocumentFetchResult:
    """Fetched documents and client-safe metadata for documents excluded during fetching."""

    documents: list[dict]
    excluded: list[dict]


@dataclass(frozen=True)
class _DocumentTypeRule:
    """One exact FHIR token match for DocumentReference.type."""

    raw: str
    system: str | None
    code: str | None

    def matches(self, coding: object) -> bool:
        if not isinstance(coding, dict):
            return False
        return (self.system is None or coding.get("system") == self.system) and (self.code is None or coding.get("code") == self.code)


def _parse_document_type_rules(values: list[str], setting_name: str) -> list[_DocumentTypeRule]:
    rules: list[_DocumentTypeRule] = []
    for raw_value in values:
        raw = raw_value.strip()
        if not raw:
            continue
        if "|" in raw:
            system, code = raw.split("|", 1)
            system = system or None
            code = code or None
        else:
            system, code = None, raw
        if system is None and code is None:
            raise DocumentFetchError(f"{setting_name} contains an empty FHIR token")
        rules.append(_DocumentTypeRule(raw=raw, system=system, code=code))
    return rules


def _document_type_filter_reason(resource: dict, include_rules: list[_DocumentTypeRule], exclude_rules: list[_DocumentTypeRule]) -> str | None:
    codings = resource.get("type", {}).get("coding", []) if isinstance(resource.get("type"), dict) else []
    if not isinstance(codings, list):
        codings = []
    if any(rule.matches(coding) for rule in exclude_rules for coding in codings):
        return "document_type_excluded"
    if include_rules and not any(rule.matches(coding) for rule in include_rules for coding in codings):
        return "document_type_not_included"
    return None


class _AttachmentTooLargeError(Exception):
    pass


async def _read_capped_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Stream an attachment body, aborting once it exceeds settings.doc_max_bytes."""
    # Streamed attachments are intentionally unretried: a retry cannot safely resume after body bytes are consumed.
    async with client.stream("GET", url, headers=fhir_headers(url, with_auth=True), follow_redirects=False) as resp:
        if not resp.is_success:
            logger.warning(f"Attachment fetch returned HTTP {resp.status_code}")
            return None
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > settings.doc_max_bytes:
            raise _AttachmentTooLargeError
        chunks: list[bytes] = []
        received = 0
        async for chunk in resp.aiter_bytes():
            received += len(chunk)
            if received > settings.doc_max_bytes:
                raise _AttachmentTooLargeError
            chunks.append(chunk)
    return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")


def _parse_document_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def _document_date_filter_reason(resource: dict, start_date: date | None, end_date: date | None) -> str | None:
    if start_date is None and end_date is None:
        return None
    document_date = _parse_document_date(resource.get("date"))
    if document_date is None:
        return "missing_document_date"
    if start_date is not None and document_date < start_date:
        return "outside_document_date_range"
    if end_date is not None and document_date > end_date:
        return "outside_document_date_range"
    return None


async def _search_document_entries(
    client: httpx.AsyncClient,
    base_url: str,
    patient_id: str,
    document_start_date: date | None = None,
    document_end_date: date | None = None,
) -> tuple[list[dict], dict | None, list[dict]]:
    """Collect policy-matching DocumentReference entries, following same-origin `next` links up to the document cap."""
    url: str | None = f"{base_url.rstrip('/')}/DocumentReference"
    params: list[tuple[str, str]] | None = [
        ("subject", f"Patient/{patient_id}"),
        ("_count", str(min(_PAGE_SIZE, settings.doc_max_count))),
    ]
    if document_start_date is not None:
        params.append(("date", f"ge{document_start_date.isoformat()}"))
    if document_end_date is not None:
        params.append(("date", f"le{document_end_date.isoformat()}"))
    include_type_rules = _parse_document_type_rules(settings.document_type_include, "DOCUMENT_TYPE_INCLUDE")
    exclude_type_rules = _parse_document_type_rules(settings.document_type_exclude, "DOCUMENT_TYPE_EXCLUDE")
    for rule in include_type_rules:
        params.append(("type", rule.raw))
    for rule in exclude_type_rules:
        params.append(("type:not", rule.raw))
    entries: list[dict] = []
    filter_exclusion_counts: dict[str, int] = {}
    while url and len(entries) < settings.doc_max_count:
        try:
            resp = await send_with_retry(
                client,
                "GET",
                url,
                profile="attachments",
                headers=fhir_headers(url, with_auth=True),
                params=params,
            )
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

        page_resources = get_bundle_resources(bundle)
        for resource in page_resources:
            reason = _document_date_filter_reason(resource, document_start_date, document_end_date)
            if reason is None:
                reason = _document_type_filter_reason(resource, include_type_rules, exclude_type_rules)
            if reason is not None:
                filter_exclusion_counts[reason] = filter_exclusion_counts.get(reason, 0) + 1
                continue
            entries.append({"resource": resource})

        next_url = get_bundle_next_link(bundle)
        url, params = None, None
        if next_url and page_resources:
            resolved = resolve_http_reference(base_url, next_url)
            if resolved and is_same_origin(resolved, base_url):
                url = resolved
            else:
                logger.warning("Ignoring DocumentReference `next` link that is not on the FHIR server origin")

    count_exclusion: dict | None = None
    if len(entries) > settings.doc_max_count or url:
        logger.warning(f"Patient/{patient_id} has more DocumentReferences than DOC_MAX_COUNT={settings.doc_max_count}; processing only the first {settings.doc_max_count}.")
        count_exclusion = {"reason": "exceeds_max_count"}
        if url is None and len(entries) > settings.doc_max_count:
            count_exclusion["count"] = len(entries) - settings.doc_max_count
    filter_exclusions = [{"reason": reason, "count": count} for reason, count in filter_exclusion_counts.items()]
    return entries[: settings.doc_max_count], count_exclusion, filter_exclusions


async def fetch_patient_documents(
    patient_id: str,
    document_start_date: date | None = None,
    document_end_date: date | None = None,
) -> DocumentFetchResult:
    """Fetch all text/plain DocumentReferences for a patient.

    Each returned document dict has the shape:
        {
            "id":   str,  # DocumentReference.id on the FHIR server
            "type": str,  # DocumentReference.type display text (or "Unknown")
            "date": str,  # DocumentReference.date (ISO8601) or today
            "text": str,  # Base64-decoded plain text content
        }

    Returns a result with no documents or exclusions if the patient has no documents or
    none have text/plain attachments. Configured DocumentReference.type and date policies are
    applied before attachment fetching. Raises DocumentFetchError if the search cannot be completed.
    """
    base_url = require_setting(settings.external_fhir_server_url, "EXTERNAL_FHIR_SERVER_URL")

    documents: list[dict] = []
    semaphore = asyncio.Semaphore(settings.doc_fetch_max_concurrency)
    client = get_http_client("attachments")
    entries, count_exclusion, filter_exclusions = await _search_document_entries(
        client,
        base_url,
        patient_id,
        document_start_date,
        document_end_date,
    )
    if not entries:
        logger.info(f"No DocumentReferences found for Patient/{patient_id}")
        excluded = [*filter_exclusions, *([count_exclusion] if count_exclusion else [])]
        return DocumentFetchResult(documents=[], excluded=excluded)

    async def _process_entry(entry: dict) -> tuple[dict | None, dict | None]:
        doc_ref = entry.get("resource", {})
        if doc_ref.get("resourceType") != "DocumentReference":
            return None, None
        doc_id = doc_ref.get("id", "unknown")
        doc_date = doc_ref.get("date", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        excluded_reason: str | None = None

        def mark_excluded(reason: str) -> None:
            nonlocal excluded_reason
            if excluded_reason is None:
                excluded_reason = reason

        # Best-effort type display
        try:
            doc_type = doc_ref["type"]["coding"][0].get("display", "Unknown")
        except KeyError, IndexError:
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
                if isinstance(data, str) and len(data) > (settings.doc_max_bytes * 4) // 3 + 4:
                    mark_excluded("exceeds_max_bytes")
                    logger.warning(f"Skipping inline attachment for DocRef {doc_id}: exceeds DOC_MAX_BYTES")
                    continue
                try:
                    plain_text = base64.b64decode(data).decode("utf-8")
                except (binascii.Error, TypeError, UnicodeError, ValueError) as exc:
                    mark_excluded("decode_error")
                    logger.warning(f"Failed to decode base64 data for DocRef {doc_id}: {exc}")

            # External URL
            elif "url" in attachment:
                attachment_url = resolve_http_reference(base_url, attachment["url"])
                if attachment_url is None:
                    mark_excluded("unsafe_url")
                    logger.warning(f"Skipping unsafe attachment URL for DocRef {doc_id}")
                    continue
                if not is_allowed_attachment_url(attachment_url, base_url, settings.attachment_allowed_hosts):
                    mark_excluded("host_not_allowed")
                    logger.warning(f"Skipping attachment for DocRef {doc_id}: host is not the FHIR server or in ATTACHMENT_ALLOWED_HOSTS")
                    continue
                if not is_same_origin(attachment_url, base_url) and await resolves_to_internal_address(attachment_url):
                    mark_excluded("non_public_address")
                    logger.warning(f"Skipping attachment for DocRef {doc_id}: allowlisted host resolves to a non-public address")
                    continue
                try:
                    async with semaphore:
                        plain_text = await _read_capped_text(client, attachment_url)
                except _AttachmentTooLargeError:
                    mark_excluded("exceeds_max_bytes")
                    logger.warning(f"Skipping attachment for DocRef {doc_id}: exceeds DOC_MAX_BYTES")
                    continue
                except httpx.RequestError as exc:
                    mark_excluded("fetch_error")
                    logger.warning(f"Request error fetching attachment for DocRef {doc_id}: {type(exc).__name__}")
                    continue
                if plain_text is None:
                    mark_excluded("fetch_error")

            if plain_text:
                return (
                    {
                        "id": doc_id,
                        "type": doc_type,
                        "date": doc_date,
                        "text": plain_text,
                        "resource": doc_ref,
                    },
                    None,
                )
        exclusion = {"doc_id": doc_id, "reason": excluded_reason} if excluded_reason else None
        return None, exclusion

    results = await asyncio.gather(*[_process_entry(entry) for entry in entries])
    documents = [doc for doc, _ in results if doc is not None]
    excluded = [exclusion for _, exclusion in results if exclusion is not None]

    excluded_documents = [*filter_exclusions, *([count_exclusion] if count_exclusion else []), *excluded]
    logger.info(f"Fetched {len(documents)} text/plain DocumentReference(s) for Patient/{patient_id}")
    return DocumentFetchResult(documents=documents, excluded=excluded_documents)
