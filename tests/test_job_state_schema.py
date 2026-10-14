"""Tests job-state migrations, ORM schema alignment, queue leasing, fenced writes, retries, and document storage.

The `db` fixture points the job_state submodules at fresh in-memory SQLite and uses `monkeypatch`; this
module covers persistence rather than router or orchestration logic."""

import importlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.orm import Session

from alembic import command
from src.services.job_state import batch_queue, batch_records, child_jobs, database, models
from src.services.job_state import responses as response_records
from src.util.settings import settings


def _create_batch_job(batch_id: str, patient_id: str, job_package: str, started_by: str = "test-user") -> bool:
    response_id = f"{batch_id}-response"
    return batch_records.create_batch_job_with_response(
        batch_id,
        patient_id,
        job_package,
        started_by,
        response_id,
        {"resourceType": "QuestionnaireResponse", "id": response_id},
        "questionnaire-1",
    )


@dataclass
class _Db:
    engine: sqlalchemy.Engine
    monkeypatch: pytest.MonkeyPatch


@pytest.fixture
def db(monkeypatch) -> _Db:
    """Point the job_state package database owner at a fresh in-memory SQLite database with the current ORM schema."""
    engine = sqlalchemy.create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(database, "db_engine", engine)
    models.Base.metadata.create_all(engine)
    return _Db(engine, monkeypatch)


def _expire_lease(engine: sqlalchemy.Engine, batch_id: str) -> None:
    with Session(engine) as session:
        session.execute(sqlalchemy.update(models.BatchJobs).where(models.BatchJobs.batch_id == batch_id).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        session.commit()


def _create_job(job_id: str, batch_id: str, patient_id: str, job_package: str, task_name: str, task_type: str, status: str = "pending") -> bool:
    snapshot = child_jobs.ensure_job(job_id, batch_id, patient_id, job_package, task_name, task_type)
    if snapshot is None:
        return False
    if status != "running":
        return child_jobs.update_job_result(snapshot.job_id, status)
    return True


def test_metadata_schema_uses_non_sqlite_schema():
    assert models._metadata_schema("postgresql+psycopg://user:pass@host/db", "custom_schema") == "custom_schema"
    assert models._metadata_schema("sqlite+pysqlite:///rcapi_jobs.sqlite", "custom_schema") is None


def test_migrated_sqlite_schema_matches_orm_metadata(tmp_path, monkeypatch):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'schema.sqlite'}"
    monkeypatch.setattr(settings, "db_connection_string", database_url)

    command.upgrade(Config(database._ALEMBIC_CONFIG_PATH), "head")
    engine = sqlalchemy.create_engine(database_url)
    try:
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            assert compare_metadata(migration_context, models.Base.metadata) == []
    finally:
        engine.dispose()


def test_postgresql_claim_statement_uses_skip_locked():
    from sqlalchemy.dialects import postgresql

    statement = batch_queue._runnable_batch_job_statement(datetime.now(UTC)).with_for_update(skip_locked=True)
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in compiled


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
    real_create_engine = sqlalchemy.create_engine
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: _FakeEngine())

    reloaded_models = importlib.reload(models)
    reloaded_database = importlib.reload(database)
    monkeypatch.setattr(reloaded_database, "_current_database_revision", lambda: "0003")
    monkeypatch.setattr(reloaded_database, "_head_revision", lambda: "0003")
    reloaded_database.initialize_db()

    assert reloaded_models.Base.metadata.schema == "custom_schema"
    assert reloaded_models.BatchJobs.__table__.schema == "custom_schema"
    assert reloaded_models.Jobs.__table__.schema == "custom_schema"
    assert reloaded_models.QuestionnaireResponses.__table__.schema == "custom_schema"
    assert len(executed_statements) == 1
    assert getattr(executed_statements[0], "element", None) == "custom_schema"

    monkeypatch.setattr(settings, "db_connection_string", original_connection_string)
    monkeypatch.setattr(settings, "db_schema", original_schema)
    monkeypatch.setattr(sqlalchemy, "create_engine", real_create_engine)
    importlib.reload(models)
    importlib.reload(database)


def test_initialize_db_rejects_outdated_revision(monkeypatch):
    monkeypatch.setattr(database, "_ensure_schema_exists", lambda: None)
    monkeypatch.setattr(database, "_current_database_revision", lambda: "0001")
    monkeypatch.setattr(database, "_head_revision", lambda: "0003")

    try:
        database.initialize_db()
    except RuntimeError as exc:
        assert "alembic upgrade head" in str(exc)
    else:
        raise AssertionError("Expected an outdated database revision to fail startup")


def test_orm_timestamp_defaults_are_evaluated_per_insert(db):

    before = datetime.now(UTC)
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    first = batch_records.get_batch_job("batch-1")
    assert first is not None
    time.sleep(0.01)
    assert _create_batch_job("batch-2", "patient-2", "Registry")
    second = batch_records.get_batch_job("batch-2")
    after = datetime.now(UTC)

    assert second is not None
    first_created_at = first.created_at.replace(tzinfo=UTC) if first.created_at.tzinfo is None else first.created_at
    second_created_at = second.created_at.replace(tzinfo=UTC) if second.created_at.tzinfo is None else second.created_at
    assert before <= first_created_at <= after
    assert before <= second_created_at <= after
    assert first_created_at < second_created_at


def test_query_batch_jobs_paginates_and_defers_result_bundle(db):
    engine = db.engine

    timestamp = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                models.BatchJobs(
                    batch_id="batch-z",
                    patient_id="patient-z",
                    job_package="Registry",
                    status="complete",
                    result_bundle={"payload": "z"},
                    created_at=timestamp,
                ),
                models.BatchJobs(
                    batch_id="batch-a",
                    patient_id="patient-a",
                    job_package="Registry",
                    status="complete",
                    result_bundle={"payload": "a"},
                    created_at=timestamp,
                ),
                models.BatchJobs(
                    batch_id="batch-mid",
                    patient_id="patient-mid",
                    job_package="Registry",
                    status="complete",
                    result_bundle={"payload": "mid"},
                    created_at=timestamp - timedelta(minutes=1),
                ),
                models.BatchJobs(
                    batch_id="batch-running",
                    patient_id="patient-running",
                    job_package="Registry",
                    status="running",
                    result_bundle={"payload": "running"},
                    created_at=timestamp,
                ),
                models.BatchJobs(
                    batch_id="batch-other",
                    patient_id="patient-other",
                    job_package="OtherRegistry",
                    status="complete",
                    result_bundle={"payload": "other"},
                    created_at=timestamp,
                ),
                models.BatchJobs(
                    batch_id="batch-old",
                    patient_id="patient-old",
                    job_package="Registry",
                    status="complete",
                    result_bundle={"payload": "old"},
                    created_at=timestamp - timedelta(days=1),
                ),
            ]
        )
        session.commit()

    filters = {
        "statuses": ["complete"],
        "job_package": "registry",
        "run_start_date": timestamp.date(),
        "run_end_date": timestamp.date(),
    }
    first_page, total = batch_records.query_batch_jobs(**filters, limit=2, offset=0)
    second_page, second_total = batch_records.query_batch_jobs(**filters, limit=1, offset=1)

    assert total == 3
    assert second_total == 3
    assert [job.batch_id for job in first_page] == ["batch-z", "batch-a"]
    assert [job.batch_id for job in second_page] == ["batch-a"]
    assert all("result_bundle" in sqlalchemy.inspect(job).unloaded for job in first_page + second_page)

    summary = batch_records.get_batch_job_summary("batch-z")
    assert summary is not None
    assert "result_bundle" in sqlalchemy.inspect(summary).unloaded


def test_get_responses_for_batches_returns_deterministic_mapping(db):
    engine = db.engine

    timestamp = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                models.BatchJobs(batch_id="batch-1", patient_id="patient-1", job_package="Registry"),
                models.BatchJobs(batch_id="batch-2", patient_id="patient-2", job_package="Registry"),
                models.QuestionnaireResponses(
                    response_id="response-z",
                    batch_job_id="batch-1",
                    job_package="Registry",
                    patient_id="patient-1",
                    response={"status": "z"},
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                models.QuestionnaireResponses(
                    response_id="response-a",
                    batch_job_id="batch-1",
                    job_package="Registry",
                    patient_id="patient-1",
                    response={"status": "a"},
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                models.QuestionnaireResponses(
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

    responses = response_records.get_responses_for_batches(["batch-1", "batch-2", "batch-missing"])

    assert set(responses) == {"batch-1", "batch-2"}
    assert responses["batch-1"].response_id == "response-a"
    assert responses["batch-2"].response_id == "response-b"
    assert response_records.get_responses_for_batches([]) == {}


def test_recover_expired_batch_job_leases_preserves_partial_and_terminal_results(db):
    db.monkeypatch.setattr(settings, "batch_job_recovery_chunk_size", 1)
    engine = db.engine
    for batch_id in ("retry-running", "exhausted-running", "fresh-running"):
        assert _create_batch_job(batch_id, "patient-1", "Registry")

    now = datetime.now(UTC)
    partial_bundle = {"resourceType": "Bundle", "id": "partial-1"}
    with Session(engine) as session:
        session.execute(
            sqlalchemy.update(models.BatchJobs)
            .where(models.BatchJobs.batch_id == "retry-running")
            .values(
                status="running",
                worker_id="worker-1",
                attempt_count=1,
                lease_expires_at=now - timedelta(minutes=1),
                result_bundle=partial_bundle,
            )
        )
        session.execute(
            sqlalchemy.update(models.BatchJobs)
            .where(models.BatchJobs.batch_id == "exhausted-running")
            .values(
                status="running",
                worker_id="worker-2",
                attempt_count=3,
                max_attempts=3,
                lease_expires_at=now - timedelta(minutes=1),
            )
        )
        session.execute(
            sqlalchemy.update(models.BatchJobs)
            .where(models.BatchJobs.batch_id == "fresh-running")
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

    retried, failed = batch_queue.recover_expired_batch_job_leases(30)

    assert retried == ["retry-running"]
    assert failed == ["exhausted-running"]
    retry_running = batch_records.get_batch_job("retry-running")
    exhausted_running = batch_records.get_batch_job("exhausted-running")
    fresh_running = batch_records.get_batch_job("fresh-running")
    assert retry_running is not None and retry_running.status == "pending"
    assert retry_running.result_bundle == partial_bundle
    assert retry_running.next_attempt_at is not None
    assert exhausted_running is not None and exhausted_running.status == "error"
    assert exhausted_running.result_bundle is not None
    assert exhausted_running.result_bundle["resourceType"] == "OperationOutcome"
    assert fresh_running is not None and fresh_running.status == "running"

    jobs = {job.job_id: job for job in child_jobs.get_jobs_for_batch("retry-running")}
    assert jobs["job-complete"].status == "complete"
    assert jobs["job-running"].status == "pending"


def test_batch_claim_is_exclusive_and_writes_are_fenced(db):
    assert _create_batch_job("batch-1", "patient-1", "Registry")

    claim = batch_queue.claim_next_batch_job("worker-1", 60)

    assert claim is not None
    assert claim.attempt_count == 1
    assert batch_queue.claim_next_batch_job("worker-2", 60) is None
    assert not batch_records.update_batch_job_result(
        "batch-1",
        {"resourceType": "Bundle", "id": "stale"},
        worker_id="worker-2",
        attempt_count=1,
    )
    assert batch_records.update_batch_job_result(
        "batch-1",
        {"resourceType": "Bundle", "id": "owned"},
        worker_id="worker-1",
        attempt_count=1,
    )
    assert not batch_queue.release_batch_job_attempt("batch-1", "worker-2", 1)
    assert batch_queue.release_batch_job_attempt("batch-1", "worker-1", 1)

    batch = batch_records.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "pending"
    assert batch.attempt_count == 0
    assert batch.worker_id is None
    assert batch.result_bundle == {"resourceType": "Bundle", "id": "owned"}


def test_batch_document_store_merges_fences_and_deletes_with_batch(db):
    engine = db.engine
    assert _create_batch_job("batch-docs", "patient-1", "Registry")
    claim = batch_queue.claim_next_batch_job("worker-1", 60)
    assert claim is not None
    fence = {"worker_id": "worker-1", "attempt_count": claim.attempt_count}

    assert batch_records.upsert_batch_job_documents(
        "batch-docs",
        [
            {"id": "doc-a", "type": "Note", "date": "2026-05-24", "text": "A"},
            {"id": "doc-b", "type": "Note", "date": "2026-05-23", "text": "B-old"},
        ],
        **fence,
    )
    assert batch_records.upsert_batch_job_documents(
        "batch-docs",
        [
            {"id": "doc-b", "type": "Updated Note", "date": "2026-05-23", "text": "B-new"},
            {"id": "doc-c", "type": "Note", "date": "2026-05-22", "text": "C"},
        ],
        **fence,
    )

    stored = batch_records.get_batch_job_documents("batch-docs")
    assert [document["id"] for document in stored] == ["doc-c", "doc-b", "doc-a"]
    assert next(document for document in stored if document["id"] == "doc-b")["text"] == "B-new"
    assert not batch_records.upsert_batch_job_documents("batch-docs", [{"id": "doc-stale", "text": "stale"}], worker_id="other-worker", attempt_count=claim.attempt_count)

    _expire_lease(engine, "batch-docs")
    assert not batch_records.upsert_batch_job_documents("batch-docs", [{"id": "doc-expired", "text": "expired"}], **fence)

    response = batch_records.delete_batch_job_record("batch-docs")
    assert response.status_code == 200
    with Session(engine) as session:
        assert session.query(models.BatchJobDocuments).count() == 0
        assert session.get(models.BatchJobs, "batch-docs") is None


def test_failed_attempt_retries_then_exhausts(db):
    engine = db.engine
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    with Session(engine) as session:
        session.execute(sqlalchemy.update(models.BatchJobs).where(models.BatchJobs.batch_id == "batch-1").values(max_attempts=2))
        session.commit()

    first = batch_queue.claim_next_batch_job("worker-1", 60)
    assert first is not None
    assert batch_queue.fail_batch_job_attempt("batch-1", "worker-1", first.attempt_count, "first failure", 0) == "retry"

    second = batch_queue.claim_next_batch_job("worker-2", 60)
    assert second is not None
    assert second.attempt_count == 2
    assert batch_queue.fail_batch_job_attempt("batch-1", "worker-2", second.attempt_count, "second failure", 0) == "error"

    batch = batch_records.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "error"
    assert batch.last_error == "second failure"
    assert batch.result_bundle is not None
    assert batch.result_bundle["resourceType"] == "OperationOutcome"


def test_requeue_batch_job_for_child_retry_resets_only_errors_and_is_fenced(db):
    engine = db.engine
    assert _create_batch_job("batch-1", "patient-1", "Registry")

    claim = batch_queue.claim_next_batch_job("worker-1", 60)
    assert claim is not None
    complete = child_jobs.ensure_job("job-complete", "batch-1", "patient-1", "Registry", "Complete", "structured")
    errored = child_jobs.ensure_job("job-error", "batch-1", "patient-1", "Registry", "Error", "structured")
    skipped = child_jobs.ensure_job("job-skipped", "batch-1", "patient-1", "Registry", "Skipped", "unstructured")
    assert complete is not None and errored is not None and skipped is not None
    assert child_jobs.update_job_result(complete.job_id, "complete", {"answer": "yes"})
    assert child_jobs.update_job_result(errored.job_id, "error", {"message": "old failure"})
    assert child_jobs.update_job_result(skipped.job_id, "skipped", {"message": "not applicable"})
    bundle = {"resourceType": "Bundle", "id": "partial-1"}
    assert batch_records.update_batch_job_result("batch-1", bundle, worker_id="worker-1", attempt_count=claim.attempt_count)

    assert batch_queue.requeue_batch_job_for_child_retry("batch-1", "worker-1", claim.attempt_count, "1 child task(s) failed: Error", 0)

    batch = batch_records.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "pending"
    assert batch.result_bundle == bundle
    assert batch.worker_id is None
    assert batch.lease_expires_at is None
    assert batch.heartbeat_at is None
    assert batch.next_attempt_at is not None
    assert batch.last_error == "1 child task(s) failed: Error"
    children = {child.task_name: child for child in child_jobs.get_jobs_for_batch("batch-1")}
    assert children["Complete"].status == "complete"
    assert children["Complete"].result == {"answer": "yes"}
    assert children["Skipped"].status == "skipped"
    assert children["Skipped"].result == {"message": "not applicable"}
    assert children["Error"].status == "pending"
    assert children["Error"].result is None
    assert children["Error"].completed_at is None

    next_claim = batch_queue.claim_next_batch_job("worker-2", 60)
    assert next_claim is not None
    assert not batch_queue.requeue_batch_job_for_child_retry("batch-1", "other-worker", next_claim.attempt_count, "stale", 30)
    _expire_lease(engine, "batch-1")
    assert not batch_queue.requeue_batch_job_for_child_retry("batch-1", "worker-2", next_claim.attempt_count, "expired", 30)


def test_failed_attempt_exhaustion_uses_partial_only_for_completed_bundle(db):
    engine = db.engine
    assert _create_batch_job("partial-batch", "patient-1", "Registry")
    assert _create_batch_job("error-batch", "patient-1", "Registry")
    with Session(engine) as session:
        session.execute(sqlalchemy.update(models.BatchJobs).where(models.BatchJobs.batch_id.in_(("partial-batch", "error-batch"))).values(max_attempts=1))
        session.commit()

    partial_claim = batch_queue.claim_next_batch_job("worker-1", 60)
    assert partial_claim is not None
    partial_complete = child_jobs.ensure_job("partial-complete", "partial-batch", "patient-1", "Registry", "Complete", "structured")
    partial_running = child_jobs.ensure_job("partial-running", "partial-batch", "patient-1", "Registry", "Running", "structured")
    assert partial_complete is not None and partial_running is not None
    assert child_jobs.update_job_result(partial_complete.job_id, "complete", {"answer": "yes"})
    assert batch_records.update_batch_job_result("partial-batch", {"resourceType": "Bundle", "id": "partial"}, worker_id="worker-1", attempt_count=partial_claim.attempt_count)
    assert batch_queue.fail_batch_job_attempt("partial-batch", "worker-1", partial_claim.attempt_count, "batch failure", 0) == "partial"

    error_claim = batch_queue.claim_next_batch_job("worker-2", 60)
    assert error_claim is not None
    error_complete = child_jobs.ensure_job("error-complete", "error-batch", "patient-1", "Registry", "Complete", "structured")
    error_running = child_jobs.ensure_job("error-running", "error-batch", "patient-1", "Registry", "Running", "structured")
    assert error_complete is not None and error_running is not None
    assert child_jobs.update_job_result(error_complete.job_id, "complete", {"answer": "yes"})
    assert batch_queue.fail_batch_job_attempt("error-batch", "worker-2", error_claim.attempt_count, "batch failure", 0) == "error"

    partial_batch = batch_records.get_batch_job("partial-batch")
    error_batch = batch_records.get_batch_job("error-batch")
    assert partial_batch is not None and error_batch is not None
    assert partial_batch.status == "partial"
    assert partial_batch.result_bundle == {"resourceType": "Bundle", "id": "partial"}
    assert error_batch.status == "error"
    assert error_batch.result_bundle is not None
    assert error_batch.result_bundle["resourceType"] == "OperationOutcome"
    assert {child.status for child in child_jobs.get_jobs_for_batch("partial-batch")} == {"complete", "error"}
    assert {child.status for child in child_jobs.get_jobs_for_batch("error-batch")} == {"complete", "error"}


def test_ensure_job_reuses_one_logical_child_and_preserves_terminal_state(db):
    assert _create_batch_job("batch-1", "patient-1", "Registry")

    first = child_jobs.ensure_job("job-1", "batch-1", "patient-1", "Registry", "TaskA", "structured")
    assert first is not None
    assert child_jobs.update_job_result(first.job_id, "complete", {"results": {"TaskA": "yes"}})
    second = child_jobs.ensure_job("job-2", "batch-1", "patient-1", "Registry", "TaskA", "structured")

    assert second is not None
    assert second.job_id == "job-1"
    assert second.status == "complete"
    assert second.result == {"results": {"TaskA": "yes"}}
    assert len(child_jobs.get_jobs_for_batch("batch-1")) == 1


def test_fenced_writes_reject_expired_leases_and_other_batches(db):
    engine = db.engine
    assert _create_batch_job("batch-1", "patient-1", "Registry")
    assert _create_batch_job("batch-2", "patient-2", "Registry")
    claim = batch_queue.claim_next_batch_job("worker-1", 60)
    other_claim = batch_queue.claim_next_batch_job("worker-2", 60)
    assert claim is not None and other_claim is not None
    fence = {"worker_id": "worker-1", "attempt_count": claim.attempt_count}

    child = child_jobs.ensure_job("job-1", "batch-1", "patient-1", "Registry", "TaskA", "structured", **fence)
    assert child is not None
    assert batch_queue.renew_batch_job_lease("batch-1", "worker-1", claim.attempt_count, 60)
    # A valid fence for a different batch cannot write this batch's child.
    assert not child_jobs.update_job_result(child.job_id, "complete", {"wrong_batch": True}, batch_id="batch-2", worker_id="worker-2", attempt_count=other_claim.attempt_count)

    _expire_lease(engine, "batch-1")

    assert not batch_queue.renew_batch_job_lease("batch-1", "worker-1", claim.attempt_count, 60)
    assert not batch_records.update_batch_job_result("batch-1", {"resourceType": "Bundle", "id": "late"}, **fence)
    assert not batch_records.update_batch_job_status("batch-1", "complete", {"resourceType": "Bundle", "id": "late"}, **fence)
    assert not child_jobs.update_job_result(child.job_id, "complete", {"late": True}, batch_id="batch-1", **fence)
    assert child_jobs.ensure_job("job-2", "batch-1", "patient-1", "Registry", "TaskB", "structured", **fence) is None
    assert not batch_queue.release_batch_job_attempt("batch-1", "worker-1", claim.attempt_count)
    assert batch_queue.fail_batch_job_attempt("batch-1", "worker-1", claim.attempt_count, "late failure", 0) == "lost"

    batch = batch_records.get_batch_job("batch-1")
    assert batch is not None
    assert batch.status == "running"
    assert batch.result_bundle is None
    assert [job.task_name for job in child_jobs.get_jobs_for_batch("batch-1")] == ["TaskA"]
    assert child_jobs.get_jobs_for_batch("batch-1")[0].status == "running"
    assert child_jobs.get_jobs_for_batch("batch-1")[0].result is None

    retried, failed = batch_queue.recover_expired_batch_job_leases(0)
    assert (retried, failed) == (["batch-1"], [])
