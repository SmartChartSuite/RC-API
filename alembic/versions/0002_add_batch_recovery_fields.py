"""Add persisted execution inputs and recovery metadata.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.util.settings import db_schema

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else db_schema


def upgrade() -> None:
    schema = _schema()
    op.add_column("batch_jobs_v1", sa.Column("questionnaire_id", sa.String(), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("job_package_version", sa.String(), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("requested_jobs", sa.JSON(), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True), schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_column("batch_jobs_v1", "updated_at", schema=schema)
    op.drop_column("batch_jobs_v1", "heartbeat_at", schema=schema)
    op.drop_column("batch_jobs_v1", "attempt_count", schema=schema)
    op.drop_column("batch_jobs_v1", "requested_jobs", schema=schema)
    op.drop_column("batch_jobs_v1", "job_package_version", schema=schema)
    op.drop_column("batch_jobs_v1", "questionnaire_id", schema=schema)
