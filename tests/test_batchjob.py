from datetime import datetime, timezone

from fastapi import BackgroundTasks, Response
from fastapi.responses import JSONResponse

from src.models.fhir import ParametersResponse
from src.models.job_request import JobRequest
from src.models.job_request import JobRequestParameter
from src.models.job_response import BatchJobAcceptedResponse
from src.routers import batchjob
from src.services import job_orchestrator
from src.services.job_state import BatchJobs


async def test_post_batch_job_schedules_requested_jobs(monkeypatch):
    monkeypatch.setattr(batchjob, "config_errors", {})
    monkeypatch.setattr(batchjob, "create_batch_job", lambda batch_id, patient_id, job_package: True)

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

    result = await batchjob.post_batch_job(body, background_tasks, response, claims={})

    assert isinstance(result, BatchJobAcceptedResponse)
    assert not isinstance(result, JSONResponse)
    assert {param.name: param.valueString for param in result.parameter}["status"] == "pending"
    assert response.headers["Location"].startswith("/batchjob/")
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is batchjob.run_batch_job
    assert task.args[2] == "patient-123"
    assert task.args[3] == "SyphilisRegistry"
    assert task.args[4] is None
    assert task.args[5] == ["SyphilisHistory", "ig_hc"]


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
        "get_all_batch_jobs",
        lambda: [
            BatchJobs(
                batch_id="batch-123",
                patient_id="patient-123",
                job_package="SyphilisRegistry",
                status="complete",
                result_bundle=None,
                created_at=created_at,
                completed_at=completed_at,
            ),
            BatchJobs(
                batch_id="batch-456",
                patient_id="patient-123",
                job_package="SyphilisRegistry",
                status="running",
                result_bundle=None,
                created_at=created_at,
                completed_at=None,
            ),
        ],
    )

    async def _fake_fetch_patient(patient_id):
        fetch_calls.append(patient_id)
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

    result = await batchjob.list_batch_jobs(include_patient=True, claims={})

    assert len(result) == 2
    assert isinstance(result[0], ParametersResponse)
    values = {param.name: param for param in result[0].parameter}
    assert values["batchId"].valueString == "batch-123"
    assert values["patientId"].valueString == "patient-123"
    assert values["jobPackage"].valueString == "SyphilisRegistry"
    assert values["status"].valueString == "complete"
    assert values["jobStartDateTime"].valueDateTime == created_at.isoformat()
    assert values["jobCompletedDateTime"].valueDateTime == completed_at.isoformat()
    assert values["patient"].resource == {"resourceType": "Patient", "id": "patient-123"}
    assert fetch_calls == ["patient-123"]


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
            status="complete",
            result_bundle=None,
            created_at=created_at,
            completed_at=completed_at,
        ),
    )

    async def _fake_fetch_patient(patient_id):
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(batchjob, "_fetch_patient", _fake_fetch_patient)

    result = await batchjob.get_batch_job_status("batch-123", include_patient=True, claims={})

    assert isinstance(result, ParametersResponse)
    values = {param.name: param for param in result.parameter}
    assert values["batchId"].valueString == "batch-123"
    assert values["patientId"].valueString == "patient-123"
    assert values["jobPackage"].valueString == "SyphilisRegistry"
    assert values["status"].valueString == "complete"
    assert values["jobStartDateTime"].valueDateTime == created_at.isoformat()
    assert values["jobCompletedDateTime"].valueDateTime == completed_at.isoformat()
    assert values["patient"].resource == {"resourceType": "Patient", "id": "patient-123"}
