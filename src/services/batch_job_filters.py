"""Pure parsing and matching helpers for batch-job list filters.

This module normalizes CSV and ISO-date query values, then applies case-insensitive string and
date-range matching for batch-job searches. It contains no I/O, persistence, or HTTP response
handling; callers in ``batch_job_service`` combine these predicates with database and FHIR data.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class InvalidDateFilter:
    """A date filter value that could not be parsed as an ISO date."""

    parameter_name: str


def matches_filter(value: str | None, expected: str | None, *, partial: bool = False) -> bool:
    if not expected:
        return True
    if not value:
        return False
    if partial:
        return expected.casefold() in value.casefold()
    return value.casefold() == expected.casefold()


def matches_any_filter(value: str | None, expected: list[str] | None, *, partial: bool = False) -> bool:
    if not expected:
        return True
    return any(matches_filter(value, candidate, partial=partial) for candidate in expected)


def parse_csv_filter(value: str | None) -> list[str] | None:
    if not value:
        return None
    values = [part.strip().casefold() for part in value.split(",") if part.strip()]
    return values or None


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_iso_date_filter(value: str | None, parameter_name: str) -> tuple[date | None, InvalidDateFilter | None]:
    parsed = parse_iso_date(value)
    if value and parsed is None:
        return None, InvalidDateFilter(parameter_name)
    return parsed, None


def matches_date_range(value: date | None, start: date | None, end: date | None) -> bool:
    if start is None and end is None:
        return True
    if value is None:
        return False
    if start is not None and value < start:
        return False
    return not (end is not None and value > end)
