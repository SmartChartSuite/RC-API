"""Persist logical child jobs and their optionally fenced execution results.

This module owns child-task creation/reuse, batch child listing, and result/status updates. It
collaborates with ``models`` for the child table, ``batch_queue`` for ownership locks and predicates,
and ``database`` for the engine. Batch records, lease transitions, questionnaire responses, HTTP
mapping, and execution orchestration do not belong here.
"""

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.services.job_state import database
from src.services.job_state.batch_queue import _lock_owned_batch, _ownership_exists
from src.services.job_state.models import Jobs, LogicalJob


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
        with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
        return list(session.execute(select(Jobs).where(Jobs.batch_id == batch_id)).scalars().all())


def get_job_progress_for_batches(batch_ids: list[str]) -> dict[str, int]:
    """Return successful-child percentages for the requested batches."""
    if not batch_ids:
        return {}

    with Session(database.db_engine) as session:
        rows = session.execute(select(Jobs.batch_id, Jobs.status).where(Jobs.batch_id.in_(batch_ids))).all()

    counts: dict[str, list[int]] = {batch_id: [0, 0] for batch_id in batch_ids}
    for batch_id, status in rows:
        counts[batch_id][1] += 1
        if status == "complete":
            counts[batch_id][0] += 1

    return {batch_id: round(successful / total * 100) if total else 0 for batch_id, (successful, total) in counts.items()}


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
    with Session(database.db_engine) as session:
        update_result: CursorResult = session.execute(stmt.values(**values))  # type: ignore
        session.commit()
    updated = update_result.rowcount == 1
    if updated:
        logger.debug(f"Updated job {job_id} → status={status}")
    return updated
