"""Shared FHIR metadata helpers for batch-job execution constraints."""

from datetime import date

DOCUMENT_DATE_RANGE_EXTENSION_URL = "https://smartchartsuite.org/fhir/StructureDefinition/rc-api-document-date-range"


def document_date_range_extension(start_date: date | None, end_date: date | None) -> dict | None:
    """Return the FHIR extension describing an inclusive DocumentReference.date range."""
    if start_date is None and end_date is None:
        return None
    period: dict[str, str] = {}
    if start_date is not None:
        period["start"] = start_date.isoformat()
    if end_date is not None:
        period["end"] = end_date.isoformat()
    return {"url": DOCUMENT_DATE_RANGE_EXTENSION_URL, "valuePeriod": period}
