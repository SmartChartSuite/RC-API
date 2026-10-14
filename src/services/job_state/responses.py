"""Persist locally stored QuestionnaireResponse records.

This module owns QuestionnaireResponse create, read, list, update, and delete operations and their
transaction boundaries. It collaborates with ``models`` for the response table and ``database`` for
the engine, while batch-job linkage is handled by callers. Batch queue state, child jobs, HTTP routing,
and upstream FHIR transport do not belong here.
"""

from datetime import UTC, datetime

from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.services.errorhandler import make_operation_outcome
from src.services.job_state import database
from src.services.job_state.models import QuestionnaireResponses


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
        with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
        return session.get(QuestionnaireResponses, response_id)


def get_responses(batch_job_id: str | None = None, job_package: str | None = None) -> list[QuestionnaireResponses]:
    with Session(database.db_engine) as session:
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

    with Session(database.db_engine) as session:
        stmt = select(QuestionnaireResponses).where(QuestionnaireResponses.batch_job_id.in_(batch_ids)).order_by(QuestionnaireResponses.response_id)
        responses = session.execute(stmt).scalars().all()

    responses_by_batch: dict[str, QuestionnaireResponses] = {}
    for response in responses:
        responses_by_batch.setdefault(response.batch_job_id, response)
    return responses_by_batch


def update_response_body(response_id: str, response_body: dict, last_updated_by: str, updated_at: datetime | None = None) -> bool:
    timestamp = updated_at or datetime.now(UTC)
    with Session(database.db_engine) as session:
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
    with Session(database.db_engine) as session:
        session.execute(delete(QuestionnaireResponses).where(QuestionnaireResponses.response_id == response_id))
        session.commit()
    return JSONResponse(make_operation_outcome("deleted", f"Response {response_id} deleted successfully.", "information"))
