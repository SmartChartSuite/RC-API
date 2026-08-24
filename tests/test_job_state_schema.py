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
    monkeypatch.setattr(reloaded, "_current_database_revision", lambda: "0002")
    monkeypatch.setattr(reloaded, "_head_revision", lambda: "0002")
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
    monkeypatch.setattr(job_state, "_head_revision", lambda: "0002")

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


def test_reconcile_stale_batch_jobs_preserves_partial_and_terminal_results(monkeypatch):
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(job_state, "db_engine", engine)
    job_state.Base.metadata.create_all(engine)
    for batch_id in ("stale-running", "stale-pending", "fresh-running"):
        assert job_state.create_batch_job(batch_id, "patient-1", "Registry")

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    partial_bundle = {"resourceType": "Bundle", "id": "partial-1"}
    with Session(engine) as session:
        session.execute(
            sqlalchemy.update(job_state.BatchJobs).where(job_state.BatchJobs.batch_id == "stale-running").values(status="running", heartbeat_at=old, updated_at=old, result_bundle=partial_bundle)
        )
        session.execute(sqlalchemy.update(job_state.BatchJobs).where(job_state.BatchJobs.batch_id == "stale-pending").values(status="pending", updated_at=old))
        session.execute(
            sqlalchemy.update(job_state.BatchJobs)
            .where(job_state.BatchJobs.batch_id == "fresh-running")
            .values(status="running", heartbeat_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        )
        session.commit()

    assert job_state.create_job("job-complete", "stale-running", "patient-1", "Registry", "complete-task", "structured", status="complete")
    assert job_state.create_job("job-running", "stale-running", "patient-1", "Registry", "running-task", "structured", status="running")

    stale_ids = job_state.reconcile_stale_batch_jobs(300)

    assert set(stale_ids) == {"stale-running", "stale-pending"}
    stale_running = job_state.get_batch_job("stale-running")
    stale_pending = job_state.get_batch_job("stale-pending")
    fresh_running = job_state.get_batch_job("fresh-running")
    assert stale_running is not None and stale_running.status == "error"
    assert stale_running.result_bundle == partial_bundle
    assert stale_pending is not None and stale_pending.status == "error"
    assert stale_pending.result_bundle is not None
    assert stale_pending.result_bundle["resourceType"] == "OperationOutcome"
    assert fresh_running is not None and fresh_running.status == "running"

    jobs = {job.job_id: job for job in job_state.get_jobs_for_batch("stale-running")}
    assert jobs["job-complete"].status == "complete"
    assert jobs["job-running"].status == "error"
