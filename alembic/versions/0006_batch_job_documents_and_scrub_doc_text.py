"""Persist fetched batch documents and remove legacy LLM document text.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.util.settings import settings

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 500
_FK_NAME = "fk_batch_job_documents_batch_id"


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else settings.db_schema


def _scrub_result(result: object) -> tuple[object, bool]:
    if not isinstance(result, dict):
        return result, False
    raw_documents = result.get("document_results")
    if not isinstance(raw_documents, list):
        return result, False

    scrubbed_documents: list[object] = []
    changed = False
    for raw_document in raw_documents:
        if isinstance(raw_document, dict) and "doc_text" in raw_document:
            scrubbed_document = dict(raw_document)
            del scrubbed_document["doc_text"]
            scrubbed_documents.append(scrubbed_document)
            changed = True
        else:
            scrubbed_documents.append(raw_document)

    if not changed:
        return result, False
    scrubbed_result = dict(result)
    scrubbed_result["document_results"] = scrubbed_documents
    return scrubbed_result, True


def _scrub_legacy_document_text() -> None:
    """Remove doc_text from legacy child-job LLM payloads in bounded batches."""
    jobs = sa.table(
        "jobs_v1",
        sa.column("job_id", sa.String()),
        sa.column("result", sa.JSON()),
        schema=_schema(),
    )
    connection = op.get_bind()
    last_job_id: str | None = None

    while True:
        statement = sa.select(jobs.c.job_id, jobs.c.result).where(sa.cast(jobs.c.result, sa.Text).like("%doc_text%"))
        if last_job_id is not None:
            statement = statement.where(jobs.c.job_id > last_job_id)
        rows = connection.execute(statement.order_by(jobs.c.job_id).limit(_BATCH_SIZE)).mappings().all()
        if not rows:
            return

        for row in rows:
            scrubbed_result, changed = _scrub_result(row["result"])
            if changed:
                connection.execute(jobs.update().where(jobs.c.job_id == row["job_id"]).values(result=scrubbed_result))
        last_job_id = str(rows[-1]["job_id"])


def upgrade() -> None:
    schema = _schema()
    batch_reference = f"{schema}.batch_jobs_v1.batch_id" if schema else "batch_jobs_v1.batch_id"
    op.create_table(
        "batch_job_documents",
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("doc_id", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("doc_date", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], [batch_reference], name=_FK_NAME, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("batch_id", "doc_id"),
        schema=schema,
    )
    _scrub_legacy_document_text()


def downgrade() -> None:
    # The doc_text scrub is intentionally irreversible; a downgrade cannot restore removed payload data.
    op.drop_table("batch_job_documents", schema=_schema())
