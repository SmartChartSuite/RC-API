from datetime import datetime, timezone

from fastapi import BackgroundTasks, Response
from fastapi.responses import JSONResponse

from src.models.fhir import BundleResource, ParametersResponse
from src.models.job_request import JobRequest
from src.models.job_request import JobRequestParameter
from src.models.job_response import BatchJobAcceptedResponse
from src.routers import batchjob
from src.services import job_orchestrator
from src.services.job_state import BatchJobs


async def test_post_batch_job_schedules_requested_jobs(monkeypatch):
    monkeypatch.setattr(batchjob, "config_errors", {})
    captured: dict = {}

    async def _fake_resolve_questionnaire(job_package, job_package_version):
        return {
            "resourceType": "Questionnaire",
            "id": "questionnaire-123",
            "url": "http://example.org/Questionnaire/questionnaire-123",
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

    def _fake_create_batch_job_with_response(batch_id, patient_id, job_package, started_by, response_id, response_body):
        captured["batch_id"] = batch_id
        captured["patient_id"] = patient_id
        captured["job_package"] = job_package
        captured["started_by"] = started_by
        captured["response_id"] = response_id
        captured["response_body"] = response_body
        return True

    monkeypatch.setattr(batchjob, "_resolve_questionnaire", _fake_resolve_questionnaire)
    monkeypatch.setattr(batchjob, "create_batch_job_with_response", _fake_create_batch_job_with_response)

    body = JobRequest(
        parameter=[
            JobRequestParameter(name="patientId", valueString="patient-123"),
            JobRequestParameter(name="jobPackage", valueString="SyphilisRegistry"),
            JobRequestParameter(name="job", valueString="SyphilisHistory"),
            JobRequestParameter(name="job", valueString="ig_hc"),
        ]
    )
    background_tasks = BackgroundTasks()
    response = Response()

    result = await batchjob.post_batch_job(body, background_tasks, response, claims={"sub": "user-123"})

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
    assert response.headers["Location"].startswith("/batchjob/")
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is batchjob.run_batch_job
    assert task.args[1] == "patient-123"
    assert task.args[2] == "SyphilisRegistry"
    assert task.args[3] == "questionnaire-123"
    assert task.args[4] is None
    assert task.args[5] == ["SyphilisHistory", "ig_hc"]


async def test_post_batch_job_rejects_multiple_questionnaire_matches(monkeypatch):
    monkeypatch.setattr(batchjob, "config_errors", {})

    async def _fake_resolve_questionnaire(job_package, job_package_version):
        return JSONResponse(
            status_code=409,
            content={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "multiple-matches",
                        "diagnostics": "Multiple Questionnaires matched jobPackage 'SyphilisRegistry'. Specify both jobPackage and jobPackageVersion to select a single Questionnaire.",
                    }
                ],
            },
        )

    monkeypatch.setattr(batchjob, "_resolve_questionnaire", _fake_resolve_questionnaire)
    monkeypatch.setattr(batchjob, "create_batch_job_with_response", lambda *args: True)

    body = JobRequest(
        parameter=[
            JobRequestParameter(name="patientId", valueString="patient-123"),
            JobRequestParameter(name="jobPackage", valueString="SyphilisRegistry"),
        ]
    )

    background_tasks = BackgroundTasks()
    response = Response()

    result = await batchjob.post_batch_job(body, background_tasks, response, claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 409
    assert len(background_tasks.tasks) == 0


def test_filter_requested_jobs_supports_multiple_job_parameters():
    cql_names = ["SyphilisHistory", "WeightHistory"]
    prompt_paths = [
        "prompts/2025_08/syphilis/ig_hc",
        "prompts/2025_08/syphilis/ig_lt",
    ]

    filtered_cql, filtered_prompts = job_orchestrator._filter_requested_jobs(
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
        job_orchestrator._filter_requested_jobs(cql_names, prompt_paths, ["missing-job"])
    except ValueError as exc:
        assert "missing-job" in str(exc)
    else:
        raise AssertionError("Expected unknown requested job name to raise ValueError")


async def test_list_batch_jobs_returns_fhir_parameters(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 5, 22, 12, 5, tzinfo=timezone.utc)
    fetch_calls: list[str] = []
    monkeypatch.setattr(
        batchjob,
        "query_batch_jobs",
        lambda **kwargs: [
            BatchJobs(
                batch_id="batch-123",
                patient_id="patient-123",
                job_package="SyphilisRegistry",
                started_by="user-123",
                status="complete",
                result_bundle=None,
                created_at=created_at,
                completed_at=completed_at,
            ),
            BatchJobs(
                batch_id="batch-456",
                patient_id="patient-123",
                job_package="SyphilisRegistry",
                started_by="user-234",
                status="running",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            ),
        ],
    )
    monkeypatch.setattr(
        batchjob,
        "get_responses",
        lambda batch_job_id=None, job_package=None: [type("ResponseRecord", (), {"response_id": "response-123", "response": {"status": "in-progress"}})()],
    )

    async def _fake_fetch_patient(patient_id):
        fetch_calls.append(patient_id)
        return {
            "resourceType": "Patient",
            "id": patient_id,
            "name": [{"family": "Doe", "given": ["Jane"]}],
            "birthDate": "2020-01-01",
            "gender": "female",
        }

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

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
    assert values["startedBy"].valueString == "user-123"
    assert values["batchJobStatus"].valueString == "complete"
    assert values["questionnaireResponseStatus"].valueString == "in-progress"
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


async def test_list_batch_jobs_applies_page_and_size(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    fetch_calls: list[str] = []
    monkeypatch.setattr(
        batchjob,
        "query_batch_jobs",
        lambda **kwargs: [
            BatchJobs(
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
        ],
    )
    monkeypatch.setattr(batchjob, "get_responses", lambda batch_job_id=None, job_package=None: [])

    async def _fake_fetch_patient(patient_id):
        fetch_calls.append(patient_id)
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

    result = await batchjob.list_batch_jobs(page=1, size=2, claims={})

    assert isinstance(result, dict)
    bundle = BundleResource.model_validate(result)
    assert bundle.total == 5
    assert bundle.entry is not None
    assert [next(param["valueString"] for param in entry.resource["parameter"] if param["name"] == "batchId") for entry in bundle.entry] == ["batch-2", "batch-3"]
    assert fetch_calls == ["patient-2", "patient-3"]


async def test_list_batch_jobs_applies_search_filters(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    captured_kwargs = {}

    def _fake_query_batch_jobs(**kwargs):
        captured_kwargs.update(kwargs)
        return [
            BatchJobs(
                batch_id="batch-match",
                patient_id="patient-match",
                job_package="ExampleRegistry",
                started_by="user-123",
                status="complete",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            ),
            BatchJobs(
                batch_id="batch-skip",
                patient_id="patient-skip",
                job_package="OtherRegistry",
                started_by="user-123",
                status="running",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            ),
        ]

    monkeypatch.setattr(
        batchjob,
        "query_batch_jobs",
        _fake_query_batch_jobs,
    )

    def _fake_get_responses(batch_job_id=None, job_package=None):
        status = "completed" if batch_job_id == "batch-match" else "in-progress"
        return [type("ResponseRecord", (), {"response_id": "response-123", "response": {"status": status}})()]

    monkeypatch.setattr(batchjob, "get_responses", _fake_get_responses)

    async def _fake_fetch_patient(patient_id):
        if patient_id == "patient-match":
            return {
                "resourceType": "Patient",
                "id": patient_id,
                "name": [{"family": "Doe", "given": ["Jane"]}],
                "birthDate": "2020-01-01",
                "gender": "female",
            }
        return {
            "resourceType": "Patient",
            "id": patient_id,
            "name": [{"family": "Smith", "given": ["John"]}],
            "birthDate": "2020-01-01",
            "gender": "male",
        }

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

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


async def test_list_batch_jobs_supports_comma_separated_status_filters(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    captured_kwargs = {}

    def _fake_query_batch_jobs(**kwargs):
        captured_kwargs.update(kwargs)
        return [
            BatchJobs(
                batch_id="batch-match",
                patient_id="patient-match",
                job_package="ExampleRegistry",
                started_by="user-123",
                status="running",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            )
        ]

    monkeypatch.setattr(batchjob, "query_batch_jobs", _fake_query_batch_jobs)
    monkeypatch.setattr(
        batchjob,
        "get_responses",
        lambda batch_job_id=None, job_package=None: [type("ResponseRecord", (), {"response_id": "response-123", "response": {"status": "in-progress"}})()],
    )

    async def _fake_fetch_patient(patient_id):
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

    result = await batchjob.list_batch_jobs(
        batch_job_status="pending, running , complete",
        questionnaire_response_status="completed, in-progress",
        claims={},
    )

    bundle = BundleResource.model_validate(result)
    assert bundle.total == 1
    assert captured_kwargs["statuses"] == ["pending", "running", "complete"]
    assert captured_kwargs["questionnaire_response_statuses"] == ["completed", "in-progress"]


async def test_list_batch_jobs_supports_comma_separated_patient_gender_filter(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        batchjob,
        "query_batch_jobs",
        lambda **kwargs: [
            BatchJobs(
                batch_id="batch-match",
                patient_id="patient-match",
                job_package="ExampleRegistry",
                started_by="user-123",
                status="running",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            ),
            BatchJobs(
                batch_id="batch-skip",
                patient_id="patient-skip",
                job_package="ExampleRegistry",
                started_by="user-123",
                status="running",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            ),
        ],
    )
    monkeypatch.setattr(batchjob, "get_responses", lambda batch_job_id=None, job_package=None: [])

    async def _fake_fetch_patient(patient_id):
        if patient_id == "patient-match":
            return {"resourceType": "Patient", "id": patient_id, "gender": "female"}
        return {"resourceType": "Patient", "id": patient_id, "gender": "unknown"}

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

    result = await batchjob.list_batch_jobs(
        patient_gender="male, female , other",
        claims={},
    )

    bundle = BundleResource.model_validate(result)
    assert bundle.total == 1
    assert bundle.entry is not None
    values = {param.name: param for param in ParametersResponse.model_validate(bundle.entry[0].resource).parameter}
    assert values["batchId"].valueString == "batch-match"


async def test_list_batch_jobs_applies_inclusive_date_filters(monkeypatch):
    matching_created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    nonmatching_created_at = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        batchjob,
        "query_batch_jobs",
        lambda **kwargs: [
            BatchJobs(
                batch_id="batch-match",
                patient_id="patient-match",
                job_package="ExampleRegistry",
                started_by="user-123",
                status="complete",
                result_bundle=None,
                created_at=matching_created_at,
                completed_at=None,
            ),
            BatchJobs(
                batch_id="batch-skip",
                patient_id="patient-skip",
                job_package="ExampleRegistry",
                started_by="user-123",
                status="complete",
                result_bundle=None,
                created_at=nonmatching_created_at,
                completed_at=None,
            ),
        ],
    )
    monkeypatch.setattr(batchjob, "get_responses", lambda batch_job_id=None, job_package=None: [])

    async def _fake_fetch_patient(patient_id):
        if patient_id == "patient-match":
            return {
                "resourceType": "Patient",
                "id": patient_id,
                "birthDate": "2020-01-01",
            }
        return {
            "resourceType": "Patient",
            "id": patient_id,
            "birthDate": "2020-02-01",
        }

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

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
    monkeypatch.setattr(batchjob, "query_batch_jobs", lambda **kwargs: [])

    result = await batchjob.list_batch_jobs(dob_start_date="01-01-2020", claims={})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


async def test_get_batch_job_results_returns_partial_bundle_while_running(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    partial_bundle = {
        "resourceType": "Bundle",
        "id": "bundle-123",
        "type": "collection",
        "total": 2,
        "entry": [
            {
                "fullUrl": "Observation/status-observation",
                "resource": {
                    "resourceType": "Observation",
                    "id": "status-observation",
                    "status": "preliminary",
                    "code": {"coding": [{"code": "result-status"}]},
                },
            },
            {
                "fullUrl": "Observation/obs-1",
                "resource": {"resourceType": "Observation", "id": "obs-1", "status": "final", "code": {"coding": [{"code": "answer"}]}},
            },
        ],
    }
    monkeypatch.setattr(
        batchjob,
        "get_batch_job",
        lambda batch_id: BatchJobs(
            batch_id=batch_id,
            patient_id="patient-123",
            job_package="Registry",
            started_by="user-123",
            status="running",
            result_bundle=partial_bundle,
            created_at=created_at,
            completed_at=None,
        ),
    )
    monkeypatch.setattr(
        batchjob,
        "get_jobs_for_batch",
        lambda batch_id: [
            type("Job", (), {"status": "complete"})(),
            type("Job", (), {"status": "running"})(),
            type("Job", (), {"status": "skipped"})(),
        ],
    )

    result = await batchjob.get_batch_job_results("batch-123", claims={})

    assert isinstance(result, dict)
    result_entries = result.get("entry")
    assert result_entries is not None
    assert result_entries[0]["resource"]["valueCodeableConcept"]["text"] == "Batch job status: 67% (2/3)"
    assert "valueCodeableConcept" not in partial_bundle["entry"][0]["resource"]


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

    result = batchjob._with_batch_progress(bundle, [], "complete")

    assert result["entry"][0]["resource"]["valueCodeableConcept"]["text"] == "Batch job status: 100% (0/0)"


async def test_get_batch_job_results_returns_202_when_running_without_snapshot(monkeypatch):
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        batchjob,
        "get_batch_job",
        lambda batch_id: BatchJobs(
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
    created_at = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 5, 22, 12, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        batchjob,
        "get_batch_job",
        lambda batch_id: BatchJobs(
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
        batchjob,
        "get_responses",
        lambda batch_job_id=None, job_package=None: [type("ResponseRecord", (), {"response_id": "response-123", "response": {"status": "completed"}})()],
    )

    async def _fake_fetch_patient(patient_id):
        return {
            "resourceType": "Patient",
            "id": patient_id,
            "name": [{"family": "Doe", "given": ["Jane"]}],
            "birthDate": "2020-01-01",
            "gender": "female",
        }

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

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
