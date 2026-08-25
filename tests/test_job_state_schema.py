from datetime import datetime, timedelta, timezone
import importlib

import sqlalchemy
from sqlalchemy.orm import Session

import src.services.job_state as job_state
import src.util.settings as settings


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


def test_mark_unfinished_jobs_error_preserves_terminal_jobs(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)

    assert job_state.create_batch_job("batch-1", "patient-1", "Registry")
    for job_id, status in (
        ("job-complete", "complete"),
        ("job-running", "running"),
        ("job-skipped", "skipped"),
        ("job-pending", "pending"),
    ):
        assert job_state.create_job(job_id, "batch-1", "patient-1", "Registry", job_id, "structured", status=status)

    updated = job_state.mark_unfinished_jobs_error("batch-1", "late failure")

    jobs = {job.job_id: job for job in job_state.get_jobs_for_batch("batch-1")}
    assert updated == 2
    assert jobs["job-complete"].status == "complete"
    assert jobs["job-skipped"].status == "skipped"
    assert jobs["job-running"].status == "error"
    assert jobs["job-running"].result == {"message": "late failure"}
    assert jobs["job-pending"].status == "error"
    assert jobs["job-pending"].result == {"message": "late failure"}


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


def test_start_batch_job_attempt_updates_attempt_and_heartbeat(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert job_state.create_batch_job("batch-1", "patient-1", "Registry")

    job_state.start_batch_job_attempt("batch-1")

    batch = job_state.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "running"
    assert batch.attempt_count == 1
    assert batch.heartbeat_at is not None
    assert batch.updated_at is not None


def test_recover_expired_batch_job_leases_preserves_partial_and_terminal_results(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    for batch_id in ("retry-running", "exhausted-running", "fresh-running"):
        assert job_state.create_batch_job(batch_id, "patient-1", "Registry")

    now = datetime.now(timezone.utc)
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

    assert job_state.create_job("job-complete", "retry-running", "patient-1", "Registry", "complete-task", "structured", status="complete")
    assert job_state.create_job("job-running", "retry-running", "patient-1", "Registry", "running-task", "structured", status="running")

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
    assert job_state.create_batch_job("batch-1", "patient-1", "Registry")

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
    assert job_state.create_batch_job("batch-1", "patient-1", "Registry")
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


def test_ensure_job_reuses_one_logical_child_and_preserves_terminal_state(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    assert job_state.create_batch_job("batch-1", "patient-1", "Registry")

    first = job_state.ensure_job("job-1", "batch-1", "patient-1", "Registry", "TaskA", "structured")
    assert first is not None
    assert job_state.update_job_result(first.job_id, "complete", {"results": {"TaskA": "yes"}})
    second = job_state.ensure_job("job-2", "batch-1", "patient-1", "Registry", "TaskA", "structured")

    assert second is not None
    assert second.job_id == "job-1"
    assert second.status == "complete"
    assert second.result == {"results": {"TaskA": "yes"}}
    assert len(job_state.get_jobs_for_batch("batch-1")) == 1
