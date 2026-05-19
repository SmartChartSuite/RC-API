"""DB state management for v1 — batch jobs, jobs, and questionnaire responses.

Tables:
    batch_jobs_v1       — one row per batch submission
    jobs_v1             — one row per async job run (child of batch)
    questionnaire_responses — patient-linked response data

Uses SQLAlchemy Core + ORM with the same engine/session pattern as v0.
"""

from datetime import datetime, timezone

from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import (
    JSON,
    Column,
    CursorResult,
    ForeignKey,
    String,
    create_engine,
    delete,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.services.errorhandler import make_operation_outcome
from src.util.settings import db_connection_string, db_schema

# ── ORM base ──────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON}


class BatchJobs(Base):
    __tablename__ = "batch_jobs_v1"

    batch_id: Mapped[str] = mapped_column(primary_key=True)
    patient_id: Mapped[str]
    job_package: Mapped[str]
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
    status: Mapped[str] = mapped_column(default="pending")
    tasks: Mapped[dict | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None]


class QuestionnaireResponses(Base):
    __tablename__ = "questionnaire_responses"

    response_id: Mapped[str] = mapped_column(primary_key=True)
    batch_job_id: Mapped[str]
    job_package: Mapped[str]
    patient_id: Mapped[str]
    user_id: Mapped[str] = mapped_column(default="unknown")
    response: Mapped[dict]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))


# ── Engine + table creation ────────────────────────────────────────────────────

db_engine = create_engine(db_connection_string, pool_pre_ping=True)

# SQLite does not support named schemas — only apply schema for other backends
_is_sqlite = db_connection_string.startswith("sqlite")
_schema_args: dict = {} if _is_sqlite else ({"schema": db_schema} if db_schema else {})


def _table_args() -> dict:
    """Return __table_args__ dict appropriate for the current DB backend."""
    return _schema_args


try:
    Base.metadata.create_all(db_engine)
    logger.info("v1 DB tables created/verified.")
except Exception as exc:
    logger.error(f"Failed to create v1 DB tables: {exc}")


# ── Batch Job CRUD ─────────────────────────────────────────────────────────────


def create_batch_job(batch_id: str, patient_id: str, job_package: str) -> bool:
    try:
        with Session(db_engine) as session:
            session.add(BatchJobs(batch_id=batch_id, patient_id=patient_id, job_package=job_package, status="pending"))
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
        session.execute(delete(BatchJobs).where(BatchJobs.batch_id == batch_id))
        session.commit()
    logger.info(f"Deleted batch job {batch_id} and its child jobs.")
    return JSONResponse(make_operation_outcome("deleted", f"Batch Job {batch_id} deleted successfully.", "information"))


# ── Job CRUD ──────────────────────────────────────────────────────────────────


def create_job(job_id: str, batch_id: str, patient_id: str, job_package: str) -> bool:
    try:
        with Session(db_engine) as session:
            session.add(Jobs(job_id=job_id, batch_id=batch_id, patient_id=patient_id, job_package=job_package, status="running"))
            session.commit()
        logger.info(f"Created job {job_id} under batch {batch_id}")
        return True
    except Exception as exc:
        logger.error(f"Failed to create job {job_id}: {exc}")
        return False


def update_job_complete(job_id: str, tasks: list[dict], result_bundle: dict | None = None) -> None:
    with Session(db_engine) as session:
        session.execute(update(Jobs).where(Jobs.job_id == job_id).values(status="complete", tasks=tasks, completed_at=datetime.now(timezone.utc)))
        session.commit()
    logger.info(f"Marked job {job_id} complete with {len(tasks)} task(s).")


# ── Questionnaire Response CRUD ────────────────────────────────────────────────


def create_response(response_id: str, batch_job_id: str, job_package: str, patient_id: str, user_id: str, response_body: dict) -> bool:
    try:
        with Session(db_engine) as session:
            session.add(
                QuestionnaireResponses(
                    response_id=response_id,
                    batch_job_id=batch_job_id,
                    job_package=job_package,
                    patient_id=patient_id,
                    user_id=user_id,
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


def get_responses(batch_job_id: str | None = None, job_package: str | None = None, user_id: str | None = None) -> list[QuestionnaireResponses]:
    with Session(db_engine) as session:
        stmt = select(QuestionnaireResponses)
        if batch_job_id:
            stmt = stmt.where(QuestionnaireResponses.batch_job_id == batch_job_id)
        if job_package:
            stmt = stmt.where(QuestionnaireResponses.job_package == job_package)
        if user_id:
            stmt = stmt.where(QuestionnaireResponses.user_id == user_id)
        return list(session.execute(stmt).scalars().all())


def update_response_body(response_id: str, response_body: dict) -> bool:
    with Session(db_engine) as session:
        result: CursorResult = session.execute(
            update(QuestionnaireResponses).where(QuestionnaireResponses.response_id == response_id).values(response=response_body, updated_at=datetime.now(timezone.utc))
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
