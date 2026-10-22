"""Application and domain use cases behind the batch-job API.

This module validates and prepares submissions, resolves and prefills Questionnaires, and enriches
listings and statuses with Patient data. It applies batch-job filters and assembles Parameters and
Bundle responses for submission, listing, status, and result retrieval; it also coordinates deletion.
It is called by the batchjob router; it persists through the ``job_state`` package and uses
``fhir_proxy``/``external_fhir`` for upstream FHIR access. HTTP binding, auth, OpenAPI metadata, and
response mapping do not belong here; those stay in ``src/routers``. Worker execution belongs in
``job_orchestrator`` and ``batch_worker``.
"""

import asyncio
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from fastapi.responses import JSONResponse

from src.models.fhir import FHIRJSON, ParametersParameter, ParametersResponse
from src.models.job_request import JobRequest
from src.services.batch_job_filters import (
    InvalidDateFilter,
    matches_any_filter,
    matches_date_range,
    matches_filter,
    parse_csv_filter,
    parse_iso_date,
    parse_iso_date_filter,
)
from src.services.external_fhir import fetch_patient as _fetch_patient
from src.services.external_fhir import fetch_patient_summary as _fetch_patient_summary
from src.services.fhir_proxy import fhir_get
from src.services.job_state.batch_records import (
    create_batch_job_with_response,
    delete_batch_job_record,
    get_batch_job,
    get_batch_job_document_texts,
    get_batch_job_summary,
    query_batch_jobs,
)
from src.services.job_state.child_jobs import get_jobs_for_batch
from src.services.job_state.models import BatchJobs, QuestionnaireResponses
from src.services.job_state.responses import get_responses_for_batches
from src.util.current_user import CurrentUser, build_questionnaire_response_author, user_from_claims
from src.util.documents import add_document_text_content
from src.util.fhir_bundle import get_bundle_resources
from src.util.outbound_url import is_valid_fhir_id
from src.util.settings import settings
from src.util.threading import run_db


@dataclass(frozen=True)
class BatchJobError:
    """An error the router maps to a FHIR OperationOutcome response."""

    status_code: int
    code: str
    diagnostics: str
    severity: str = "error"


@dataclass(frozen=True)
class MissingConfig:
    """Required configuration names that the router maps to a 503 response."""

    names: tuple[str, ...]


@dataclass(frozen=True)
class StoredErrorBundle:
    """A stored non-Bundle result that the router returns with HTTP 500."""

    bundle: dict


ServiceFailure = BatchJobError | MissingConfig | StoredErrorBundle


@dataclass(frozen=True)
class BatchJobSubmission:
    """Values needed to construct the accepted POST response."""

    batch_id: str
    response_id: str
    start_time: str
    patient_id: str
    job_package: str


def patient_name(patient: dict[str, Any] | None) -> str | None:
    if not patient:
        return None
    names = patient.get("name")
    if not isinstance(names, list) or not names:
        return None
    primary = names[0]
    if not isinstance(primary, dict):
        return None
    family = primary.get("family")
    given = primary.get("given")
    first = given[0] if isinstance(given, list) and given else None
    if isinstance(family, str) and family and isinstance(first, str) and first:
        return f"{family}, {first}"
    return family or first


def questionnaire_response_info(response: QuestionnaireResponses | None) -> tuple[str | None, str | None, str | None]:
    """Return (response_id, status, last_updated) for a batch response, if any."""
    if response is None:
        return None, None, None
    response_id = getattr(response, "response_id", None)
    response_id = response_id if isinstance(response_id, str) and response_id else None
    response_resource = response.response
    status = response_resource.get("status") if isinstance(response_resource, dict) else None
    status = status if isinstance(status, str) and status else None
    meta = response_resource.get("meta") if isinstance(response_resource, dict) else None
    last_updated = meta.get("lastUpdated") if isinstance(meta, dict) else None
    last_updated = last_updated if isinstance(last_updated, str) and last_updated else None
    if last_updated is None:
        database_timestamp = getattr(response, "updated_at", None)
        last_updated = database_timestamp.isoformat() if database_timestamp is not None else None
    return response_id, status, last_updated


def to_batch_job_parameters(
    job: BatchJobs,
    response: QuestionnaireResponses | None,
    patient: dict[str, Any] | None = None,
    include_patient_resource: bool = False,
) -> ParametersResponse:
    parameters = [
        ParametersParameter(name="batchId", valueString=job.batch_id),
        ParametersParameter(name="patientId", valueString=job.patient_id),
        ParametersParameter(name="jobPackage", valueString=job.job_package),
        ParametersParameter(name="startedBy", valueString=job.started_by),
        ParametersParameter(name="batchJobStatus", valueString=cast(Any, job.status)),
        ParametersParameter(name="jobStartDateTime", valueDateTime=job.created_at.isoformat()),
    ]
    form_response_id, form_status, response_last_updated = questionnaire_response_info(response)
    if form_status:
        parameters.append(ParametersParameter(name="questionnaireResponseStatus", valueString=form_status))
    if response_last_updated:
        parameters.append(ParametersParameter(name="responseLastUpdated", valueDateTime=response_last_updated))
    if form_response_id:
        parameters.append(
            ParametersParameter(
                name="batchJobQuestionnaireResponse",
                valueReference={"reference": f"QuestionnaireResponse/{form_response_id}"},
            )
        )
    if job.questionnaire_title:
        parameters.append(ParametersParameter(name="jobPackageTitle", valueString=job.questionnaire_title))
    derived_patient_name = patient_name(patient)
    if derived_patient_name:
        parameters.append(ParametersParameter(name="patientName", valueString=derived_patient_name))
    birth_date = patient.get("birthDate") if isinstance(patient, dict) else None
    if isinstance(birth_date, str) and birth_date:
        parameters.append(ParametersParameter(name="patientDob", valueDate=birth_date))
    gender = patient.get("gender") if isinstance(patient, dict) else None
    if isinstance(gender, str) and gender:
        parameters.append(ParametersParameter(name="patientGender", valueCode=gender))
    if job.completed_at:
        parameters.append(ParametersParameter(name="jobCompletedDateTime", valueDateTime=job.completed_at.isoformat()))
    if include_patient_resource and patient is not None:
        parameters.append(ParametersParameter(name="patientResource", resource=patient))
    return ParametersResponse(parameter=parameters)


def required_batch_job_config() -> list[str]:
    return [k for k in ("EXTERNAL_FHIR_SERVER_URL", "HAPI_FHIR_CQL_EXECUTION_URL") if k in settings.config_errors]


_PATIENT_FETCH_MAX_CONCURRENCY = 8


async def fetch_patients(patient_ids: list[str]) -> dict[str, dict[str, Any] | None]:
    """Fetch unique Patients concurrently while bounding pressure on the FHIR server."""
    unique_ids = list(dict.fromkeys(patient_ids))
    semaphore = asyncio.Semaphore(_PATIENT_FETCH_MAX_CONCURRENCY)

    async def _fetch_one(patient_id: str) -> tuple[str, dict[str, Any] | None]:
        async with semaphore:
            return patient_id, await _fetch_patient(patient_id)

    return dict(await asyncio.gather(*(_fetch_one(patient_id) for patient_id in unique_ids)))


async def fetch_patient_summaries(patient_ids: list[str]) -> dict[str, dict[str, Any] | None]:
    """Fetch projected Patient summaries through the shared process-local cache."""
    unique_ids = list(dict.fromkeys(patient_ids))
    semaphore = asyncio.Semaphore(_PATIENT_FETCH_MAX_CONCURRENCY)

    async def _fetch_one(patient_id: str) -> tuple[str, dict[str, Any] | None]:
        async with semaphore:
            return patient_id, await _fetch_patient_summary(patient_id)

    return dict(await asyncio.gather(*(_fetch_one(patient_id) for patient_id in unique_ids)))


def questionnaire_reference(questionnaire: dict[str, Any]) -> str | None:
    canonical = questionnaire.get("url")
    if isinstance(canonical, str) and canonical:
        return canonical
    questionnaire_id = questionnaire.get("id")
    if isinstance(questionnaire_id, str) and questionnaire_id:
        return f"Questionnaire/{questionnaire_id}"
    return None


def prefill_questionnaire_response_items(questionnaire_items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    prefilled_items: list[dict[str, Any]] = []
    for item in questionnaire_items or []:
        prefilled_item: dict[str, Any] = {}
        for field in ("linkId", "text", "definition"):
            value = item.get(field)
            if isinstance(value, str) and value:
                prefilled_item[field] = value

        child_items = prefill_questionnaire_response_items(item.get("item"))
        if child_items:
            prefilled_item["item"] = child_items

        if prefilled_item:
            prefilled_items.append(prefilled_item)

    return prefilled_items


async def resolve_questionnaire(job_package: str, job_package_version: str | None) -> dict[str, Any] | BatchJobError:
    params: dict[str, str] = {"name": job_package}
    if job_package_version:
        params["version"] = job_package_version

    questionnaire_bundle = await fhir_get("Questionnaire", params=params)
    if questionnaire_bundle.get("resourceType") == "OperationOutcome":
        return BatchJobError(
            503,
            "transient",
            "Could not resolve Questionnaire for the requested job package.",
        )

    entries = get_bundle_resources(questionnaire_bundle)
    if not entries:
        details = f"No Questionnaire found with name '{job_package}'"
        if job_package_version:
            details += f" and version '{job_package_version}'"
        return BatchJobError(404, "not-found", f"{details}.")

    if len(entries) > 1:
        return BatchJobError(
            409,
            "multiple-matches",
            f"Multiple Questionnaires matched jobPackage '{job_package}'. Specify both jobPackage and jobPackageVersion to select a single Questionnaire.",
        )

    questionnaire = entries[0]
    if questionnaire.get("resourceType") != "Questionnaire":
        return BatchJobError(
            503,
            "transient",
            "FHIR server returned an unexpected resource while resolving the Questionnaire.",
        )
    return questionnaire


async def submit_batch_job(
    body: JobRequest,
    claims: dict,
    current_user: CurrentUser | None = None,
) -> BatchJobSubmission | BatchJobError | MissingConfig:
    missing = required_batch_job_config()
    if missing:
        return MissingConfig(tuple(missing))

    resolved_user = current_user or user_from_claims(claims)
    patient_id: str | None = body.get_param("patientId")
    job_package: str | None = body.get_param("jobPackage")
    job_package_version: str | None = body.get_param("jobPackageVersion")
    job_names = body.get_params("job")
    started_by = resolved_user.sub

    if not patient_id or not job_package:
        return BatchJobError(400, "required", "The 'patientId' and 'jobPackage' parameters must have non-empty values.")
    if not is_valid_fhir_id(patient_id):
        return BatchJobError(400, "invalid", "Invalid patientId. FHIR ids must be 1-64 characters of [A-Za-z0-9-.].")

    questionnaire = await resolve_questionnaire(job_package, job_package_version)
    if isinstance(questionnaire, BatchJobError):
        return questionnaire

    questionnaire_ref = questionnaire_reference(questionnaire)
    if not questionnaire_ref:
        return BatchJobError(
            500,
            "processing",
            "Resolved Questionnaire is missing both url and id, so a QuestionnaireResponse could not be created.",
        )

    batch_id = str(uuid.uuid4())
    response_id = str(uuid.uuid4())
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    response_timestamp = datetime.now(UTC)
    questionnaire_id = questionnaire.get("id")

    if not questionnaire_id:
        return BatchJobError(
            500,
            "processing",
            "Resolved Questionnaire is missing an id, so the batch job could not be started.",
        )

    response_resource = {
        "resourceType": "QuestionnaireResponse",
        "id": response_id,
        "status": "in-progress",
        "questionnaire": questionnaire_ref,
        "subject": {"reference": f"Patient/{patient_id}"},
        "author": build_questionnaire_response_author(resolved_user),
        "meta": {"lastUpdated": response_timestamp.isoformat()},
        "authored": response_timestamp.isoformat(),
        "item": prefill_questionnaire_response_items(questionnaire.get("item")),
    }

    created = await run_db(
        create_batch_job_with_response,
        batch_id,
        patient_id,
        job_package,
        started_by,
        response_id,
        response_resource,
        questionnaire_id,
        questionnaire.get("title") if isinstance(questionnaire.get("title"), str) else None,
        job_package_version,
        job_names or None,
        response_timestamp,
    )
    if not created:
        return BatchJobError(
            500,
            "processing",
            "The batch job and QuestionnaireResponse could not be saved to the database. See logs for details.",
        )

    return BatchJobSubmission(batch_id, response_id, start_time, patient_id, job_package)


def _invalid_date_filter_error(error: InvalidDateFilter) -> BatchJobError:
    return BatchJobError(
        400,
        "value",
        f"Query parameter '{error.parameter_name}' must be a valid ISO date in YYYY-MM-DD format.",
    )


async def list_batch_jobs(
    *,
    include_patient: bool,
    page: int,
    size: int,
    questionnaire_response_status: str | None,
    patient_name_filter: str | None,
    patient_gender: str | None,
    job_package_filter: str | None,
    batch_job_status: str | None,
    dob_start_date: str | None,
    dob_end_date: str | None,
    job_run_start_date: str | None,
    job_run_end_date: str | None,
) -> FHIRJSON | BatchJobError:
    dob_start, error = parse_iso_date_filter(dob_start_date, "dobStartDate")
    if error:
        return _invalid_date_filter_error(error)
    dob_end, error = parse_iso_date_filter(dob_end_date, "dobEndDate")
    if error:
        return _invalid_date_filter_error(error)
    run_start, error = parse_iso_date_filter(job_run_start_date, "jobRunStartDate")
    if error:
        return _invalid_date_filter_error(error)
    run_end, error = parse_iso_date_filter(job_run_end_date, "jobRunEndDate")
    if error:
        return _invalid_date_filter_error(error)
    batch_job_statuses = parse_csv_filter(batch_job_status)
    questionnaire_response_statuses = parse_csv_filter(questionnaire_response_status)
    patient_genders = parse_csv_filter(patient_gender)

    start = page * size
    has_patient_filters = bool(patient_name_filter or patient_genders or dob_start or dob_end)

    if has_patient_filters:
        # Patient filters require external FHIR data, so filter all DB-matched jobs in Python before paging.
        all_jobs, _ = await run_db(
            query_batch_jobs,
            statuses=batch_job_statuses,
            job_package=job_package_filter,
            questionnaire_response_statuses=questionnaire_response_statuses,
            run_start_date=run_start,
            run_end_date=run_end,
        )
        fetch = fetch_patients if include_patient else fetch_patient_summaries
        patients_by_id = await fetch([job.patient_id for job in all_jobs])
        filtered_jobs: list[tuple[BatchJobs, dict[str, Any] | None]] = []
        for job in all_jobs:
            patient = patients_by_id[job.patient_id]
            derived_patient_name = patient_name(patient)
            derived_patient_gender = patient.get("gender") if isinstance(patient, dict) else None
            derived_patient_dob = parse_iso_date(patient.get("birthDate") if isinstance(patient, dict) else None)

            if not matches_filter(derived_patient_name, patient_name_filter, partial=True):
                continue
            if not matches_any_filter(cast(str | None, derived_patient_gender), patient_genders):
                continue
            if not matches_date_range(derived_patient_dob, dob_start, dob_end):
                continue
            filtered_jobs.append((job, patient))

        total = len(filtered_jobs)
        page_jobs = filtered_jobs[start : start + size]
    else:
        selected_jobs, total = await run_db(
            query_batch_jobs,
            statuses=batch_job_statuses,
            job_package=job_package_filter,
            questionnaire_response_statuses=questionnaire_response_statuses,
            run_start_date=run_start,
            run_end_date=run_end,
            limit=size,
            offset=start,
        )
        fetch = fetch_patients if include_patient else fetch_patient_summaries
        patients_by_id = await fetch([job.patient_id for job in selected_jobs])
        page_jobs = [(job, patients_by_id[job.patient_id]) for job in selected_jobs]

    responses_by_batch = await run_db(get_responses_for_batches, [job.batch_id for job, _ in page_jobs])
    results = [
        {
            "resource": to_batch_job_parameters(
                job,
                response=responses_by_batch.get(job.batch_id),
                patient=patient,
                include_patient_resource=include_patient,
            ).model_dump(exclude_none=True)
        }
        for job, patient in page_jobs
    ]

    return cast(
        FHIRJSON,
        {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": total,
            "entry": results,
        },
    )


_TERMINAL_TASK_STATUSES = {"complete", "error", "skipped"}
_TERMINAL_BATCH_STATUSES = {"complete", "partial", "error"}


def with_batch_progress(result_bundle: dict, child_jobs: list[Any], batch_status: str) -> dict:
    """Return a Bundle copy whose status Observation text reflects live task progress."""
    bundle = deepcopy(result_bundle)
    total_jobs = len(child_jobs)
    completed_jobs = sum(1 for child_job in child_jobs if getattr(child_job, "status", None) in _TERMINAL_TASK_STATUSES)
    percent_complete = round((completed_jobs / total_jobs) * 100) if total_jobs else (100 if batch_status in _TERMINAL_BATCH_STATUSES else 0)
    progress_text = f"Batch job status: {percent_complete}% ({completed_jobs}/{total_jobs})"

    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        codings = resource.get("code", {}).get("coding", [])
        if resource.get("id") == "status-observation" or any(coding.get("code") == "result-status" for coding in codings):
            resource.setdefault("valueCodeableConcept", {})["text"] = progress_text
            break

    return bundle


def hydrate_document_entries(bundle: dict, document_texts: dict[str, str]) -> dict:
    """Add persisted text to DocumentReferences that do not already contain content."""
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if not isinstance(resource, dict) or resource.get("resourceType") != "DocumentReference" or "content" in resource:
            continue
        doc_id = resource.get("id")
        if not isinstance(doc_id, str):
            continue
        text = document_texts.get(doc_id)
        if text:
            add_document_text_content(resource, text)
    return bundle


async def get_batch_job_results(batch_id: str) -> FHIRJSON | BatchJobError | StoredErrorBundle:
    job = await run_db(get_batch_job, batch_id)
    if not job:
        return BatchJobError(404, "not-found", f"Batch Job ID {batch_id} was not found.")
    if job.result_bundle and job.result_bundle.get("resourceType") == "Bundle":
        child_jobs = await run_db(get_jobs_for_batch, batch_id)
        bundle = with_batch_progress(job.result_bundle, child_jobs, job.status)
        document_texts = await run_db(get_batch_job_document_texts, batch_id)
        return dict(hydrate_document_entries(bundle, document_texts))
    if job.status in ("pending", "running"):
        return BatchJobError(
            202,
            "informational",
            f"Batch job {batch_id} is still {job.status} and no partial result Bundle is available yet.",
            severity="information",
        )
    if not job.result_bundle:
        return BatchJobError(
            500,
            "transient",
            f"Batch job {batch_id} has no result bundle. The job may have encountered an error - check job status.",
        )
    return StoredErrorBundle(job.result_bundle)


async def get_batch_job_status(batch_id: str, include_patient: bool) -> ParametersResponse | BatchJobError:
    job = await run_db(get_batch_job_summary, batch_id)
    if not job:
        return BatchJobError(404, "not-found", f"Batch Job ID {batch_id} was not found.")
    patient = await _fetch_patient(job.patient_id)
    response = (await run_db(get_responses_for_batches, [batch_id])).get(batch_id)
    return to_batch_job_parameters(job, response=response, patient=patient, include_patient_resource=include_patient)


async def delete_batch_job(batch_id: str) -> JSONResponse:
    """Let the job_state package build and return the deletion response unchanged."""
    return await run_db(delete_batch_job_record, batch_id)
