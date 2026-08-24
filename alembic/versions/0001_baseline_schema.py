"""Create the baseline RC-API schema.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.schema import CreateSchema

from src.util.settings import db_schema

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else db_schema


def upgrade() -> None:
    schema = _schema()
    if schema:
        op.get_bind().execute(CreateSchema(schema, if_not_exists=True))

    op.create_table(
        "batch_jobs_v1",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("patient_id", sa.String(), nullable=False),
        sa.Column("job_package", sa.String(), nullable=False),
        sa.Column("started_by", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result_bundle", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("batch_id"),
        schema=schema,
    )
    op.create_table(
        "questionnaire_responses",
        sa.Column("response_id", sa.String(), nullable=False),
        sa.Column("batch_job_id", sa.String(), nullable=False),
        sa.Column("job_package", sa.String(), nullable=False),
        sa.Column("patient_id", sa.String(), nullable=False),
        sa.Column("last_updated_by", sa.String(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("response_id"),
        schema=schema,
    )
    batch_reference = f"{schema}.batch_jobs_v1.batch_id" if schema else "batch_jobs_v1.batch_id"
    op.create_table(
        "jobs_v1",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("patient_id", sa.String(), nullable=False),
        sa.Column("job_package", sa.String(), nullable=False),
        sa.Column("task_name", sa.String(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], [batch_reference]),
        sa.PrimaryKeyConstraint("job_id"),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("jobs_v1", schema=schema)
    op.drop_table("questionnaire_responses", schema=schema)
    op.drop_table("batch_jobs_v1", schema=schema)
