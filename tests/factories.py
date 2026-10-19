"""Small shared builders for batch-job tests."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from src.services.job_state.models import BatchJobs, QuestionnaireResponses
from src.util.settings import settings
from tests.fakes import patient_resource

_DEFAULT_CREATED_AT = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def expected_author(display: str, sub: str) -> dict[str, Any]:
    """Return the server-stamped QuestionnaireResponse.author for the current settings."""
    identifier = {"system": settings.oauth2_issuer, "value": sub} if settings.oauth2_issuer else {"value": sub}
    return {"display": display, "identifier": identifier}


def batch_job(
    *,
    batch_id: str = "batch-123",
    patient_id: str = "patient-123",
    job_package: str = "ExampleRegistry",
    started_by: str = "user-123",
    status: str = "complete",
    result_bundle: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    questionnaire_id: str | None = None,
    questionnaire_title: str | None = None,
    job_package_version: str | None = None,
    requested_jobs: list[str] | None = None,
    attempt_count: int = 0,
    heartbeat_at: datetime | None = None,
    updated_at: datetime | None = None,
    worker_id: str | None = None,
    lease_expires_at: datetime | None = None,
    next_attempt_at: datetime | None = None,
    max_attempts: int = 3,
    last_error: str | None = None,
) -> BatchJobs:
    """Build an in-memory batch row with stable metadata defaults."""
    return BatchJobs(
        batch_id=batch_id,
        patient_id=patient_id,
        job_package=job_package,
        started_by=started_by,
        status=status,
        result_bundle=result_bundle,
        created_at=_DEFAULT_CREATED_AT if created_at is None else created_at,
        completed_at=completed_at,
        questionnaire_id=questionnaire_id,
        questionnaire_title=questionnaire_title,
        job_package_version=job_package_version,
        requested_jobs=requested_jobs,
        attempt_count=attempt_count,
        heartbeat_at=heartbeat_at,
        updated_at=updated_at,
        worker_id=worker_id,
        lease_expires_at=lease_expires_at,
        next_attempt_at=next_attempt_at,
        max_attempts=max_attempts,
        last_error=last_error,
    )


def response_record(
    *,
    response_id: str = "response-123",
    batch_job_id: str = "batch-123",
    job_package: str = "ExampleRegistry",
    patient_id: str = "patient-123",
    response: dict[str, Any] | None = None,
    updated_at: datetime | None = None,
) -> QuestionnaireResponses:
    """Build the response attributes read by the batch-job service."""
    return QuestionnaireResponses(
        response_id=response_id,
        batch_job_id=batch_job_id,
        job_package=job_package,
        patient_id=patient_id,
        response={"status": "in-progress"} if response is None else response,
        updated_at=updated_at,
    )


def patient(
    *,
    patient_id: str = "patient-123",
    name: list[dict[str, Any]] | None = None,
    birth_date: str | None = None,
    gender: str | None = None,
) -> dict[str, Any]:
    """Build a minimal Patient resource with optional searchable fields."""
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if birth_date is not None:
        fields["birthDate"] = birth_date
    if gender is not None:
        fields["gender"] = gender
    return patient_resource(patient_id, **fields)


def fake_fetch_patient(
    patient_resources: Mapping[str, dict[str, Any] | None] | None = None,
    *,
    fetch_calls: list[str] | None = None,
) -> Callable[[str], Awaitable[dict[str, Any] | None]]:
    """Return an async patient lookup backed by a per-test resource map."""
    resources = dict(patient_resources or {})

    async def _fetch_patient(patient_id: str) -> dict[str, Any] | None:
        if fetch_calls is not None:
            fetch_calls.append(patient_id)
        if patient_id in resources:
            return resources[patient_id]
        return patient(patient_id=patient_id)

    return _fetch_patient
