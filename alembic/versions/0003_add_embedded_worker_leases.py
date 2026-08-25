"""Add durable embedded-worker lease and retry metadata.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.util.settings import db_schema

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else db_schema


def _deduplicate_logical_jobs() -> None:
    jobs = sa.table(
        "jobs_v1",
        sa.column("job_id", sa.String()),
        sa.column("batch_id", sa.String()),
        sa.column("task_type", sa.String()),
        sa.column("task_name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("result", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
        schema=_schema(),
    )
    connection = op.get_bind()
    duplicate_keys = connection.execute(sa.select(jobs.c.batch_id, jobs.c.task_type, jobs.c.task_name).group_by(jobs.c.batch_id, jobs.c.task_type, jobs.c.task_name).having(sa.func.count() > 1)).all()
    terminal_priority = {"complete": 3, "skipped": 2, "error": 1}
    for batch_id, task_type, task_name in duplicate_keys:
        rows = connection.execute(
            sa.select(jobs).where(
                jobs.c.batch_id == batch_id,
                jobs.c.task_type == task_type,
                jobs.c.task_name == task_name,
            )
        ).mappings()
        ranked = sorted(
            rows,
            key=lambda row: (
                terminal_priority.get(row["status"], 0),
                row["result"] is not None,
                str(row["completed_at"] or row["created_at"] or ""),
            ),
            reverse=True,
        )
        duplicate_ids = [row["job_id"] for row in ranked[1:]]
        if duplicate_ids:
            connection.execute(sa.delete(jobs).where(jobs.c.job_id.in_(duplicate_ids)))


def upgrade() -> None:
    schema = _schema()
    op.add_column("batch_jobs_v1", sa.Column("worker_id", sa.String(), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("last_error", sa.Text(), nullable=True), schema=schema)
    op.create_index(
        "ix_batch_jobs_v1_worker_queue",
        "batch_jobs_v1",
        ["status", "next_attempt_at", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_batch_jobs_v1_worker_lease",
        "batch_jobs_v1",
        ["status", "lease_expires_at"],
        schema=schema,
    )
    _deduplicate_logical_jobs()
    op.create_index(
        "ux_jobs_v1_logical_task",
        "jobs_v1",
        ["batch_id", "task_type", "task_name"],
        unique=True,
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ux_jobs_v1_logical_task", table_name="jobs_v1", schema=schema)
    op.drop_index("ix_batch_jobs_v1_worker_lease", table_name="batch_jobs_v1", schema=schema)
    op.drop_index("ix_batch_jobs_v1_worker_queue", table_name="batch_jobs_v1", schema=schema)
    op.drop_column("batch_jobs_v1", "last_error", schema=schema)
    op.drop_column("batch_jobs_v1", "max_attempts", schema=schema)
    op.drop_column("batch_jobs_v1", "next_attempt_at", schema=schema)
    op.drop_column("batch_jobs_v1", "lease_expires_at", schema=schema)
    op.drop_column("batch_jobs_v1", "worker_id", schema=schema)
