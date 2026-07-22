"""DB state management for v1 — batch jobs, jobs, and questionnaire responses.

Tables:
    batch_jobs_v1       — one row per batch submission
    jobs_v1             — one row per task executed under a batch job
    questionnaire_responses — patient-linked response data

Uses SQLAlchemy Core + ORM with the same engine/session pattern as v0.
"""

from datetime import date, datetime, time, timedelta, timezone

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


def _ensure_schema_exists() -> None:
    schema = Base.metadata.schema
    if not schema:
        return
    with db_engine.begin() as connection:
        connection.execute(CreateSchema(schema, if_not_exists=True))


def initialize_db() -> None:
    try:
        _ensure_schema_exists()
        Base.metadata.create_all(db_engine)
        logger.info("v1 DB tables created/verified.")
    except Exception as exc:
        logger.error(f"Failed to create v1 DB tables: {exc}")
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
) -> bool:
    try:
        with Session(db_engine) as session:
            session.add(BatchJobs(batch_id=batch_id, patient_id=patient_id, job_package=job_package, started_by=started_by, status="pending"))
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


def update_batch_job_status(batch_id: str, status: str, result_bundle: dict | None = None) -> None:
    vals: dict = {"status": status}
    if status == "complete":
        vals["completed_at"] = datetime.now(timezone.utc)
    if result_bundle is not None:
        vals["result_bundle"] = result_bundle
    with Session(db_engine) as session:
        session.execute(update(BatchJobs).where(BatchJobs.batch_id == batch_id).values(**vals))
        session.commit()
    logger.info(f"Updated batch job {batch_id} → status={status}")


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


def update_job_result(job_id: str, status: str, result: dict | None = None) -> None:
    values: dict = {"status": status, "result": result}
    if status in {"complete", "error", "skipped"}:
        values["completed_at"] = datetime.now(timezone.utc)
    with Session(db_engine) as session:
        session.execute(update(Jobs).where(Jobs.job_id == job_id).values(**values))
        session.commit()
    logger.debug(f"Updated job {job_id} → status={status}")


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
