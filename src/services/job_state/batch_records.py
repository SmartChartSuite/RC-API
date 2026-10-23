"""Persist batch-job records, fenced result snapshots, and retained batch documents.

This module owns batch metadata CRUD, database-filtered batch queries, submission/response creation,
status and result writes, deletion, and the document store used by orchestration. It collaborates with
``models`` for tables, ``batch_queue`` for shared ownership locks and predicates, and ``database`` for
the engine. Lease claiming, child-task state, QuestionnaireResponse CRUD, HTTP mapping, and FHIR fetches
do not belong here.
"""

from datetime import UTC, date, datetime, time, timedelta

from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import CursorResult, delete, exists, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, defer

from src.services.errorhandler import make_operation_outcome
from src.services.job_state import database
from src.services.job_state.batch_queue import _lock_owned_batch, _owned_by
from src.services.job_state.models import BatchJobDocuments, BatchJobs, Jobs, QuestionnaireResponses
from src.util.settings import settings


def get_batch_job(batch_id: str) -> BatchJobs | None:
    with Session(database.db_engine) as session:
        return session.get(BatchJobs, batch_id)


def get_batch_job_summary(batch_id: str) -> BatchJobs | None:
    """Return batch metadata without loading the potentially large result Bundle."""
    stmt = select(BatchJobs).options(defer(BatchJobs.result_bundle)).where(BatchJobs.batch_id == batch_id)
    with Session(database.db_engine) as session:
        return session.scalar(stmt)


def query_batch_jobs(
    *,
    statuses: list[str] | None = None,
    job_package: str | None = None,
    questionnaire_response_statuses: list[str] | None = None,
    run_start_date: date | None = None,
    run_end_date: date | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[BatchJobs], int]:
    """Return database-filtered batch jobs and their total count.

    Filters that depend on external Patient data are applied by the caller.
    Results are ordered newest-first with a batch ID tie-breaker. The result
    Bundle is deferred because list and status callers only need batch metadata.
    """
    filtered_stmt = select(BatchJobs)
    if statuses:
        filtered_stmt = filtered_stmt.where(func.lower(BatchJobs.status).in_(statuses))
    if job_package:
        filtered_stmt = filtered_stmt.where(func.lower(BatchJobs.job_package) == job_package.casefold())
    if questionnaire_response_statuses:
        filtered_stmt = filtered_stmt.where(
            exists(
                select(QuestionnaireResponses.response_id).where(
                    QuestionnaireResponses.batch_job_id == BatchJobs.batch_id,
                    func.lower(QuestionnaireResponses.response["status"].as_string()).in_(questionnaire_response_statuses),
                )
            )
        )
    if run_start_date is not None:
        filtered_stmt = filtered_stmt.where(BatchJobs.created_at >= datetime.combine(run_start_date, time.min, tzinfo=UTC))
    if run_end_date is not None:
        filtered_stmt = filtered_stmt.where(BatchJobs.created_at < datetime.combine(run_end_date, time.min, tzinfo=UTC) + timedelta(days=1))

    count_subquery = filtered_stmt.with_only_columns(BatchJobs.batch_id, maintain_column_froms=True).subquery()
    count_stmt = select(func.count()).select_from(count_subquery)
    stmt = filtered_stmt.options(defer(BatchJobs.result_bundle)).order_by(BatchJobs.created_at.desc(), BatchJobs.batch_id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    if offset:
        stmt = stmt.offset(offset)

    with Session(database.db_engine) as session:
        total = session.scalar(count_stmt) or 0
        jobs = list(session.execute(stmt).scalars().all())
    return jobs, total


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
        with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
        result: CursorResult = session.execute(stmt.values(result_bundle=result_bundle, heartbeat_at=now, updated_at=now))  # type: ignore
        session.commit()
    updated = result.rowcount == 1
    if updated:
        logger.debug(f"Updated partial result Bundle for batch job {batch_id}")
    return updated


def upsert_batch_job_documents(
    batch_id: str,
    documents: list[dict],
    *,
    worker_id: str | None = None,
    attempt_count: int | None = None,
) -> bool:
    """Merge fetched documents into a batch store while optionally fencing the worker attempt."""
    if (worker_id is None) != (attempt_count is None):
        raise ValueError("worker_id and attempt_count must be provided together")

    normalized: dict[str, tuple[str, str, str | None]] = {}
    for document in documents:
        doc_id = document.get("id")
        if not isinstance(doc_id, str) or not doc_id:
            continue
        doc_type = document.get("type", "Unknown")
        if not isinstance(doc_type, str) or not doc_type:
            doc_type = "Unknown"
        doc_date = document.get("date", "")
        if not isinstance(doc_date, str):
            doc_date = str(doc_date) if doc_date is not None else ""
        text = document.get("text")
        if text is not None and not isinstance(text, str):
            text = str(text)
        normalized[doc_id] = (doc_type, doc_date, text)

    now = datetime.now(UTC)
    with Session(database.db_engine) as session:
        if worker_id is not None and attempt_count is not None and _lock_owned_batch(session, batch_id, worker_id, attempt_count, now) is None:
            return False
        for doc_id, (doc_type, doc_date, text) in normalized.items():
            existing = session.get(BatchJobDocuments, (batch_id, doc_id))
            if existing is None:
                session.add(
                    BatchJobDocuments(
                        batch_id=batch_id,
                        doc_id=doc_id,
                        doc_type=doc_type,
                        doc_date=doc_date,
                        text=text,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.doc_type = doc_type
                existing.doc_date = doc_date
                existing.text = text
                existing.updated_at = now
        session.commit()
    return True


def get_batch_job_documents(batch_id: str) -> list[dict]:
    """Return the complete stored document set in deterministic date/id order."""
    statement = select(BatchJobDocuments).where(BatchJobDocuments.batch_id == batch_id).order_by(BatchJobDocuments.doc_date, BatchJobDocuments.doc_id)
    with Session(database.db_engine) as session:
        rows = session.execute(statement).scalars().all()
    return [{"id": row.doc_id, "type": row.doc_type, "date": row.doc_date, "text": row.text} for row in rows]


def get_batch_job_document_texts(batch_id: str) -> dict[str, str]:
    """Return stored non-null document text with one batch-scoped query."""
    statement = select(BatchJobDocuments.doc_id, BatchJobDocuments.text).where(BatchJobDocuments.batch_id == batch_id, BatchJobDocuments.text.is_not(None))
    with Session(database.db_engine) as session:
        rows = session.execute(statement).all()
    return {doc_id: text for doc_id, text in rows if isinstance(text, str)}


def delete_batch_job_record(batch_id: str) -> JSONResponse:
    existing = get_batch_job(batch_id)
    if not existing:
        return JSONResponse(make_operation_outcome("not-found", f"Batch Job ID {batch_id} was not found."), 404)
    with Session(database.db_engine) as session:
        session.execute(delete(Jobs).where(Jobs.batch_id == batch_id))
        session.execute(delete(QuestionnaireResponses).where(QuestionnaireResponses.batch_job_id == batch_id))
        session.execute(delete(BatchJobDocuments).where(BatchJobDocuments.batch_id == batch_id))
        session.execute(delete(BatchJobs).where(BatchJobs.batch_id == batch_id))
        session.commit()
    logger.info(f"Deleted batch job {batch_id}, its child jobs, documents, and linked questionnaire responses.")
    return JSONResponse(make_operation_outcome("deleted", f"Batch Job {batch_id} deleted successfully.", "information"))
