"""Track admin-created batch review copies.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.util.settings import settings

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else settings.db_schema


def upgrade() -> None:
    schema = _schema()
    op.add_column("batch_jobs_v1", sa.Column("source_batch_id", sa.String(length=64), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("reviewer_key", sa.String(length=64), nullable=True), schema=schema)
    op.add_column("batch_jobs_v1", sa.Column("reviewer_label", sa.String(length=128), nullable=True), schema=schema)
    op.create_index(
        "ux_batch_jobs_v1_review_copy",
        "batch_jobs_v1",
        ["source_batch_id", "reviewer_key"],
        unique=True,
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ux_batch_jobs_v1_review_copy", table_name="batch_jobs_v1", schema=schema)
    op.drop_column("batch_jobs_v1", "reviewer_label", schema=schema)
    op.drop_column("batch_jobs_v1", "reviewer_key", schema=schema)
    op.drop_column("batch_jobs_v1", "source_batch_id", schema=schema)
