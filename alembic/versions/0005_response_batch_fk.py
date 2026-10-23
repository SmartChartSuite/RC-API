"""Link QuestionnaireResponses to their batch job with a foreign key and index.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.util.settings import settings

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_questionnaire_responses_batch_job_id"
_INDEX_NAME = "ix_questionnaire_responses_batch_job_id"


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else settings.db_schema


def upgrade() -> None:
    schema = _schema()
    responses = sa.table("questionnaire_responses", sa.column("batch_job_id"), schema=schema)
    batches = sa.table("batch_jobs_v1", sa.column("batch_id"), schema=schema)
    # Responses pointing at a non-existent batch would violate the new constraint.
    op.execute(responses.delete().where(~responses.c.batch_job_id.in_(sa.select(batches.c.batch_id))))

    with op.batch_alter_table("questionnaire_responses", schema=schema) as batch_op:
        batch_op.create_index(_INDEX_NAME, ["batch_job_id"])
        batch_op.create_foreign_key(_FK_NAME, "batch_jobs_v1", ["batch_job_id"], ["batch_id"], referent_schema=schema)


def downgrade() -> None:
    with op.batch_alter_table("questionnaire_responses", schema=_schema()) as batch_op:
        batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
        batch_op.drop_index(_INDEX_NAME)
