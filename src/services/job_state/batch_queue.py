"""Implement the durable batch queue, lease, retry, recovery, and fencing state machine.

This module owns runnable predicates, PostgreSQL ``FOR UPDATE SKIP LOCKED`` claiming, the SQLite CAS
fallback, lease renewal/release, retry and failure transitions, expired-lease recovery, and shared
ownership helpers. It collaborates with ``models`` for tables and ``database.db_engine`` for the
engine. Batch CRUD, child-task CRUD, response CRUD, and HTTP response shaping do not belong here.
"""

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import ColumnElement, CursorResult, exists, or_, select, update
from sqlalchemy.orm import Session

from src.services.errorhandler import make_operation_outcome
from src.services.job_state import database
from src.services.job_state.models import BatchJobs, ClaimedBatchJob, Jobs
from src.util.settings import settings


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


def _ownership_exists(batch_id: str, worker_id: str, attempt_count: int, now: datetime):
    return exists(select(BatchJobs.batch_id).where(*_owned_by(batch_id, worker_id, attempt_count, now)))


_CLAIM_CANDIDATE_LIMIT = 100


def _is_postgresql(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _runnable_batch_job_predicate(now: datetime, excluded_ids: set[str] | None = None) -> tuple[ColumnElement[bool], ...]:
    predicates: list[ColumnElement[bool]] = [
        BatchJobs.status == "pending",
        BatchJobs.attempt_count < BatchJobs.max_attempts,
        or_(BatchJobs.next_attempt_at.is_(None), BatchJobs.next_attempt_at <= now),
    ]
    if excluded_ids:
        predicates.append(BatchJobs.batch_id.not_in(excluded_ids))
    return tuple(predicates)


def _runnable_batch_job_statement(now: datetime, excluded_ids: set[str] | None = None):
    return select(BatchJobs).where(*_runnable_batch_job_predicate(now, excluded_ids)).order_by(BatchJobs.created_at, BatchJobs.batch_id).limit(1)


def _mark_batch_job_running(job: BatchJobs, worker_id: str, now: datetime, lease_expires_at: datetime) -> ClaimedBatchJob:
    job.status = "running"
    job.worker_id = worker_id
    job.attempt_count += 1
    job.heartbeat_at = now
    job.lease_expires_at = lease_expires_at
    job.next_attempt_at = None
    job.updated_at = now
    return _claimed_batch_snapshot(job, worker_id)


def _claim_postgresql(session: Session, worker_id: str, lease_seconds: int, now: datetime) -> ClaimedBatchJob | None:
    candidate = session.scalar(_runnable_batch_job_statement(now).with_for_update(skip_locked=True))
    if candidate is None:
        return None
    return _mark_batch_job_running(candidate, worker_id, now, now + timedelta(seconds=lease_seconds))


def _claim_with_cas(
    session: Session,
    worker_id: str,
    lease_seconds: int,
    now: datetime,
    tried_ids: set[str],
) -> tuple[ClaimedBatchJob | None, str | None]:
    candidate_id = session.scalar(select(BatchJobs.batch_id).where(*_runnable_batch_job_predicate(now, tried_ids)).order_by(BatchJobs.created_at, BatchJobs.batch_id).limit(1))
    if candidate_id is None:
        return None, None

    lease_expires_at = now + timedelta(seconds=lease_seconds)
    result: CursorResult = session.execute(
        update(BatchJobs)
        .where(
            BatchJobs.batch_id == candidate_id,
            *_runnable_batch_job_predicate(now),
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
        return None, candidate_id

    claimed = session.get(BatchJobs, candidate_id)
    if claimed is None:
        session.rollback()
        return None, candidate_id
    return _claimed_batch_snapshot(claimed, worker_id), None


def claim_next_batch_job(worker_id: str, lease_seconds: int) -> ClaimedBatchJob | None:
    """Atomically claim the oldest runnable pending batch for this worker."""
    with Session(database.db_engine) as session:
        now = datetime.now(UTC)
        if _is_postgresql(session):
            snapshot = _claim_postgresql(session, worker_id, lease_seconds, now)
            if snapshot is None:
                return None
            session.commit()
            logger.info(f"Worker {worker_id} claimed batch {snapshot.batch_id} attempt {snapshot.attempt_count}")
            return snapshot

    # SQLite serializes writers, so a conditional update remains the portable fallback.
    # A lost compare-and-swap moves to the next candidate instead of retrying the same row.
    tried_ids: set[str] = set()
    for _ in range(_CLAIM_CANDIDATE_LIMIT):
        now = datetime.now(UTC)
        with Session(database.db_engine) as session:
            snapshot, lost_candidate_id = _claim_with_cas(session, worker_id, lease_seconds, now, tried_ids)
            if snapshot is not None:
                session.commit()
                logger.info(f"Worker {worker_id} claimed batch {snapshot.batch_id} attempt {snapshot.attempt_count}")
                return snapshot
            if lost_candidate_id is None:
                session.rollback()
                return None
        tried_ids.add(lost_candidate_id)
    return None


def renew_batch_job_lease(batch_id: str, worker_id: str, attempt_count: int, lease_seconds: int) -> bool:
    """Extend a lease only while its worker and attempt still own the batch and the lease is unexpired."""
    now = datetime.now(UTC)
    with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
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


def _expired_batch_job_predicate(now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        BatchJobs.status == "running",
        or_(BatchJobs.lease_expires_at.is_(None), BatchJobs.lease_expires_at <= now),
    )


def _expired_batch_job_statement(now: datetime, chunk_size: int, *, skip_locked: bool):
    statement = (
        select(
            BatchJobs.batch_id,
            BatchJobs.attempt_count,
            BatchJobs.max_attempts,
            BatchJobs.result_bundle,
        )
        .where(*_expired_batch_job_predicate(now))
        .order_by(BatchJobs.created_at, BatchJobs.batch_id)
        .limit(chunk_size)
    )
    return statement.with_for_update(skip_locked=True) if skip_locked else statement


def _batch_ids_with_status(session: Session, batch_ids: list[str], status: str) -> set[str]:
    if not batch_ids:
        return set()
    return set(session.scalars(select(BatchJobs.batch_id).where(BatchJobs.batch_id.in_(batch_ids), BatchJobs.status == status)).all())


def _recover_batch_group(session: Session, batch_ids: list[str], now: datetime, values: dict) -> set[str]:
    if not batch_ids:
        return set()
    result: CursorResult = session.execute(update(BatchJobs).where(BatchJobs.batch_id.in_(batch_ids), *_expired_batch_job_predicate(now)).values(**values))  # type: ignore
    rowcount = result.rowcount
    if rowcount == 0:
        return set()
    if rowcount == len(batch_ids):
        return set(batch_ids)
    return _batch_ids_with_status(session, batch_ids, str(values["status"]))


def recover_expired_batch_job_leases(retry_delay_seconds: int) -> tuple[list[str], list[str]]:
    """Requeue expired attempts, terminally failing batches with no attempts left."""
    message = "Batch worker lease expired before completion."
    retried: list[str] = []
    failed: list[str] = []
    chunk_size = settings.batch_job_recovery_chunk_size

    while True:
        now = datetime.now(UTC)
        with Session(database.db_engine) as session:
            rows = session.execute(
                _expired_batch_job_statement(
                    now,
                    chunk_size,
                    skip_locked=_is_postgresql(session),
                )
            ).all()
            if not rows:
                break

            batch_ids = [batch_id for batch_id, _, _, _ in rows]
            retry_ids: list[str] = []
            terminal_rows: list[tuple[str, object]] = []
            for batch_id, attempt_count, max_attempts, result_bundle in rows:
                if attempt_count < max_attempts:
                    retry_ids.append(batch_id)
                else:
                    terminal_rows.append((batch_id, result_bundle))

            terminal_ids = [batch_id for batch_id, _ in terminal_rows]
            completed_ids = set(session.scalars(select(Jobs.batch_id).where(Jobs.batch_id.in_(terminal_ids), Jobs.status == "complete").group_by(Jobs.batch_id)).all())
            partial_ids: list[str] = []
            error_with_result_ids: list[str] = []
            error_without_result_ids: list[str] = []
            for batch_id, result_bundle in terminal_rows:
                has_completed_result = batch_id in completed_ids and isinstance(result_bundle, dict) and result_bundle.get("resourceType") == "Bundle"
                if has_completed_result:
                    partial_ids.append(batch_id)
                elif result_bundle is None:
                    error_without_result_ids.append(batch_id)
                else:
                    error_with_result_ids.append(batch_id)

            retry_values = {
                "status": "pending",
                "worker_id": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "last_error": message,
                "updated_at": now,
                "next_attempt_at": now + timedelta(seconds=retry_delay_seconds),
                "completed_at": None,
            }
            terminal_values = {
                "worker_id": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "last_error": message,
                "updated_at": now,
                "next_attempt_at": None,
                "completed_at": now,
            }
            retry_recovered = _recover_batch_group(session, retry_ids, now, retry_values)
            partial_recovered = _recover_batch_group(session, partial_ids, now, {**terminal_values, "status": "partial"})
            error_with_result_recovered = _recover_batch_group(session, error_with_result_ids, now, {**terminal_values, "status": "error"})
            error_without_result_recovered = _recover_batch_group(
                session,
                error_without_result_ids,
                now,
                {
                    **terminal_values,
                    "status": "error",
                    "result_bundle": make_operation_outcome("exception", message),
                },
            )
            terminal_recovered = partial_recovered | error_with_result_recovered | error_without_result_recovered
            recovered_count = len(retry_recovered | terminal_recovered)
            if recovered_count == 0:
                session.rollback()
                logger.error(f"Stopped expired batch recovery after a chunk made no progress: {batch_ids}")
                break

            successful_retry_ids = [batch_id for batch_id in retry_ids if batch_id in retry_recovered]
            successful_terminal_ids = [batch_id for batch_id in terminal_ids if batch_id in terminal_recovered]
            if successful_retry_ids:
                session.execute(
                    update(Jobs)
                    .where(
                        Jobs.batch_id.in_(successful_retry_ids),
                        Jobs.status.not_in(("complete", "skipped")),
                    )
                    .values(status="pending", result=None, completed_at=None)
                )
            if successful_terminal_ids:
                session.execute(
                    update(Jobs)
                    .where(
                        Jobs.batch_id.in_(successful_terminal_ids),
                        Jobs.status.not_in(("complete", "error", "skipped")),
                    )
                    .values(status="error", result={"message": message}, completed_at=now)
                )

            retried.extend(batch_id for batch_id in retry_ids if batch_id in retry_recovered)
            failed.extend(batch_id for batch_id in terminal_ids if batch_id in terminal_recovered)
            session.commit()

    if retried or failed:
        logger.warning(f"Recovered expired batch leases: retry={retried}, error={failed}")
    return retried, failed
