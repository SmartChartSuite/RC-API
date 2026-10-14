"""Define the SQLAlchemy models and detached snapshots used by the job-state package.

This module owns the declarative base, configured-schema helpers, ORM tables, and the small immutable
snapshots passed from persistence into workers and orchestration. ``database`` owns the engine and
migration checks; the queue and CRUD modules own transactions and state transitions. Query behavior,
lease fencing, HTTP responses, and upstream transport do not belong here.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import JSON, Column, ForeignKey, Index, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    heartbeat_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None] = mapped_column(default=_utc_now, onupdate=_utc_now)
    worker_id: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]
    max_attempts: Mapped[int] = mapped_column(server_default="3")
    last_error: Mapped[str | None] = mapped_column(Text)


class BatchJobDocuments(Base):
    __tablename__ = "batch_job_documents"

    batch_id: Mapped[str] = mapped_column(
        ForeignKey("batch_jobs_v1.batch_id", name="fk_batch_job_documents_batch_id", ondelete="CASCADE"),
        primary_key=True,
    )
    doc_id: Mapped[str] = mapped_column(primary_key=True)
    doc_type: Mapped[str]
    doc_date: Mapped[str]
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=_utc_now, onupdate=_utc_now)


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
