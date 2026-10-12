"""Persist the human-readable Questionnaire title on batch jobs.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.util.settings import db_schema

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else db_schema


def upgrade() -> None:
    op.add_column(
        "batch_jobs_v1",
        sa.Column("questionnaire_title", sa.String(), nullable=True),
        schema=_schema(),
    )


def downgrade() -> None:
    op.drop_column("batch_jobs_v1", "questionnaire_title", schema=_schema())
