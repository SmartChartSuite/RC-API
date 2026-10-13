"""DB state management for v1 — batch jobs, jobs, and questionnaire responses.

Tables:
    batch_jobs_v1       — one row per batch submission
    jobs_v1             — one row per task executed under a batch job
    questionnaire_responses — patient-linked response data

Uses SQLAlchemy Core + ORM with the same engine/session pattern as v0.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import ClassVar

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import (
    JSON,
    Column,
    ColumnElement,
    CursorResult,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    create_engine,
    delete,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateSchema

from src.services.errorhandler import make_operation_outcome
from src.util.settings import settings


def _utc_now() -> datetime:
    """Return the current UTC time for Python-side ORM defaults."""
    return datetime.now(UTC)


# ── ORM base ──────────────────────────────────────────────────────────────────


def _uses_named_schema(connection_string: str) -> bool:
    return not connection_string.startswith("sqlite")


def _metadata_schema(connection_string: str, schema: str | None) -> str | None:
    return schema if _uses_named_schema(connection_string) and schema else None


class Base(DeclarativeBase):
    metadata = MetaData(schema=_metadata_schema(settings.db_connection_string, settings.db_schema))
    type_annotation_map: ClassVar = {dict: JSON}


class BatchJobs(Base):
    __tablename__ = "batch_jobs_v1"
    __table_args__ = (
        Index("ix_batch_jobs_v1_worker_queue", "status", "next_attempt_at", "created_at"),
        Index("ix_batch_jobs_v1_worker_lease", "status", "lease_expires_at"),
    )

    batch_id: Mapped[str] = mapped_column(primary_key=True)
    patient_id: Mapped[str]
    job_package: Mapped[str]
    started_by: Mapped[str] = mapped_column(default="unknown")
    status: Mapped[str] = mapped_column(default="pending")
    result_bundle: Mapped[dict | None]
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    completed_at: Mapped[datetime | None]
    questionnaire_id: Mapped[str | None]
    questionnaire_title: Mapped[str | None]
    job_package_version: Mapped[str | None]
    requested_jobs: Mapped[list[str] | None] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(default=0)
    heartbeat_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None] = mapped_column(default=_utc_now, onupdate=_utc_now)
    worker_id: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]
    max_attempts: Mapped[int] = mapped_column(default=settings.batch_job_max_attempts)
    last_error: Mapped[str | None] = mapped_column(Text)


@dataclass(frozen=True)
class ClaimedBatchJob:
    """Detached execution inputs and lease identity for one claimed batch."""

    batch_id: str
    patient_id: str
    job_package: str
    questionnaire_id: str | None
    job_package_version: str | None
    requested_jobs: list[str] | None
    worker_id: str
    attempt_count: int
    result_bundle: dict | None
    max_attempts: int = settings.batch_job_max_attempts


@dataclass(frozen=True)
class LogicalJob:
    """Detached state for one logical structured or unstructured task."""

    job_id: str
    status: str
    result: dict | None


class Jobs(Base):
    __tablename__ = "jobs_v1"
    __table_args__ = (Index("ux_jobs_v1_logical_task", "batch_id", "task_type", "task_name", unique=True),)

    job_id: Mapped[str] = mapped_column(primary_key=True)
    batch_id = Column(String, ForeignKey("batch_jobs_v1.batch_id"), nullable=False)
    patient_id: Mapped[str]
    job_package: Mapped[str]
    task_name: Mapped[str]
    task_type: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    result: Mapped[dict | None]
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    completed_at: Mapped[datetime | None]


class QuestionnaireResponses(Base):
    __tablename__ = "questionnaire_responses"
    __table_args__ = (Index("ix_questionnaire_responses_batch_job_id", "batch_job_id"),)

    response_id: Mapped[str] = mapped_column(primary_key=True)
    batch_job_id: Mapped[str] = mapped_column(ForeignKey("batch_jobs_v1.batch_id", name="fk_questionnaire_responses_batch_job_id"))
    job_package: Mapped[str]
    patient_id: Mapped[str]
    last_updated_by: Mapped[str] = mapped_column(default="unknown")
    response: Mapped[dict]
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=_utc_now, onupdate=_utc_now)


# ── Engine + table creation ────────────────────────────────────────────────────

db_engine = create_engine(settings.db_connection_string, pool_pre_ping=True)
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


def get_batch_job(batch_id: str) -> BatchJobs | None:
    with Session(db_engine) as session:
        return session.get(BatchJobs, batch_id)


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
        stmt = stmt.where(BatchJobs.created_at >= datetime.combine(run_start_date, time.min, tzinfo=UTC))
    if run_end_date is not None:
        stmt = stmt.where(BatchJobs.created_at < datetime.combine(run_end_date, time.min, tzinfo=UTC) + timedelta(days=1))
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
    questionnaire_title: str | None = None,
    job_package_version: str | None = None,
    requested_jobs: list[str] | None = None,
    response_timestamp: datetime | None = None,
) -> bool:
    try:
        now = response_timestamp or datetime.now(UTC)
        with Session(db_engine) as session:
            session.add(
                BatchJobs(
                    batch_id=batch_id,
                    patient_id=patient_id,
                    job_package=job_package,
                    started_by=started_by,
                    status="pending",
                    questionnaire_id=questionnaire_id,
                    questionnaire_title=questionnaire_title,
                    job_package_version=job_package_version,
                    requested_jobs=requested_jobs or None,
                    attempt_count=0,
                    max_attempts=settings.batch_job_max_attempts,
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
    except SQLAlchemyError as exc:
        logger.error(f"Failed to create batch job {batch_id} with questionnaire response {response_id}: {exc}")
        return False


def _claimed_batch_snapshot(job: BatchJobs, worker_id: str) -> ClaimedBatchJob:
    return ClaimedBatchJob(
        batch_id=job.batch_id,
        patient_id=job.patient_id,
        job_package=job.job_package,
        questionnaire_id=job.questionnaire_id,
        job_package_version=job.job_package_version,
        requested_jobs=job.requested_jobs,
        worker_id=worker_id,
        attempt_count=job.attempt_count,
        result_bundle=job.result_bundle,
        max_attempts=job.max_attempts,
    )


def _owned_by(batch_id: str, worker_id: str, attempt_count: int, now: datetime) -> tuple[ColumnElement[bool], ...]:
    """Predicates for "this worker attempt still holds an unexpired lease on the batch".

    Every fenced write uses these, so once a lease expires only recovery can change the batch,
    even if the old worker is still running. Times come from the application clock, the same
    clock that wrote ``lease_expires_at``.
    """
    return (
        BatchJobs.batch_id == batch_id,
        BatchJobs.status == "running",
        BatchJobs.worker_id == worker_id,
        BatchJobs.attempt_count == attempt_count,
        BatchJobs.lease_expires_at > now,
    )


def _lock_owned_batch(session: Session, batch_id: str, worker_id: str, attempt_count: int, now: datetime) -> BatchJobs | None:
    """Lock the batch row for this transaction if the attempt still owns it, else return None.

    Holding the row lock (PostgreSQL ``SELECT ... FOR UPDATE``) keeps recovery and reclaim from
    taking the batch over between the ownership check and the writes that depend on it.
    """
    return session.scalar(select(BatchJobs).where(*_owned_by(batch_id, worker_id, attempt_count, now)).with_for_update())


def claim_next_batch_job(worker_id: str, lease_seconds: int) -> ClaimedBatchJob | None:
    """Atomically claim the oldest runnable pending batch for this worker."""
    for _ in range(5):
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with Session(db_engine) as session:
            candidate_id = session.scalar(
                select(BatchJobs.batch_id)
                .where(
                    BatchJobs.status == "pending",
                    BatchJobs.attempt_count < BatchJobs.max_attempts,
                    or_(BatchJobs.next_attempt_at.is_(None), BatchJobs.next_attempt_at <= now),
                )
                .order_by(BatchJobs.created_at, BatchJobs.batch_id)
                .limit(1)
            )
            if candidate_id is None:
                return None

            result: CursorResult = session.execute(
                update(BatchJobs)
                .where(
                    BatchJobs.batch_id == candidate_id,
                    BatchJobs.status == "pending",
                    BatchJobs.attempt_count < BatchJobs.max_attempts,
                    or_(BatchJobs.next_attempt_at.is_(None), BatchJobs.next_attempt_at <= now),
                )
                .values(
                    status="running",
                    worker_id=worker_id,
                    attempt_count=BatchJobs.attempt_count + 1,
                    heartbeat_at=now,
                    lease_expires_at=lease_expires_at,
                    next_attempt_at=None,
                    updated_at=now,
                )
            )  # type: ignore
            if result.rowcount != 1:
                session.rollback()
                continue

            claimed = session.get(BatchJobs, candidate_id)
            if claimed is None:
                session.rollback()
                continue
            snapshot = _claimed_batch_snapshot(claimed, worker_id)
            session.commit()
            logger.info(f"Worker {worker_id} claimed batch {candidate_id} attempt {snapshot.attempt_count}")
            return snapshot
    return None


def renew_batch_job_lease(batch_id: str, worker_id: str, attempt_count: int, lease_seconds: int) -> bool:
    """Extend a lease only while its worker and attempt still own the batch and the lease is unexpired."""
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        result: CursorResult = session.execute(
            update(BatchJobs)
            .where(*_owned_by(batch_id, worker_id, attempt_count, now))
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
        )  # type: ignore
        session.commit()
    return result.rowcount == 1


def release_batch_job_attempt(batch_id: str, worker_id: str, attempt_count: int) -> bool:
    """Return an owned attempt to the queue during graceful worker shutdown."""
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        result: CursorResult = session.execute(
            update(BatchJobs)
            .where(*_owned_by(batch_id, worker_id, attempt_count, now))
            .values(
                status="pending",
                worker_id=None,
                attempt_count=max(0, attempt_count - 1),
                heartbeat_at=None,
                lease_expires_at=None,
                next_attempt_at=now,
                updated_at=now,
            )
        )  # type: ignore
        if result.rowcount == 1:
            session.execute(
                update(Jobs)
                .where(
                    Jobs.batch_id == batch_id,
                    Jobs.status.not_in(("complete", "skipped")),
                )
                .values(status="pending", result=None, completed_at=None)
            )
        session.commit()
    released = result.rowcount == 1
    if released:
        logger.info(f"Released batch {batch_id} attempt {attempt_count} during worker shutdown")
    return released


def _has_completed_child(session: Session, batch_id: str) -> bool:
    return session.scalar(select(Jobs.job_id).where(Jobs.batch_id == batch_id, Jobs.status == "complete").limit(1)) is not None


def requeue_batch_job_for_child_retry(
    batch_id: str,
    worker_id: str,
    attempt_count: int,
    message: str,
    retry_delay_seconds: int,
) -> bool:
    """Requeue an owned batch after child errors, preserving completed and skipped children."""
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        job = _lock_owned_batch(session, batch_id, worker_id, attempt_count, now)
        if job is None:
            return False

        job.status = "pending"
        job.worker_id = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
        job.completed_at = None
        job.last_error = message
        job.updated_at = now
        session.execute(update(Jobs).where(Jobs.batch_id == batch_id, Jobs.status == "error").values(status="pending", result=None, completed_at=None))
        session.commit()
    logger.info(f"Requeued batch {batch_id} for child retry: {message}")
    return True


def fail_batch_job_attempt(
    batch_id: str,
    worker_id: str,
    attempt_count: int,
    message: str,
    retry_delay_seconds: int,
) -> str:
    """Release a failed owned attempt for retry, or terminally fail it."""
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        job = _lock_owned_batch(session, batch_id, worker_id, attempt_count, now)
        if job is None:
            return "lost"

        retrying = job.attempt_count < job.max_attempts
        job.worker_id = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.last_error = message
        job.updated_at = now
        if retrying:
            job.status = "pending"
            job.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
            job.completed_at = None
            session.execute(
                update(Jobs)
                .where(
                    Jobs.batch_id == batch_id,
                    Jobs.status.not_in(("complete", "skipped")),
                )
                .values(status="pending", result=None, completed_at=None)
            )
            outcome = "retry"
        else:
            has_completed_result = _has_completed_child(session, batch_id) and isinstance(job.result_bundle, dict) and job.result_bundle.get("resourceType") == "Bundle"
            outcome = "partial" if has_completed_result else "error"
            job.status = outcome
            job.next_attempt_at = None
            job.completed_at = now
            if outcome == "error" and job.result_bundle is None:
                job.result_bundle = make_operation_outcome("exception", message)
            session.execute(
                update(Jobs)
                .where(
                    Jobs.batch_id == batch_id,
                    Jobs.status.not_in(("complete", "error", "skipped")),
                )
                .values(status="error", result={"message": message}, completed_at=now)
            )
        session.commit()
    logger.warning(f"Batch {batch_id} attempt {attempt_count} ended with {outcome}: {message}")
    return outcome


def recover_expired_batch_job_leases(retry_delay_seconds: int) -> tuple[list[str], list[str]]:
    """Requeue expired attempts, terminally failing batches with no attempts left."""
    now = datetime.now(UTC)
    message = "Batch worker lease expired before completion."
    retried: list[str] = []
    failed: list[str] = []
    with Session(db_engine) as session:
        expired = list(
            session.execute(
                select(BatchJobs)
                .where(
                    BatchJobs.status == "running",
                    or_(
                        BatchJobs.lease_expires_at.is_(None),
                        BatchJobs.lease_expires_at <= now,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for job in expired:
            job.worker_id = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.last_error = message
            job.updated_at = now
            if job.attempt_count < job.max_attempts:
                job.status = "pending"
                job.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
                job.completed_at = None
                retried.append(job.batch_id)
                session.execute(
                    update(Jobs)
                    .where(
                        Jobs.batch_id == job.batch_id,
                        Jobs.status.not_in(("complete", "skipped")),
                    )
                    .values(status="pending", result=None, completed_at=None)
                )
            else:
                has_completed_result = _has_completed_child(session, job.batch_id) and isinstance(job.result_bundle, dict) and job.result_bundle.get("resourceType") == "Bundle"
                job.status = "partial" if has_completed_result else "error"
                job.next_attempt_at = None
                job.completed_at = now
                if job.status == "error" and job.result_bundle is None:
                    job.result_bundle = make_operation_outcome("exception", message)
                failed.append(job.batch_id)
                session.execute(
                    update(Jobs)
                    .where(
                        Jobs.batch_id == job.batch_id,
                        Jobs.status.not_in(("complete", "error", "skipped")),
                    )
                    .values(status="error", result={"message": message}, completed_at=now)
                )
        if expired:
            session.commit()
    if retried or failed:
        logger.warning(f"Recovered expired batch leases: retry={retried}, error={failed}")
    return retried, failed


def update_batch_job_status(
    batch_id: str,
    status: str,
    result_bundle: dict | None = None,
    *,
    worker_id: str | None = None,
    attempt_count: int | None = None,
) -> bool:
    """Update batch status, optionally fenced to the currently owned worker attempt."""
    now = datetime.now(UTC)
    vals: dict = {"status": status, "updated_at": now}
    if status == "running":
        vals["heartbeat_at"] = now
    if status in {"complete", "partial", "error"}:
        vals.update(
            completed_at=now,
            worker_id=None,
            lease_expires_at=None,
            next_attempt_at=None,
        )
    if result_bundle is not None:
        vals["result_bundle"] = result_bundle

    stmt = update(BatchJobs).where(BatchJobs.batch_id == batch_id)
    if worker_id is not None or attempt_count is not None:
        if worker_id is None or attempt_count is None:
            raise ValueError("worker_id and attempt_count must be provided together")
        stmt = stmt.where(*_owned_by(batch_id, worker_id, attempt_count, now))
    with Session(db_engine) as session:
        result: CursorResult = session.execute(stmt.values(**vals))  # type: ignore
        session.commit()
    updated = result.rowcount == 1
    if updated:
        logger.info(f"Updated batch job {batch_id} → status={status}")
    return updated


def update_batch_job_result(
    batch_id: str,
    result_bundle: dict,
    *,
    worker_id: str | None = None,
    attempt_count: int | None = None,
) -> bool:
    """Persist a partial result snapshot, optionally fenced to one worker attempt."""
    now = datetime.now(UTC)
    stmt = update(BatchJobs).where(BatchJobs.batch_id == batch_id)
    if worker_id is not None or attempt_count is not None:
        if worker_id is None or attempt_count is None:
            raise ValueError("worker_id and attempt_count must be provided together")
        stmt = stmt.where(*_owned_by(batch_id, worker_id, attempt_count, now))
    with Session(db_engine) as session:
        result: CursorResult = session.execute(stmt.values(result_bundle=result_bundle, heartbeat_at=now, updated_at=now))  # type: ignore
        session.commit()
    updated = result.rowcount == 1
    if updated:
        logger.debug(f"Updated partial result Bundle for batch job {batch_id}")
    return updated


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


def _ownership_exists(batch_id: str, worker_id: str, attempt_count: int, now: datetime):
    return exists(select(BatchJobs.batch_id).where(*_owned_by(batch_id, worker_id, attempt_count, now)))


def ensure_job(
    job_id: str,
    batch_id: str,
    patient_id: str,
    job_package: str,
    task_name: str,
    task_type: str,
    *,
    worker_id: str | None = None,
    attempt_count: int | None = None,
) -> LogicalJob | None:
    """Create or reuse one logical child task without resetting terminal state.

    When fenced, the ownership check locks the batch row, and the child upsert runs in the same
    transaction, so a lost or expired attempt cannot create or reset children.
    """
    if (worker_id is None) != (attempt_count is None):
        raise ValueError("worker_id and attempt_count must be provided together")
    try:
        with Session(db_engine) as session:
            if worker_id is not None and attempt_count is not None and _lock_owned_batch(session, batch_id, worker_id, attempt_count, datetime.now(UTC)) is None:
                return None
            existing = session.scalar(
                select(Jobs).where(
                    Jobs.batch_id == batch_id,
                    Jobs.task_type == task_type,
                    Jobs.task_name == task_name,
                )
            )
            if existing is None:
                existing = Jobs(
                    job_id=job_id,
                    batch_id=batch_id,
                    patient_id=patient_id,
                    job_package=job_package,
                    task_name=task_name,
                    task_type=task_type,
                    status="running",
                )
                session.add(existing)
            elif existing.status not in {"complete", "error", "skipped"}:
                existing.status = "running"
                existing.completed_at = None
            session.flush()
            snapshot = LogicalJob(existing.job_id, existing.status, existing.result)
            session.commit()
        return snapshot
    except SQLAlchemyError as exc:
        logger.error(f"Failed to ensure job {job_id}: {exc}")
        return None


def get_jobs_for_batch(batch_id: str) -> list[Jobs]:
    """Return all child task jobs for a batch submission."""
    with Session(db_engine) as session:
        return list(session.execute(select(Jobs).where(Jobs.batch_id == batch_id)).scalars().all())


def update_job_result(
    job_id: str,
    status: str,
    result: dict | None = None,
    *,
    batch_id: str | None = None,
    worker_id: str | None = None,
    attempt_count: int | None = None,
) -> bool:
    """Update a child result, optionally fenced to its owning batch attempt."""
    if any(value is not None for value in (batch_id, worker_id, attempt_count)) and any(value is None for value in (batch_id, worker_id, attempt_count)):
        raise ValueError("batch_id, worker_id, and attempt_count must be provided together")
    values: dict = {"status": status, "result": result}
    now = datetime.now(UTC)
    if status in {"complete", "error", "skipped"}:
        values["completed_at"] = now
    stmt = update(Jobs).where(Jobs.job_id == job_id)
    if batch_id is not None and worker_id is not None and attempt_count is not None:
        stmt = stmt.where(Jobs.batch_id == batch_id, _ownership_exists(batch_id, worker_id, attempt_count, now))
    with Session(db_engine) as session:
        update_result: CursorResult = session.execute(stmt.values(**values))  # type: ignore
        session.commit()
    updated = update_result.rowcount == 1
    if updated:
        logger.debug(f"Updated job {job_id} → status={status}")
    return updated


# ── Questionnaire Response CRUD ────────────────────────────────────────────────


def create_response(
    response_id: str,
    batch_job_id: str,
    job_package: str,
    patient_id: str,
    last_updated_by: str,
    response_body: dict,
    response_timestamp: datetime | None = None,
) -> bool:
    try:
        timestamp = response_timestamp or datetime.now(UTC)
        with Session(db_engine) as session:
            session.add(
                QuestionnaireResponses(
                    response_id=response_id,
                    batch_job_id=batch_job_id,
                    job_package=job_package,
                    patient_id=patient_id,
                    last_updated_by=last_updated_by,
                    response=response_body,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.commit()
        logger.info(f"Created questionnaire response {response_id}")
        return True
    except SQLAlchemyError as exc:
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


def get_responses_for_batches(batch_ids: list[str]) -> dict[str, QuestionnaireResponses]:
    """Return one response per requested batch, keeping the lexicographically first response ID."""
    if not batch_ids:
        return {}

    with Session(db_engine) as session:
        stmt = select(QuestionnaireResponses).where(QuestionnaireResponses.batch_job_id.in_(batch_ids)).order_by(QuestionnaireResponses.response_id)
        responses = session.execute(stmt).scalars().all()

    responses_by_batch: dict[str, QuestionnaireResponses] = {}
    for response in responses:
        responses_by_batch.setdefault(response.batch_job_id, response)
    return responses_by_batch


def update_response_body(response_id: str, response_body: dict, last_updated_by: str, updated_at: datetime | None = None) -> bool:
    timestamp = updated_at or datetime.now(UTC)
    with Session(db_engine) as session:
        result: CursorResult = session.execute(
            update(QuestionnaireResponses).where(QuestionnaireResponses.response_id == response_id).values(response=response_body, last_updated_by=last_updated_by, updated_at=timestamp)
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
