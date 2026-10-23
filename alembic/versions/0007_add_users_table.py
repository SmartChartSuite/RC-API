"""Store authenticated user profiles used for QuestionnaireResponse attribution.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.util.settings import settings

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str | None:
    return None if op.get_bind().dialect.name == "sqlite" else settings.db_schema


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("sub", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sub"),
        schema=_schema(),
    )


def downgrade() -> None:
    op.drop_table("users", schema=_schema())
