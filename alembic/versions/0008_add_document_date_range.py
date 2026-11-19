"""Persist the DocumentReference date range requested for a batch job.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.util.settings import settings

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else settings.db_schema


def upgrade() -> None:
    schema = _schema()
    op.add_column("batch_jobs_v1", sa.Column("document_start_date", sa.Date(), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("document_end_date", sa.Date(), nullable=True), schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_column("batch_jobs_v1", "document_end_date", schema=schema)
    op.drop_column("batch_jobs_v1", "document_start_date", schema=schema)
