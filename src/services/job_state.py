"""DB state management for v1 — batch jobs, jobs, and questionnaire responses.

Tables:
    batch_jobs_v1       — one row per batch submission
    jobs_v1             — one row per task executed under a batch job
    questionnaire_responses — patient-linked response data

Uses SQLAlchemy Core + ORM with the same engine/session pattern as v0.
"""

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import (
    JSON,
    Column,
    CursorResult,
    exists,
    ForeignKey,
    MetaData,
    String,
    create_engine,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateSchema

from src.services.errorhandler import make_operation_outcome
from src.util.settings import db_connection_string, db_schema

# ── ORM base ──────────────────────────────────────────────────────────────────


def _uses_named_schema(connection_string: str) -> bool:
    return not connection_string.startswith("sqlite")


def _metadata_schema(connection_string: str, schema: str | None) -> str | None:
    return schema if _uses_named_schema(connection_string) and schema else None


class Base(DeclarativeBase):
    metadata = MetaData(schema=_metadata_schema(db_connection_string, db_schema))
    type_annotation_map = {dict: JSON}


class BatchJobs(Base):
    __tablename__ = "batch_jobs_v1"

    batch_id: Mapped[str] = mapped_column(primary_key=True)
    patient_id: Mapped[str]
    job_package: Mapped[str]
    started_by: Mapped[str] = mapped_column(default="unknown")
    status: Mapped[str] = mapped_column(default="pending")
    result_bundle: Mapped[dict | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None]
    questionnaire_id: Mapped[str | None]
    job_package_version: Mapped[str | None]
    requested_jobs: Mapped[list[str] | None] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(default=0)
    heartbeat_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None] = mapped_column(default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))


class Jobs(Base):
    __tablename__ = "jobs_v1"

    job_id: Mapped[str] = mapped_column(primary_key=True)
    batch_id = Column(String, ForeignKey("batch_jobs_v1.batch_id"), nullable=False)
    patient_id: Mapped[str]
    job_package: Mapped[str]
    task_name: Mapped[str]
    task_type: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    result: Mapped[dict | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None]


class QuestionnaireResponses(Base):
    __tablename__ = "questionnaire_responses"

    response_id: Mapped[str] = mapped_column(primary_key=True)
    batch_job_id: Mapped[str]
    job_package: Mapped[str]
    patient_id: Mapped[str]
    last_updated_by: Mapped[str] = mapped_column(default="unknown")
    response: Mapped[dict]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))


# ── Engine + table creation ────────────────────────────────────────────────────

db_engine = create_engine(db_connection_string, pool_pre_ping=True)
_ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _ensure_schema_exists() -> None:
    schema = Base.metadata.schema
    if not schema:
        return
    with db_engine.begin() as connection:
        connection.execute(CreateSchema(schema, if_not_exists=True))


def _head_revision() -> str | None:
    config = Config(_ALEMBIC_CONFIG_PATH)
    return ScriptDirectory.from_config(config).get_current_head()


def _current_database_revision() -> str | None:
    with db_engine.connect() as connection:
        migration_context = MigrationContext.configure(
            connection,
            opts={"version_table_schema": Base.metadata.schema},
        )
        return migration_context.get_current_revision()


def initialize_db() -> None:
    """Verify connectivity and require the database to be migrated to Alembic head."""
    try:
        _ensure_schema_exists()
        current_revision = _current_database_revision()
        head_revision = _head_revision()
        if current_revision != head_revision:
            raise RuntimeError(f"Database schema revision is {current_revision or 'unversioned'}; expected {head_revision}. Run 'uv run alembic upgrade head' before starting the API.")
        logger.info(f"Database schema verified at Alembic revision {head_revision}.")
    except Exception as exc:
        logger.error(f"Database initialization failed: {exc}")
        raise


# ── Batch Job CRUD ─────────────────────────────────────────────────────────────


def create_batch_job(batch_id: str, patient_id: str, job_package: str, started_by: str = "unknown") -> bool:
    try:
        with Session(db_engine) as session:
            session.add(BatchJobs(batch_id=batch_id, patient_id=patient_id, job_package=job_package, started_by=started_by, status="pending"))
            session.commit()
        logger.info(f"Created batch job {batch_id}")
        return True
    except Exception as exc:
        logger.error(f"Failed to create batch job {batch_id}: {exc}")
        return False


def get_batch_job(batch_id: str) -> BatchJobs | None:
    with Session(db_engine) as session:
        return session.get(BatchJobs, batch_id)


def get_all_batch_jobs() -> list[BatchJobs]:
    with Session(db_engine) as session:
        return list(session.execute(select(BatchJobs)).scalars().all())


def query_batch_jobs(
    *,
    statuses: list[str] | None = None,
    job_package: str | None = None,
    questionnaire_response_statuses: list[str] | None = None,
    run_start_date: date | None = None,
    run_end_date: date | None = None,
) -> list[BatchJobs]:
    """Return batch jobs filtered at the database level.

    Filters that depend on external Patient data are applied by the caller.
    Results are ordered newest-first.
    """
    stmt = select(BatchJobs)
    if statuses:
        stmt = stmt.where(func.lower(BatchJobs.status).in_(statuses))
    if job_package:
        stmt = stmt.where(func.lower(BatchJobs.job_package) == job_package.casefold())
    if questionnaire_response_statuses:
        stmt = stmt.where(
            exists(
                select(QuestionnaireResponses.response_id).where(
                    QuestionnaireResponses.batch_job_id == BatchJobs.batch_id,
                    func.lower(QuestionnaireResponses.response["status"].as_string()).in_(questionnaire_response_statuses),
                )
            )
        )
    if run_start_date is not None:
        stmt = stmt.where(BatchJobs.created_at >= datetime.combine(run_start_date, time.min, tzinfo=timezone.utc))
    if run_end_date is not None:
        stmt = stmt.where(BatchJobs.created_at < datetime.combine(run_end_date, time.min, tzinfo=timezone.utc) + timedelta(days=1))
    stmt = stmt.order_by(BatchJobs.created_at.desc())
    with Session(db_engine) as session:
        return list(session.execute(stmt).scalars().all())


def create_batch_job_with_response(
    batch_id: str,
    patient_id: str,
    job_package: str,
    started_by: str,
    response_id: str,
    response_body: dict,
    questionnaire_id: str,
    job_package_version: str | None = None,
    requested_jobs: list[str] | None = None,
) -> bool:
    try:
        now = datetime.now(timezone.utc)
        with Session(db_engine) as session:
            session.add(
                BatchJobs(
                    batch_id=batch_id,
                    patient_id=patient_id,
                    job_package=job_package,
                    started_by=started_by,
                    status="pending",
                    questionnaire_id=questionnaire_id,
                    job_package_version=job_package_version,
                    requested_jobs=requested_jobs or None,
                    attempt_count=0,
                    updated_at=now,
                )
            )
            session.add(
                QuestionnaireResponses(
                    response_id=response_id,
                    batch_job_id=batch_id,
                    job_package=job_package,
                    patient_id=patient_id,
                    last_updated_by=started_by,
                    response=response_body,
                )
            )
            session.commit()
        logger.info(f"Created batch job {batch_id} with questionnaire response {response_id}")
        return True
    except Exception as exc:
        logger.error(f"Failed to create batch job {batch_id} with questionnaire response {response_id}: {exc}")
        return False


def start_batch_job_attempt(batch_id: str) -> None:
    """Mark a batch running and atomically increment its execution attempt."""
    now = datetime.now(timezone.utc)
    with Session(db_engine) as session:
        session.execute(
            update(BatchJobs)
            .where(BatchJobs.batch_id == batch_id)
            .values(
                status="running",
                attempt_count=BatchJobs.attempt_count + 1,
                heartbeat_at=now,
                updated_at=now,
            )
        )
        session.commit()
    logger.info(f"Started execution attempt for batch job {batch_id}")


def touch_batch_job_heartbeat(batch_id: str) -> None:
    """Refresh liveness metadata for a running batch job."""
    now = datetime.now(timezone.utc)
    with Session(db_engine) as session:
        session.execute(update(BatchJobs).where(BatchJobs.batch_id == batch_id, BatchJobs.status == "running").values(heartbeat_at=now, updated_at=now))
        session.commit()


def reconcile_stale_batch_jobs(stale_after_seconds: int) -> list[str]:
    """Mark stale pending/running batches and their unfinished child jobs as interrupted."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=stale_after_seconds)
    stale_reference = func.coalesce(BatchJobs.heartbeat_at, BatchJobs.updated_at, BatchJobs.created_at)
    message = "Batch execution was interrupted before completion."

    with Session(db_engine) as session:
        stale_jobs = list(
            session.execute(
                select(BatchJobs).where(
                    BatchJobs.status.in_(("pending", "running")),
                    stale_reference < cutoff,
                )
            )
            .scalars()
            .all()
        )
        stale_ids = [job.batch_id for job in stale_jobs]
        for job in stale_jobs:
            job.status = "error"
            job.completed_at = now
            job.updated_at = now
            if job.result_bundle is None:
                job.result_bundle = make_operation_outcome("exception", message)

        if stale_ids:
            session.execute(
                update(Jobs)
                .where(
                    Jobs.batch_id.in_(stale_ids),
                    Jobs.status.not_in(("complete", "error", "skipped")),
                )
                .values(status="error", result={"message": message}, completed_at=now)
            )
            session.commit()

    if stale_ids:
        logger.warning(f"Marked {len(stale_ids)} stale batch job(s) as interrupted: {stale_ids}")
    return stale_ids


def update_batch_job_status(batch_id: str, status: str, result_bundle: dict | None = None) -> None:
    now = datetime.now(timezone.utc)
    vals: dict = {"status": status, "updated_at": now}
    if status == "running":
        vals["heartbeat_at"] = now
    if status in {"complete", "error"}:
        vals["completed_at"] = now
    if result_bundle is not None:
        vals["result_bundle"] = result_bundle
    with Session(db_engine) as session:
        session.execute(update(BatchJobs).where(BatchJobs.batch_id == batch_id).values(**vals))
        session.commit()
    logger.info(f"Updated batch job {batch_id} → status={status}")


def update_batch_job_result(batch_id: str, result_bundle: dict) -> None:
    """Persist a partial result snapshot and refresh batch liveness metadata."""
    now = datetime.now(timezone.utc)
    with Session(db_engine) as session:
        session.execute(update(BatchJobs).where(BatchJobs.batch_id == batch_id).values(result_bundle=result_bundle, heartbeat_at=now, updated_at=now))
        session.commit()
    logger.debug(f"Updated partial result Bundle for batch job {batch_id}")


def delete_batch_job_record(batch_id: str) -> JSONResponse:
    existing = get_batch_job(batch_id)
    if not existing:
        return JSONResponse(make_operation_outcome("not-found", f"Batch Job ID {batch_id} was not found."), 404)
    with Session(db_engine) as session:
        session.execute(delete(Jobs).where(Jobs.batch_id == batch_id))
        session.execute(delete(QuestionnaireResponses).where(QuestionnaireResponses.batch_job_id == batch_id))
        session.execute(delete(BatchJobs).where(BatchJobs.batch_id == batch_id))
        session.commit()
    logger.info(f"Deleted batch job {batch_id}, its child jobs, and linked questionnaire responses.")
    return JSONResponse(make_operation_outcome("deleted", f"Batch Job {batch_id} deleted successfully.", "information"))


# ── Job CRUD ──────────────────────────────────────────────────────────────────


def create_job(job_id: str, batch_id: str, patient_id: str, job_package: str, task_name: str, task_type: str, status: str = "pending") -> bool:
    try:
        with Session(db_engine) as session:
            session.add(
                Jobs(
                    job_id=job_id,
                    batch_id=batch_id,
                    patient_id=patient_id,
                    job_package=job_package,
                    task_name=task_name,
                    task_type=task_type,
                    status=status,
                )
            )
            session.commit()
        logger.debug(f"Created job {job_id} under batch {batch_id} for task {task_type}:{task_name}")
        return True
    except Exception as exc:
        logger.error(f"Failed to create job {job_id}: {exc}")
        return False


def get_jobs_for_batch(batch_id: str) -> list[Jobs]:
    """Return all child task jobs for a batch submission."""
    with Session(db_engine) as session:
        return list(session.execute(select(Jobs).where(Jobs.batch_id == batch_id)).scalars().all())


def update_job_result(job_id: str, status: str, result: dict | None = None) -> None:
    values: dict = {"status": status, "result": result}
    if status in {"complete", "error", "skipped"}:
        values["completed_at"] = datetime.now(timezone.utc)
    with Session(db_engine) as session:
        session.execute(update(Jobs).where(Jobs.job_id == job_id).values(**values))
        session.commit()
    logger.debug(f"Updated job {job_id} → status={status}")


def mark_unfinished_jobs_error(batch_id: str, message: str) -> int:
    """Mark only pending/running child jobs as failed after a batch-level exception."""
    with Session(db_engine) as session:
        result: CursorResult = session.execute(
            update(Jobs)
            .where(
                Jobs.batch_id == batch_id,
                Jobs.status.not_in(("complete", "error", "skipped")),
            )
            .values(
                status="error",
                result={"message": message},
                completed_at=datetime.now(timezone.utc),
            )
        )  # type: ignore
        session.commit()
    updated = result.rowcount or 0
    logger.debug(f"Marked {updated} unfinished job(s) as error for batch {batch_id}")
    return updated


# ── Questionnaire Response CRUD ────────────────────────────────────────────────


def create_response(response_id: str, batch_job_id: str, job_package: str, patient_id: str, last_updated_by: str, response_body: dict) -> bool:
    try:
        with Session(db_engine) as session:
            session.add(
                QuestionnaireResponses(
                    response_id=response_id,
                    batch_job_id=batch_job_id,
                    job_package=job_package,
                    patient_id=patient_id,
                    last_updated_by=last_updated_by,
                    response=response_body,
                )
            )
            session.commit()
        logger.info(f"Created questionnaire response {response_id}")
        return True
    except Exception as exc:
        logger.error(f"Failed to create response {response_id}: {exc}")
        return False


def get_response(response_id: str) -> QuestionnaireResponses | None:
    with Session(db_engine) as session:
        return session.get(QuestionnaireResponses, response_id)


def get_responses(batch_job_id: str | None = None, job_package: str | None = None) -> list[QuestionnaireResponses]:
    with Session(db_engine) as session:
        stmt = select(QuestionnaireResponses)
        if batch_job_id:
            stmt = stmt.where(QuestionnaireResponses.batch_job_id == batch_job_id)
        if job_package:
            stmt = stmt.where(QuestionnaireResponses.job_package == job_package)
        return list(session.execute(stmt).scalars().all())


def update_response_body(response_id: str, response_body: dict, last_updated_by: str) -> bool:
    with Session(db_engine) as session:
        result: CursorResult = session.execute(
            update(QuestionnaireResponses)
            .where(QuestionnaireResponses.response_id == response_id)
            .values(response=response_body, last_updated_by=last_updated_by, updated_at=datetime.now(timezone.utc))
        )  # type: ignore
        session.commit()
    updated = result.rowcount > 0
    if not updated:
        logger.warning(f"Response {response_id} not found for update.")
    return updated


def delete_response_record(response_id: str) -> JSONResponse:
    existing = get_response(response_id)
    if not existing:
        return JSONResponse(make_operation_outcome("not-found", f"Response {response_id} was not found."), 404)
    with Session(db_engine) as session:
        session.execute(delete(QuestionnaireResponses).where(QuestionnaireResponses.response_id == response_id))
        session.commit()
    return JSONResponse(make_operation_outcome("deleted", f"Response {response_id} deleted successfully.", "information"))
