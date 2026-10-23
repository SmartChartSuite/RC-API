"""Test batch-job HTTP use cases, filtering, and result hydration behavior.

The tests patch `batch_job_service` persistence and patient-lookup seams, and call the pure
`job_package` and `result_bundle` helpers directly; durable persistence is covered by the job_state package tests in
`test_job_state_schema.py`."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from main import app
from src.models.fhir import BundleResource, ParametersResponse
from src.models.job_request import JobRequest, JobRequestParameter
from src.models.job_response import BatchJobAcceptedResponse
from src.routers import batchjob
from src.services import batch_job_service, job_package, result_bundle
from src.util.current_user import CurrentUser
from src.util.settings import settings
from tests.factories import batch_job, expected_author, fake_fetch_patient, patient, response_record


@pytest.fixture
def list_batch_jobs_setup(monkeypatch):
    def setup(
        jobs,
        *,
        responses=None,
        patient_resources=None,
        fetch_calls=None,
        total=None,
    ):
        captured_kwargs: dict[str, Any] = {}

        def fake_query_batch_jobs(**kwargs: Any):
            captured_kwargs.update(kwargs)
            return jobs, len(jobs) if total is None else total

        monkeypatch.setattr(batch_job_service, "query_batch_jobs", fake_query_batch_jobs)
        response_map = dict(responses or {})

        def fake_get_responses_for_batches(batch_ids):
            return {batch_id: response_map[batch_id] for batch_id in batch_ids if batch_id in response_map}

        monkeypatch.setattr(batch_job_service, "get_responses_for_batches", fake_get_responses_for_batches)
        monkeypatch.setattr(
            batch_job_service,
            "_fetch_patient",
            fake_fetch_patient(patient_resources, fetch_calls=fetch_calls),
        )
        monkeypatch.setattr(
            batch_job_service,
            "_fetch_patient_summary",
            fake_fetch_patient(patient_resources, fetch_calls=fetch_calls),
        )
        return captured_kwargs

    return setup


async def test_post_batch_job_schedules_requested_jobs(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {})
    captured: dict = {}

    async def _fake_resolve_questionnaire(job_package, job_package_version):
        return {
            "resourceType": "Questionnaire",
            "id": "questionnaire-123",
            "url": "http://example.org/Questionnaire/questionnaire-123",
            "title": "Syphilis Registry",
            "item": [
                {
                    "linkId": "General",
                    "text": "General",
                    "type": "group",
                    "item": [
                        {
                            "linkId": "q1",
                            "text": "Question 1",
                            "type": "choice",
                        },
                        {
                            "linkId": "nested-group",
                            "text": "Nested Group",
                            "type": "group",
                            "item": [
                                {
                                    "linkId": "q2",
                                    "text": "Question 2",
                                    "type": "string",
                                }
                            ],
                        },
                    ],
                }
            ],
        }

    def _fake_create_batch_job_with_response(
        batch_id,
        patient_id,
        job_package,
        started_by,
        response_id,
        response_body,
        questionnaire_id,
        questionnaire_title,
        job_package_version,
        requested_jobs,
        response_timestamp,
    ):
        captured["batch_id"] = batch_id
        captured["patient_id"] = patient_id
        captured["job_package"] = job_package
        captured["started_by"] = started_by
        captured["response_id"] = response_id
        captured["response_body"] = response_body
        captured["questionnaire_id"] = questionnaire_id
        captured["questionnaire_title"] = questionnaire_title
        captured["job_package_version"] = job_package_version
        captured["requested_jobs"] = requested_jobs
        captured["response_timestamp"] = response_timestamp
        return True

    monkeypatch.setattr(batch_job_service, "resolve_questionnaire", _fake_resolve_questionnaire)
    monkeypatch.setattr(batch_job_service, "create_batch_job_with_response", _fake_create_batch_job_with_response)

    body = JobRequest(
        parameter=[
            JobRequestParameter(name="patientId", valueString="patient-123"),
            JobRequestParameter(name="jobPackage", valueString="SyphilisRegistry"),
            JobRequestParameter(name="job", valueString="SyphilisHistory"),
            JobRequestParameter(name="job", valueString="ig_hc"),
        ]
    )
    response = Response()

    result = await batchjob.post_batch_job(
        body,
        response,
        claims={"sub": "user-123"},
        current_user=CurrentUser(sub="user-123", name="Named User"),
    )

    assert isinstance(result, BatchJobAcceptedResponse)
    assert not isinstance(result, JSONResponse)
    values = {param.name: param for param in result.parameter}
    questionnaire_response_reference = values["batchJobQuestionnaireResponse"].valueReference
    assert values["batchJobStatus"].valueString == "pending"
    assert questionnaire_response_reference is not None
    assert questionnaire_response_reference["reference"].startswith("QuestionnaireResponse/")
    assert captured["response_body"] == {
        "resourceType": "QuestionnaireResponse",
        "id": captured["response_id"],
        "status": "in-progress",
        "questionnaire": "http://example.org/Questionnaire/questionnaire-123",
        "subject": {"reference": "Patient/patient-123"},
        "author": expected_author("Named User", "user-123"),
        "meta": {"lastUpdated": captured["response_timestamp"].isoformat()},
        "authored": captured["response_timestamp"].isoformat(),
        "item": [
            {
                "linkId": "General",
                "text": "General",
                "item": [
                    {"linkId": "q1", "text": "Question 1"},
                    {
                        "linkId": "nested-group",
                        "text": "Nested Group",
                        "item": [
                            {"linkId": "q2", "text": "Question 2"},
                        ],
                    },
                ],
            }
        ],
    }
    assert captured["questionnaire_id"] == "questionnaire-123"
    assert captured["questionnaire_title"] == "Syphilis Registry"
    assert captured["job_package_version"] is None
    assert captured["requested_jobs"] == ["SyphilisHistory", "ig_hc"]
    assert captured["response_timestamp"].isoformat() == captured["response_body"]["meta"]["lastUpdated"]
    assert captured["response_timestamp"].isoformat() == captured["response_body"]["authored"]
    assert response.headers["Location"].startswith("/batchjob/")


async def test_post_batch_job_rejects_multiple_questionnaire_matches(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {})

    async def _fake_resolve_questionnaire(job_package, job_package_version):
        return batch_job_service.BatchJobError(
            409,
            "multiple-matches",
            "Multiple Questionnaires matched jobPackage 'SyphilisRegistry'. Specify both jobPackage and jobPackageVersion to select a single Questionnaire.",
        )

    monkeypatch.setattr(batch_job_service, "resolve_questionnaire", _fake_resolve_questionnaire)
    monkeypatch.setattr(batch_job_service, "create_batch_job_with_response", lambda *args: True)

    body = JobRequest(
        parameter=[
            JobRequestParameter(name="patientId", valueString="patient-123"),
            JobRequestParameter(name="jobPackage", valueString="SyphilisRegistry"),
        ]
    )

    response = Response()

    result = await batchjob.post_batch_job(body, response, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 409


def test_filter_requested_jobs_supports_multiple_job_parameters():
    cql_names = ["SyphilisHistory", "WeightHistory"]
    prompt_paths = [
        "prompts/2025_08/syphilis/ig_hc",
        "prompts/2025_08/syphilis/ig_lt",
    ]

    filtered_cql, filtered_prompts = job_package.filter_requested_jobs(
        cql_names,
        prompt_paths,
        ["SyphilisHistory", "ig_hc"],
    )

    assert filtered_cql == ["SyphilisHistory"]
    assert filtered_prompts == ["prompts/2025_08/syphilis/ig_hc"]


def test_filter_requested_jobs_rejects_unknown_names():
    cql_names = ["SyphilisHistory"]
    prompt_paths = ["prompts/2025_08/syphilis/ig_hc"]

    try:
        job_package.filter_requested_jobs(cql_names, prompt_paths, ["missing-job"])
    except ValueError as exc:
        assert "missing-job" in str(exc)
    else:
        raise AssertionError("Expected unknown requested job name to raise ValueError")


def test_list_batch_jobs_rejects_size_over_limit(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_enabled", False)
    response = TestClient(app).get("/batchjob?size=101")

    assert response.status_code == 400
    assert response.json()["resourceType"] == "OperationOutcome"


async def test_list_batch_jobs_returns_fhir_parameters(list_batch_jobs_setup):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    completed_at = datetime(2026, 5, 22, 12, 5, tzinfo=UTC)
    fetch_calls: list[str] = []
    jobs = [
        batch_job(
            batch_id="batch-123",
            job_package="SyphilisRegistry",
            started_by="user-123",
            status="complete",
            created_at=created_at,
            completed_at=completed_at,
            questionnaire_title="Syphilis Registry",
        ),
        batch_job(
            batch_id="batch-456",
            job_package="SyphilisRegistry",
            started_by="user-234",
            status="running",
            created_at=created_at,
        ),
    ]
    list_batch_jobs_setup(
        jobs,
        total=2,
        responses={
            batch_id: response_record(
                batch_job_id=batch_id,
                response={"status": "in-progress", "meta": {"lastUpdated": "2026-05-22T12:00:00+00:00"}},
            )
            for batch_id in ("batch-123", "batch-456")
        },
        patient_resources={
            "patient-123": patient(
                name=[{"family": "Doe", "given": ["Jane"]}],
                birth_date="2020-01-01",
                gender="female",
            )
        },
        fetch_calls=fetch_calls,
    )

    result = await batchjob.list_batch_jobs(include_patient=True, claims={})

    assert isinstance(result, dict)
    bundle = BundleResource.model_validate(result)
    assert bundle.resourceType == "Bundle"
    assert bundle.type == "searchset"
    assert bundle.total == 2
    assert bundle.entry is not None
    assert len(bundle.entry) == 2
    first = ParametersResponse.model_validate(bundle.entry[0].resource)
    values = {param.name: param for param in first.parameter}
    assert values["batchId"].valueString == "batch-123"
    assert values["patientId"].valueString == "patient-123"
    assert values["jobPackage"].valueString == "SyphilisRegistry"
    assert values["jobPackageTitle"].valueString == "Syphilis Registry"
    assert values["startedBy"].valueString == "user-123"
    assert values["batchJobStatus"].valueString == "complete"
    assert values["questionnaireResponseStatus"].valueString == "in-progress"
    assert values["responseLastUpdated"].valueDateTime == "2026-05-22T12:00:00+00:00"
    assert values["batchJobQuestionnaireResponse"].valueReference == {"reference": "QuestionnaireResponse/response-123"}
    assert values["patientName"].valueString == "Doe, Jane"
    assert values["patientDob"].valueDate == "2020-01-01"
    assert values["patientGender"].valueCode == "female"
    assert values["jobStartDateTime"].valueDateTime == created_at.isoformat()
    assert values["jobCompletedDateTime"].valueDateTime == completed_at.isoformat()
    assert values["patientResource"].resource == {
        "resourceType": "Patient",
        "id": "patient-123",
        "name": [{"family": "Doe", "given": ["Jane"]}],
        "birthDate": "2020-01-01",
        "gender": "female",
    }
    assert fetch_calls == ["patient-123"]


async def test_list_batch_jobs_uses_summary_fetch_without_full_patient_resource(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    calls: list[str] = []
    jobs = [batch_job(batch_id="batch-123", patient_id="patient-123", created_at=created_at)]
    monkeypatch.setattr(batch_job_service, "query_batch_jobs", lambda **kwargs: (jobs, 1))
    monkeypatch.setattr(batch_job_service, "get_responses_for_batches", lambda batch_ids: {})

    async def fetch_summary(patient_ids):
        calls.append("summary")
        return {patient_id: patient(patient_id=patient_id) for patient_id in patient_ids}

    async def fetch_full(patient_ids):
        calls.append("full")
        raise AssertionError("full Patient fetch should not be used")

    monkeypatch.setattr(batch_job_service, "fetch_patient_summaries", fetch_summary)
    monkeypatch.setattr(batch_job_service, "fetch_patients", fetch_full)

    result = await batch_job_service.list_batch_jobs(
        include_patient=False,
        page=0,
        size=10,
        questionnaire_response_status=None,
        patient_name_filter=None,
        patient_gender=None,
        job_package_filter=None,
        batch_job_status=None,
        dob_start_date=None,
        dob_end_date=None,
        job_run_start_date=None,
        job_run_end_date=None,
    )

    assert isinstance(result, dict)
    assert calls == ["summary"]


async def test_list_batch_jobs_bypasses_summary_fetch_when_full_patient_requested(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    calls: list[str] = []
    jobs = [batch_job(batch_id="batch-123", patient_id="patient-123", created_at=created_at)]
    monkeypatch.setattr(batch_job_service, "query_batch_jobs", lambda **kwargs: (jobs, 1))
    monkeypatch.setattr(batch_job_service, "get_responses_for_batches", lambda batch_ids: {})

    async def fetch_summary(patient_ids):
        calls.append("summary")
        raise AssertionError("summary fetch should not be used")

    async def fetch_full(patient_ids):
        calls.append("full")
        return {patient_id: patient(patient_id=patient_id) for patient_id in patient_ids}

    monkeypatch.setattr(batch_job_service, "fetch_patient_summaries", fetch_summary)
    monkeypatch.setattr(batch_job_service, "fetch_patients", fetch_full)

    result = await batch_job_service.list_batch_jobs(
        include_patient=True,
        page=0,
        size=10,
        questionnaire_response_status=None,
        patient_name_filter=None,
        patient_gender=None,
        job_package_filter=None,
        batch_job_status=None,
        dob_start_date=None,
        dob_end_date=None,
        job_run_start_date=None,
        job_run_end_date=None,
    )

    assert isinstance(result, dict)
    assert calls == ["full"]


async def test_stream_batch_jobs_emits_initial_bundle_without_waiting():
    bundle = {"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []}

    class Request:
        async def is_disconnected(self):
            return True

    stream = batchjob._stream_batch_jobs(Request(), bundle, {})
    first = await anext(stream)

    assert first.startswith("event: form-list\n")
    assert '"resourceType":"Bundle"' in first


async def test_stream_batch_jobs_emits_keepalive_when_bundle_is_unchanged(monkeypatch):
    bundle = {"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []}
    monkeypatch.setattr(settings, "batch_job_stream_poll_interval_seconds", 0)

    async def list_batches(**kwargs):
        return bundle

    monkeypatch.setattr(batch_job_service, "list_batch_jobs", list_batches)

    class Request:
        calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 2

    stream = batchjob._stream_batch_jobs(Request(), bundle, {})

    assert (await anext(stream)).startswith("event: form-list\n")
    assert await anext(stream) == ": keepalive\n\n"


async def test_stream_batch_jobs_emits_new_event_when_bundle_changes(monkeypatch):
    first_bundle = {"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []}
    second_bundle = {"resourceType": "Bundle", "type": "searchset", "total": 1, "entry": []}
    monkeypatch.setattr(settings, "batch_job_stream_poll_interval_seconds", 0)
    calls = 0

    async def list_batches(**kwargs):
        nonlocal calls
        calls += 1
        return second_bundle if calls == 1 else first_bundle

    monkeypatch.setattr(batch_job_service, "list_batch_jobs", list_batches)

    class Request:
        calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 2

    stream = batchjob._stream_batch_jobs(Request(), first_bundle, {})

    assert (await anext(stream)).startswith("event: form-list\n")
    changed = await anext(stream)
    assert changed.startswith("event: form-list\n")
    assert '"total":1' in changed


def test_stream_batch_jobs_rejects_full_patient_resource_request(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_enabled", False)
    response = TestClient(app).get("/batchjob/stream?include_patient=true")

    assert response.status_code == 400
    assert response.json()["resourceType"] == "OperationOutcome"
    assert response.json()["issue"][0]["code"] == "not-supported"


async def test_list_batch_jobs_applies_page_and_size(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    fetch_calls: list[str] = []
    monkeypatch.setattr(
        batch_job_service,
        "query_batch_jobs",
        lambda **kwargs: (
            [
                batch_job(
                    batch_id=f"batch-{index}",
                    patient_id=f"patient-{index}",
                    job_package="ExampleRegistry",
                    started_by="user-123",
                    status="complete",
                    result_bundle=None,
                    created_at=created_at,
                    completed_at=None,
                )
                for index in range(5)
            ][kwargs["offset"] : kwargs["offset"] + kwargs["limit"]],
            5,
        ),
    )
    monkeypatch.setattr(batch_job_service, "get_responses_for_batches", lambda batch_ids: {})

    monkeypatch.setattr(batch_job_service, "_fetch_patient", fake_fetch_patient(fetch_calls=fetch_calls))
    monkeypatch.setattr(batch_job_service, "_fetch_patient_summary", fake_fetch_patient(fetch_calls=fetch_calls))

    result = await batchjob.list_batch_jobs(page=1, size=2, claims={})

    assert isinstance(result, dict)
    bundle = BundleResource.model_validate(result)
    assert bundle.total == 5
    assert bundle.entry is not None
    assert [next(param["valueString"] for param in entry.resource["parameter"] if param["name"] == "batchId") for entry in bundle.entry] == ["batch-2", "batch-3"]
    assert fetch_calls == ["patient-2", "patient-3"]


async def test_list_batch_jobs_uses_one_bulk_response_load_for_page(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    loader_calls: list[list[str]] = []
    jobs = [
        batch_job(
            batch_id=f"batch-{index}",
            patient_id=f"patient-{index}",
            job_package="ExampleRegistry",
            started_by="user-123",
            status="complete",
            result_bundle=None,
            created_at=created_at,
            completed_at=None,
        )
        for index in range(3)
    ]
    monkeypatch.setattr(batch_job_service, "query_batch_jobs", lambda **kwargs: (jobs, len(jobs)))

    def _fake_get_responses_for_batches(batch_ids):
        loader_calls.append(list(batch_ids))
        return {
            batch_id: response_record(
                response_id=f"response-{batch_id}",
                batch_job_id=batch_id,
                response={"status": f"status-{batch_id}"},
            )
            for batch_id in batch_ids
        }

    monkeypatch.setattr(batch_job_service, "get_responses_for_batches", _fake_get_responses_for_batches)

    monkeypatch.setattr(batch_job_service, "_fetch_patient", fake_fetch_patient())
    monkeypatch.setattr(batch_job_service, "_fetch_patient_summary", fake_fetch_patient())

    result = await batchjob.list_batch_jobs(size=3, claims={})

    assert isinstance(result, dict)
    bundle = BundleResource.model_validate(result)
    assert bundle.entry is not None
    assert loader_calls == [["batch-0", "batch-1", "batch-2"]]
    for entry, batch_id in zip(bundle.entry, ("batch-0", "batch-1", "batch-2"), strict=True):
        values = {param.name: param for param in ParametersResponse.model_validate(entry.resource).parameter}
        assert values["questionnaireResponseStatus"].valueString == f"status-{batch_id}"
        assert values["batchJobQuestionnaireResponse"].valueReference == {"reference": f"QuestionnaireResponse/response-{batch_id}"}


async def test_list_batch_jobs_includes_progress_for_pending_running_and_partial(list_batch_jobs_setup, monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    jobs = [
        batch_job(batch_id="running", patient_id="patient-running", status="running", created_at=created_at),
        batch_job(batch_id="partial", patient_id="patient-partial", status="partial", created_at=created_at),
        batch_job(batch_id="complete", patient_id="patient-complete", status="complete", created_at=created_at),
        batch_job(batch_id="pending", patient_id="patient-pending", status="pending", created_at=created_at),
    ]
    monkeypatch.setattr(batch_job_service, "query_batch_jobs", lambda **kwargs: (jobs, len(jobs)))
    monkeypatch.setattr(batch_job_service, "get_responses_for_batches", lambda batch_ids: {})

    async def fetch_summaries(patient_ids):
        return {patient_id: patient(patient_id=patient_id) for patient_id in patient_ids}

    monkeypatch.setattr(batch_job_service, "fetch_patient_summaries", fetch_summaries)
    monkeypatch.setattr(batch_job_service, "get_job_progress_for_batches", lambda batch_ids: {batch_id: 67 for batch_id in batch_ids})

    result = await batch_job_service.list_batch_jobs(
        include_patient=False,
        page=0,
        size=10,
        questionnaire_response_status=None,
        patient_name_filter=None,
        patient_gender=None,
        job_package_filter=None,
        batch_job_status=None,
        dob_start_date=None,
        dob_end_date=None,
        job_run_start_date=None,
        job_run_end_date=None,
    )

    assert isinstance(result, dict)
    progress_by_batch = {}
    for entry in result["entry"]:
        values = {param["name"]: param for param in entry["resource"]["parameter"]}
        progress_by_batch[values["batchId"]["valueString"]] = values.get("batchJobProgress", {}).get("valueInteger")
    assert progress_by_batch == {"running": 67, "partial": 67, "complete": None, "pending": 67}


async def test_list_batch_jobs_applies_search_filters(list_batch_jobs_setup):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    captured_kwargs = list_batch_jobs_setup(
        [
            batch_job(
                batch_id="batch-match",
                patient_id="patient-match",
                status="complete",
                created_at=created_at,
            ),
            batch_job(
                batch_id="batch-skip",
                patient_id="patient-skip",
                job_package="OtherRegistry",
                status="running",
                created_at=created_at,
            ),
        ],
        responses={
            "batch-match": response_record(batch_job_id="batch-match", response={"status": "completed"}),
            "batch-skip": response_record(batch_job_id="batch-skip", response={"status": "in-progress"}),
        },
        patient_resources={
            "patient-match": patient(
                patient_id="patient-match",
                name=[{"family": "Doe", "given": ["Jane"]}],
                birth_date="2020-01-01",
                gender="female",
            ),
            "patient-skip": patient(
                patient_id="patient-skip",
                name=[{"family": "Smith", "given": ["John"]}],
                birth_date="2020-01-01",
                gender="male",
            ),
        },
    )

    result = await batchjob.list_batch_jobs(
        questionnaire_response_status="completed",
        patient_name="doe",
        patient_gender="female",
        job_package_filter="ExampleRegistry",
        batch_job_status="complete",
        claims={},
    )

    assert isinstance(result, dict)
    bundle = BundleResource.model_validate(result)
    assert bundle.total == 1
    assert bundle.entry is not None
    assert captured_kwargs["statuses"] == ["complete"]
    assert captured_kwargs["questionnaire_response_statuses"] == ["completed"]
    values = {param.name: param for param in ParametersResponse.model_validate(bundle.entry[0].resource).parameter}
    assert values["batchId"].valueString == "batch-match"


@pytest.mark.parametrize(
    ("batch_job_status", "questionnaire_response_status", "expected_statuses", "expected_response_statuses"),
    [
        ("pending, running , complete, partial", "completed, in-progress", ["pending", "running", "complete", "partial"], ["completed", "in-progress"]),
        ("complete", "completed", ["complete"], ["completed"]),
    ],
)
async def test_list_batch_jobs_forwards_status_filters(
    list_batch_jobs_setup,
    batch_job_status,
    questionnaire_response_status,
    expected_statuses,
    expected_response_statuses,
):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    captured_kwargs = list_batch_jobs_setup(
        [batch_job(batch_id="batch-match", patient_id="patient-match", status="running", created_at=created_at)],
        responses={"batch-match": response_record(batch_job_id="batch-match", response={"status": "in-progress"})},
    )

    result = await batchjob.list_batch_jobs(
        batch_job_status=batch_job_status,
        questionnaire_response_status=questionnaire_response_status,
        claims={},
    )

    bundle = BundleResource.model_validate(result)
    assert bundle.total == 1
    assert captured_kwargs["statuses"] == expected_statuses
    assert captured_kwargs["questionnaire_response_statuses"] == expected_response_statuses


async def test_list_batch_jobs_supports_comma_separated_patient_gender_filter(list_batch_jobs_setup):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    list_batch_jobs_setup(
        [
            batch_job(batch_id="batch-match", patient_id="patient-match", status="running", created_at=created_at),
            batch_job(batch_id="batch-skip", patient_id="patient-skip", status="running", created_at=created_at),
        ],
        patient_resources={
            "patient-match": patient(patient_id="patient-match", gender="female"),
            "patient-skip": patient(patient_id="patient-skip", gender="unknown"),
        },
    )

    result = await batchjob.list_batch_jobs(
        patient_gender="male, female , other",
        claims={},
    )

    bundle = BundleResource.model_validate(result)
    assert bundle.total == 1
    assert bundle.entry is not None
    values = {param.name: param for param in ParametersResponse.model_validate(bundle.entry[0].resource).parameter}
    assert values["batchId"].valueString == "batch-match"


async def test_list_batch_jobs_applies_inclusive_date_filters(list_batch_jobs_setup):
    matching_created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    nonmatching_created_at = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    list_batch_jobs_setup(
        [
            batch_job(batch_id="batch-match", patient_id="patient-match", created_at=matching_created_at),
            batch_job(batch_id="batch-skip", patient_id="patient-skip", created_at=nonmatching_created_at),
        ],
        patient_resources={
            "patient-match": patient(patient_id="patient-match", birth_date="2020-01-01"),
            "patient-skip": patient(patient_id="patient-skip", birth_date="2020-02-01"),
        },
    )

    result = await batchjob.list_batch_jobs(
        dob_start_date="2020-01-01",
        dob_end_date="2020-01-01",
        job_run_start_date="2026-05-22",
        job_run_end_date="2026-05-22",
        claims={},
    )

    assert isinstance(result, dict)
    bundle = BundleResource.model_validate(result)
    assert bundle.total == 1
    assert bundle.entry is not None
    values = {param.name: param for param in ParametersResponse.model_validate(bundle.entry[0].resource).parameter}
    assert values["batchId"].valueString == "batch-match"


async def test_list_batch_jobs_rejects_invalid_date_filters(monkeypatch):
    monkeypatch.setattr(batch_job_service, "query_batch_jobs", lambda **kwargs: ([], 0))

    result = await batchjob.list_batch_jobs(dob_start_date="01-01-2020", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


def test_hydration_matches_pre_change_bundle_shape():
    documents = [{"id": "doc-1", "type": "Note", "date": "2026-05-22", "text": "plain text"}]
    full_entries = result_bundle.build_document_entries(documents, "patient-123", "batch-123")
    stored_bundle = result_bundle.build_result_bundle(None, [], full_entries, "preliminary", batch_id="batch-123")
    pre_change_bundle = deepcopy(stored_bundle)
    pre_change_bundle["entry"] = [stored_bundle["entry"][0], *full_entries]

    hydrated = batch_job_service.hydrate_document_entries(deepcopy(stored_bundle), {"doc-1": "plain text"})

    assert hydrated == pre_change_bundle


@pytest.mark.parametrize("batch_status", ["running", "complete"])
async def test_get_batch_job_results_hydrates_one_document_query_for_running_and_complete(monkeypatch, batch_status):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    stored_bundle = {
        "resourceType": "Bundle",
        "id": "bundle-123",
        "type": "collection",
        "total": 2,
        "entry": [
            {
                "fullUrl": "Observation/status-observation",
                "resource": {"resourceType": "Observation", "id": "status-observation", "code": {"coding": [{"code": "result-status"}]}},
            },
            {
                "fullUrl": "DocumentReference/doc-1",
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "doc-1",
                    "status": "current",
                    "type": {"text": "Note"},
                    "subject": {"reference": "Patient/patient-123"},
                    "date": "2026-05-22",
                },
            },
        ],
    }
    document_queries: list[str] = []
    monkeypatch.setattr(
        batch_job_service,
        "get_batch_job",
        lambda batch_id: batch_job(
            batch_id=batch_id,
            patient_id="patient-123",
            job_package="Registry",
            started_by="user-123",
            status=batch_status,
            result_bundle=stored_bundle,
            created_at=created_at,
            completed_at=created_at if batch_status == "complete" else None,
        ),
    )
    monkeypatch.setattr(batch_job_service, "get_jobs_for_batch", lambda batch_id: [])
    monkeypatch.setattr(batch_job_service, "get_batch_job_document_texts", lambda batch_id: document_queries.append(batch_id) or {"doc-1": "plain text"})

    result = await batchjob.get_batch_job_results("batch-123", claims={})

    assert isinstance(result, dict)
    result_entries = result.get("entry")
    assert result_entries is not None
    assert result_entries[1]["resource"]["content"] == [{"attachment": {"contentType": "text/plain", "data": "cGxhaW4gdGV4dA=="}}]
    assert document_queries == ["batch-123"]
    assert "content" not in stored_bundle["entry"][1]["resource"]


def test_hydration_preserves_legacy_inline_content_and_missing_rows():
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "fullUrl": "DocumentReference/legacy",
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "legacy",
                    "content": [{"attachment": {"contentType": "text/plain", "data": "b2xk"}}],
                },
            },
            {"fullUrl": "DocumentReference/missing", "resource": {"resourceType": "DocumentReference", "id": "missing"}},
        ],
    }
    original = deepcopy(bundle)

    hydrated = batch_job_service.hydrate_document_entries(bundle, {"legacy": "new text"})

    assert hydrated == original


@pytest.mark.parametrize(
    ("batch_status", "child_statuses", "expected_progress"),
    [
        ("running", ["complete", "running", "skipped"], "Batch job status: 33% (1/3)"),
        ("partial", ["complete", "error"], "Batch job status: 50% (1/2)"),
        ("partial", ["complete", "complete", "error"], "Batch job status: 67% (2/3)"),
    ],
)
async def test_get_batch_job_results_returns_stored_bundle_with_live_progress(monkeypatch, batch_status, child_statuses, expected_progress):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    stored_bundle = {
        "resourceType": "Bundle",
        "id": "bundle-123",
        "type": "collection",
        "total": 2,
        "entry": [
            {
                "fullUrl": "Observation/status-observation",
                "resource": {"resourceType": "Observation", "id": "status-observation", "status": "preliminary", "code": {"coding": [{"code": "result-status"}]}},
            },
            {
                "fullUrl": "Observation/obs-1",
                "resource": {"resourceType": "Observation", "id": "obs-1", "status": "final", "code": {"coding": [{"code": "answer"}]}},
            },
        ],
    }
    monkeypatch.setattr(
        batch_job_service,
        "get_batch_job",
        lambda batch_id: batch_job(
            batch_id=batch_id,
            patient_id="patient-123",
            job_package="Registry",
            started_by="user-123",
            status=batch_status,
            result_bundle=stored_bundle,
            created_at=created_at,
            completed_at=None if batch_status == "running" else created_at,
        ),
    )
    monkeypatch.setattr(batch_job_service, "get_jobs_for_batch", lambda batch_id: [type("Job", (), {"status": status})() for status in child_statuses])

    result = await batchjob.get_batch_job_results("batch-123", claims={})

    assert isinstance(result, dict)
    validated = BundleResource.model_validate(result)
    assert validated.model_dump()["id"] == "bundle-123"
    assert validated.entry is not None
    assert validated.entry[0].resource["valueCodeableConcept"]["text"] == expected_progress
    assert "valueCodeableConcept" not in stored_bundle["entry"][0]["resource"]


def test_with_batch_progress_reports_complete_zero_task_batch():
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "status-observation",
                    "code": {"coding": [{"code": "result-status"}]},
                    "valueCodeableConcept": {"coding": [{"code": "complete"}]},
                }
            }
        ],
    }

    result = batch_job_service.with_batch_progress(bundle, [], "complete")

    assert result["entry"][0]["resource"]["valueCodeableConcept"]["text"] == "Batch job status: 100% (0/0)"


async def test_get_batch_job_results_returns_202_when_running_without_snapshot(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        batch_job_service,
        "get_batch_job",
        lambda batch_id: batch_job(
            batch_id=batch_id,
            patient_id="patient-123",
            job_package="Registry",
            started_by="user-123",
            status="running",
            result_bundle=None,
            created_at=created_at,
            completed_at=None,
        ),
    )

    result = await batchjob.get_batch_job_results("batch-123", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 202


async def test_get_batch_job_status_returns_fhir_parameters(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    completed_at = datetime(2026, 5, 22, 12, 5, tzinfo=UTC)
    monkeypatch.setattr(
        batch_job_service,
        "get_batch_job_summary",
        lambda batch_id: batch_job(
            batch_id=batch_id,
            patient_id="patient-123",
            job_package="SyphilisRegistry",
            started_by="user-123",
            status="complete",
            result_bundle=None,
            created_at=created_at,
            completed_at=completed_at,
        ),
    )
    monkeypatch.setattr(
        batch_job_service,
        "get_responses_for_batches",
        lambda batch_ids: {batch_id: response_record(batch_job_id=batch_id, response={"status": "completed"}) for batch_id in batch_ids},
    )

    monkeypatch.setattr(
        batch_job_service,
        "_fetch_patient",
        fake_fetch_patient(
            {
                "patient-123": patient(
                    name=[{"family": "Doe", "given": ["Jane"]}],
                    birth_date="2020-01-01",
                    gender="female",
                )
            }
        ),
    )

    result = await batchjob.get_batch_job_status("batch-123", include_patient=True, claims={})

    assert isinstance(result, ParametersResponse)
    values = {param.name: param for param in result.parameter}
    assert values["batchId"].valueString == "batch-123"
    assert values["patientId"].valueString == "patient-123"
    assert values["jobPackage"].valueString == "SyphilisRegistry"
    assert values["startedBy"].valueString == "user-123"
    assert values["batchJobStatus"].valueString == "complete"
    assert values["questionnaireResponseStatus"].valueString == "completed"
    assert values["batchJobQuestionnaireResponse"].valueReference == {"reference": "QuestionnaireResponse/response-123"}
    assert values["patientName"].valueString == "Doe, Jane"
    assert values["patientDob"].valueDate == "2020-01-01"
    assert values["patientGender"].valueCode == "female"
    assert values["jobStartDateTime"].valueDateTime == created_at.isoformat()
    assert values["jobCompletedDateTime"].valueDateTime == completed_at.isoformat()
    assert values["patientResource"].resource == {
        "resourceType": "Patient",
        "id": "patient-123",
        "name": [{"family": "Doe", "given": ["Jane"]}],
        "birthDate": "2020-01-01",
        "gender": "female",
    }


async def test_post_batch_job_rejects_invalid_patient_id(monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {})

    async def _must_not_resolve(*args):
        raise AssertionError("questionnaire resolution must not run for an invalid patientId")

    monkeypatch.setattr(batch_job_service, "resolve_questionnaire", _must_not_resolve)
    body = JobRequest(
        parameter=[
            JobRequestParameter(name="patientId", valueString="../DocumentReference?subject=x"),
            JobRequestParameter(name="jobPackage", valueString="SyphilisRegistry"),
        ]
    )

    result = await batchjob.post_batch_job(body, Response(), claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
