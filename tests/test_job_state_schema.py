import importlib
import time
from datetime import UTC, datetime, timedelta

import sqlalchemy
from sqlalchemy.orm import Session

from src.services import job_state
from src.util.settings import settings


def _create_batch_job(batch_id: str, patient_id: str, job_package: str, started_by: str = "test-user") -> bool:
    response_id = f"{batch_id}-response"
    return job_state.create_batch_job_with_response(
        batch_id,
        patient_id,
        job_package,
        started_by,
        response_id,
        {"resourceType": "QuestionnaireResponse", "id": response_id},
        "questionnaire-1",
    )


def _create_job(job_id: str, batch_id: str, patient_id: str, job_package: str, task_name: str, task_type: str, status: str = "pending") -> bool:
    snapshot = job_state.ensure_job(job_id, batch_id, patient_id, job_package, task_name, task_type)
    if snapshot is None:
        return False
    if status != "running":
        return job_state.update_job_result(snapshot.job_id, status)
    return True


def test_metadata_schema_uses_non_sqlite_schema():
    assert job_state._metadata_schema("postgresql+psycopg://user:pass@host/db", "custom_schema") == "custom_schema"
    assert job_state._metadata_schema("sqlite+pysqlite:///rcapi_jobs.sqlite", "custom_schema") is None


def test_models_apply_configured_schema_for_named_schema_backends(monkeypatch):
    original_connection_string = settings.db_connection_string
    original_schema = settings.db_schema
    executed_statements = []

    class _FakeConnection:
        def execute(self, statement):
            executed_statements.append(statement)

    class _FakeBegin:
        def __enter__(self):
            return _FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeBegin()

    monkeypatch.setattr(settings, "db_connection_string", "postgresql+psycopg://user:pass@host/db")
    monkeypatch.setattr(settings, "db_schema", "custom_schema")
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: _FakeEngine())

    reloaded = importlib.reload(job_state)
    monkeypatch.setattr(reloaded, "_current_database_revision", lambda: "0003")
    monkeypatch.setattr(reloaded, "_head_revision", lambda: "0003")
    reloaded.initialize_db()

    assert reloaded.Base.metadata.schema == "custom_schema"
    assert reloaded.BatchJobs.__table__.schema == "custom_schema"
    assert reloaded.Jobs.__table__.schema == "custom_schema"
    assert reloaded.QuestionnaireResponses.__table__.schema == "custom_schema"
    assert len(executed_statements) == 1
    assert getattr(executed_statements[0], "element", None) == "custom_schema"

    monkeypatch.setattr(settings, "db_connection_string", original_connection_string)
    monkeypatch.setattr(settings, "db_schema", original_schema)
    importlib.reload(reloaded)


def test_initialize_db_rejects_outdated_revision(monkeypatch):
    monkeypatch.setattr(job_state, "_ensure_schema_exists", lambda: None)
    monkeypatch.setattr(job_state, "_current_database_revision", lambda: "0001")
    monkeypatch.setattr(job_state, "_head_revision", lambda: "0003")

    try:
        job_state.initialize_db()
    except RuntimeError as exc:
        assert "alembic upgrade head" in str(exc)
    else:
        raise AssertionError("Expected an outdated database revision to fail startup")


def test_orm_timestamp_defaults_are_evaluated_per_insert(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)

    before = datetime.now(UTC)
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    first = job_state.get_batch_job("batch-1")
    assert first is not None
    time.sleep(0.01)
    assert _create_batch_job("batch-2", "patient-2", "Registry")
    second = job_state.get_batch_job("batch-2")
    after = datetime.now(UTC)

    assert second is not None
    first_created_at = first.created_at.replace(tzinfo=UTC) if first.created_at.tzinfo is None else first.created_at
    second_created_at = second.created_at.replace(tzinfo=UTC) if second.created_at.tzinfo is None else second.created_at
    assert before <= first_created_at <= after
    assert before <= second_created_at <= after
    assert first_created_at < second_created_at


def test_get_responses_for_batches_returns_deterministic_mapping(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)

    timestamp = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                job_state.BatchJobs(batch_id="batch-1", patient_id="patient-1", job_package="Registry"),
                job_state.BatchJobs(batch_id="batch-2", patient_id="patient-2", job_package="Registry"),
                job_state.QuestionnaireResponses(
                    response_id="response-z",
                    batch_job_id="batch-1",
                    job_package="Registry",
                    patient_id="patient-1",
                    response={"status": "z"},
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                job_state.QuestionnaireResponses(
                    response_id="response-a",
                    batch_job_id="batch-1",
                    job_package="Registry",
                    patient_id="patient-1",
                    response={"status": "a"},
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                job_state.QuestionnaireResponses(
                    response_id="response-b",
                    batch_job_id="batch-2",
                    job_package="Registry",
                    patient_id="patient-2",
                    response={"status": "b"},
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
            ]
        )
        session.commit()

    responses = job_state.get_responses_for_batches(["batch-1", "batch-2", "batch-missing"])

    assert set(responses) == {"batch-1", "batch-2"}
    assert responses["batch-1"].response_id == "response-a"
    assert responses["batch-2"].response_id == "response-b"
    assert job_state.get_responses_for_batches([]) == {}


def test_recover_expired_batch_job_leases_preserves_partial_and_terminal_results(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    for batch_id in ("retry-running", "exhausted-running", "fresh-running"):
        assert _create_batch_job(batch_id, "patient-1", "Registry")

    now = datetime.now(UTC)
    partial_bundle = {"resourceType": "Bundle", "id": "partial-1"}
    with Session(engine) as session:
        session.execute(
            sqlalchemy.update(job_state.BatchJobs)
            .where(job_state.BatchJobs.batch_id == "retry-running")
            .values(
                status="running",
                worker_id="worker-1",
                attempt_count=1,
                lease_expires_at=now - timedelta(minutes=1),
                result_bundle=partial_bundle,
            )
        )
        session.execute(
            sqlalchemy.update(job_state.BatchJobs)
            .where(job_state.BatchJobs.batch_id == "exhausted-running")
            .values(
                status="running",
                worker_id="worker-2",
                attempt_count=3,
                max_attempts=3,
                lease_expires_at=now - timedelta(minutes=1),
            )
        )
        session.execute(
            sqlalchemy.update(job_state.BatchJobs)
            .where(job_state.BatchJobs.batch_id == "fresh-running")
            .values(
                status="running",
                worker_id="worker-3",
                attempt_count=1,
                lease_expires_at=now + timedelta(minutes=1),
            )
        )
        session.commit()

    assert _create_job("job-complete", "retry-running", "patient-1", "Registry", "complete-task", "structured", status="complete")
    assert _create_job("job-running", "retry-running", "patient-1", "Registry", "running-task", "structured", status="running")

    retried, failed = job_state.recover_expired_batch_job_leases(30)

    assert retried == ["retry-running"]
    assert failed == ["exhausted-running"]
    retry_running = job_state.get_batch_job("retry-running")
    exhausted_running = job_state.get_batch_job("exhausted-running")
    fresh_running = job_state.get_batch_job("fresh-running")
    assert retry_running is not None and retry_running.status == "pending"
    assert retry_running.result_bundle == partial_bundle
    assert retry_running.next_attempt_at is not None
    assert exhausted_running is not None and exhausted_running.status == "error"
    assert exhausted_running.result_bundle is not None
    assert exhausted_running.result_bundle["resourceType"] == "OperationOutcome"
    assert fresh_running is not None and fresh_running.status == "running"

    jobs = {job.job_id: job for job in job_state.get_jobs_for_batch("retry-running")}
    assert jobs["job-complete"].status == "complete"
    assert jobs["job-running"].status == "pending"


def test_batch_claim_is_exclusive_and_writes_are_fenced(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("batch-1", "patient-1", "Registry")

    claim = job_state.claim_next_batch_job("worker-1", 60)

    assert claim is not None
    assert claim.attempt_count == 1
    assert job_state.claim_next_batch_job("worker-2", 60) is None
    assert not job_state.update_batch_job_result(
        "batch-1",
        {"resourceType": "Bundle", "id": "stale"},
        worker_id="worker-2",
        attempt_count=1,
    )
    assert job_state.update_batch_job_result(
        "batch-1",
        {"resourceType": "Bundle", "id": "owned"},
        worker_id="worker-1",
        attempt_count=1,
    )
    assert not job_state.release_batch_job_attempt("batch-1", "worker-2", 1)
    assert job_state.release_batch_job_attempt("batch-1", "worker-1", 1)

    batch = job_state.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "pending"
    assert batch.attempt_count == 0
    assert batch.worker_id is None
    assert batch.result_bundle == {"resourceType": "Bundle", "id": "owned"}


def test_failed_attempt_retries_then_exhausts(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    with Session(engine) as session:
        session.execute(sqlalchemy.update(job_state.BatchJobs).where(job_state.BatchJobs.batch_id == "batch-1").values(max_attempts=2))
        session.commit()

    first = job_state.claim_next_batch_job("worker-1", 60)
    assert first is not None
    assert job_state.fail_batch_job_attempt("batch-1", "worker-1", first.attempt_count, "first failure", 0) == "retry"

    second = job_state.claim_next_batch_job("worker-2", 60)
    assert second is not None
    assert second.attempt_count == 2
    assert job_state.fail_batch_job_attempt("batch-1", "worker-2", second.attempt_count, "second failure", 0) == "error"

    batch = job_state.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "error"
    assert batch.last_error == "second failure"
    assert batch.result_bundle is not None
    assert batch.result_bundle["resourceType"] == "OperationOutcome"


def test_requeue_batch_job_for_child_retry_resets_only_errors_and_is_fenced(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("batch-1", "patient-1", "Registry")

    claim = job_state.claim_next_batch_job("worker-1", 60)
    assert claim is not None
    complete = job_state.ensure_job("job-complete", "batch-1", "patient-1", "Registry", "Complete", "structured")
    errored = job_state.ensure_job("job-error", "batch-1", "patient-1", "Registry", "Error", "structured")
    skipped = job_state.ensure_job("job-skipped", "batch-1", "patient-1", "Registry", "Skipped", "unstructured")
    assert complete is not None and errored is not None and skipped is not None
    assert job_state.update_job_result(complete.job_id, "complete", {"answer": "yes"})
    assert job_state.update_job_result(errored.job_id, "error", {"message": "old failure"})
    assert job_state.update_job_result(skipped.job_id, "skipped", {"message": "not applicable"})
    bundle = {"resourceType": "Bundle", "id": "partial-1"}
    assert job_state.update_batch_job_result("batch-1", bundle, worker_id="worker-1", attempt_count=claim.attempt_count)

    assert job_state.requeue_batch_job_for_child_retry("batch-1", "worker-1", claim.attempt_count, "1 child task(s) failed: Error", 0)

    batch = job_state.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "pending"
    assert batch.result_bundle == bundle
    assert batch.worker_id is None
    assert batch.lease_expires_at is None
    assert batch.heartbeat_at is None
    assert batch.next_attempt_at is not None
    assert batch.last_error == "1 child task(s) failed: Error"
    children = {child.task_name: child for child in job_state.get_jobs_for_batch("batch-1")}
    assert children["Complete"].status == "complete"
    assert children["Complete"].result == {"answer": "yes"}
    assert children["Skipped"].status == "skipped"
    assert children["Skipped"].result == {"message": "not applicable"}
    assert children["Error"].status == "pending"
    assert children["Error"].result is None
    assert children["Error"].completed_at is None

    next_claim = job_state.claim_next_batch_job("worker-2", 60)
    assert next_claim is not None
    assert not job_state.requeue_batch_job_for_child_retry("batch-1", "other-worker", next_claim.attempt_count, "stale", 30)
    with Session(engine) as session:
        session.execute(sqlalchemy.update(job_state.BatchJobs).where(job_state.BatchJobs.batch_id == "batch-1").values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        session.commit()
    assert not job_state.requeue_batch_job_for_child_retry("batch-1", "worker-2", next_claim.attempt_count, "expired", 30)


def test_failed_attempt_exhaustion_uses_partial_only_for_completed_bundle(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("partial-batch", "patient-1", "Registry")
    assert _create_batch_job("error-batch", "patient-1", "Registry")
    with Session(engine) as session:
        session.execute(sqlalchemy.update(job_state.BatchJobs).where(job_state.BatchJobs.batch_id.in_(("partial-batch", "error-batch"))).values(max_attempts=1))
        session.commit()

    partial_claim = job_state.claim_next_batch_job("worker-1", 60)
    assert partial_claim is not None
    partial_complete = job_state.ensure_job("partial-complete", "partial-batch", "patient-1", "Registry", "Complete", "structured")
    partial_running = job_state.ensure_job("partial-running", "partial-batch", "patient-1", "Registry", "Running", "structured")
    assert partial_complete is not None and partial_running is not None
    assert job_state.update_job_result(partial_complete.job_id, "complete", {"answer": "yes"})
    assert job_state.update_batch_job_result("partial-batch", {"resourceType": "Bundle", "id": "partial"}, worker_id="worker-1", attempt_count=partial_claim.attempt_count)
    assert job_state.fail_batch_job_attempt("partial-batch", "worker-1", partial_claim.attempt_count, "batch failure", 0) == "partial"

    error_claim = job_state.claim_next_batch_job("worker-2", 60)
    assert error_claim is not None
    error_complete = job_state.ensure_job("error-complete", "error-batch", "patient-1", "Registry", "Complete", "structured")
    error_running = job_state.ensure_job("error-running", "error-batch", "patient-1", "Registry", "Running", "structured")
    assert error_complete is not None and error_running is not None
    assert job_state.update_job_result(error_complete.job_id, "complete", {"answer": "yes"})
    assert job_state.fail_batch_job_attempt("error-batch", "worker-2", error_claim.attempt_count, "batch failure", 0) == "error"

    partial_batch = job_state.get_batch_job("partial-batch")
    error_batch = job_state.get_batch_job("error-batch")
    assert partial_batch is not None and error_batch is not None
    assert partial_batch.status == "partial"
    assert partial_batch.result_bundle == {"resourceType": "Bundle", "id": "partial"}
    assert error_batch.status == "error"
    assert error_batch.result_bundle is not None
    assert error_batch.result_bundle["resourceType"] == "OperationOutcome"
    assert {child.status for child in job_state.get_jobs_for_batch("partial-batch")} == {"complete", "error"}
    assert {child.status for child in job_state.get_jobs_for_batch("error-batch")} == {"complete", "error"}


def test_ensure_job_reuses_one_logical_child_and_preserves_terminal_state(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("batch-1", "patient-1", "Registry")

    first = job_state.ensure_job("job-1", "batch-1", "patient-1", "Registry", "TaskA", "structured")
    assert first is not None
    assert job_state.update_job_result(first.job_id, "complete", {"results": {"TaskA": "yes"}})
    second = job_state.ensure_job("job-2", "batch-1", "patient-1", "Registry", "TaskA", "structured")

    assert second is not None
    assert second.job_id == "job-1"
    assert second.status == "complete"
    assert second.result == {"results": {"TaskA": "yes"}}
    assert len(job_state.get_jobs_for_batch("batch-1")) == 1


def test_expired_lease_rejects_fenced_writes_until_recovery(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    claim = job_state.claim_next_batch_job("worker-1", 60)
    assert claim is not None
    fence = {"worker_id": "worker-1", "attempt_count": claim.attempt_count}

    child = job_state.ensure_job("job-1", "batch-1", "patient-1", "Registry", "TaskA", "structured", **fence)
    assert child is not None
    assert job_state.renew_batch_job_lease("batch-1", "worker-1", claim.attempt_count, 60)

    with Session(engine) as session:
        session.execute(sqlalchemy.update(job_state.BatchJobs).where(job_state.BatchJobs.batch_id == "batch-1").values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        session.commit()

    assert not job_state.renew_batch_job_lease("batch-1", "worker-1", claim.attempt_count, 60)
    assert not job_state.update_batch_job_result("batch-1", {"resourceType": "Bundle", "id": "late"}, **fence)
    assert not job_state.update_batch_job_status("batch-1", "complete", {"resourceType": "Bundle", "id": "late"}, **fence)
    assert not job_state.update_job_result(child.job_id, "complete", {"late": True}, batch_id="batch-1", **fence)
    assert job_state.ensure_job("job-2", "batch-1", "patient-1", "Registry", "TaskB", "structured", **fence) is None
    assert not job_state.release_batch_job_attempt("batch-1", "worker-1", claim.attempt_count)
    assert job_state.fail_batch_job_attempt("batch-1", "worker-1", claim.attempt_count, "late failure", 0) == "lost"

    batch = job_state.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "running"
    assert batch.result_bundle is None
    assert [job.task_name for job in job_state.get_jobs_for_batch("batch-1")] == ["TaskA"]
    assert job_state.get_jobs_for_batch("batch-1")[0].status == "running"

    retried, failed = job_state.recover_expired_batch_job_leases(0)
    assert (retried, failed) == (["batch-1"], [])


def test_fenced_child_update_requires_matching_batch(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    assert _create_batch_job("batch-2", "patient-2", "Registry")

    first = job_state.claim_next_batch_job("worker-1", 60)
    second = job_state.claim_next_batch_job("worker-2", 60)
    assert first is not None
    assert second is not None
    child = job_state.ensure_job(
        "job-1",
        "batch-1",
        "patient-1",
        "Registry",
        "TaskA",
        "structured",
        worker_id="worker-1",
        attempt_count=first.attempt_count,
    )
    assert child is not None

    assert not job_state.update_job_result(
        child.job_id,
        "complete",
        {"wrong_batch": True},
        batch_id="batch-2",
        worker_id="worker-2",
        attempt_count=second.attempt_count,
    )
    unchanged = job_state.get_jobs_for_batch("batch-1")
    assert len(unchanged) == 1
    assert unchanged[0].status == "running"
    assert unchanged[0].result is None
