"""Persist authenticated user identities and cached profile details.

This module owns the users table's small upsert/read/touch operations. Userinfo
transport and FastAPI dependency behavior live in ``src.util.current_user``;
QuestionnaireResponse author mapping belongs to the callers that write resources.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CursorResult, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.services.job_state import database
from src.services.job_state.models import Users

_UNSET = object()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def upsert_user(
    sub: str,
    *,
    name: str | None | object = _UNSET,
    email: str | None | object = _UNSET,
    email_verified: bool | object = _UNSET,
    profile_synced_at: datetime | None | object = _UNSET,
    seen_at: datetime | None = None,
) -> Users:
    """Insert or update one user without racing on the first request.

    Omitted profile arguments leave an existing profile untouched. Passing a
    profile argument, including ``None``, deliberately updates that field; this
    lets a successful userinfo response represent a missing name or email while
    a failed response preserves previously fetched data.
    """
    if not sub:
        raise ValueError("User sub must be non-empty")

    timestamp = seen_at or _utc_now()
    values: dict[str, Any] = {
        "sub": sub,
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
        "email_verified": False,
    }
    profile_values: dict[str, Any] = {}
    for field_name, value in (
        ("name", name),
        ("email", email),
        ("email_verified", email_verified),
        ("profile_synced_at", profile_synced_at),
    ):
        if value is not _UNSET:
            values[field_name] = value
            profile_values[field_name] = value

    with Session(database.db_engine, expire_on_commit=False) as session:
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "sqlite":
            statement = sqlite_insert(Users).values(**values)
        elif dialect_name == "postgresql":
            statement = postgresql_insert(Users).values(**values)
        else:
            raise RuntimeError(f"Unsupported database dialect for user upsert: {dialect_name}")

        update_values: dict[str, Any] = {"last_seen_at": timestamp}
        for field_name in profile_values:
            update_values[field_name] = getattr(statement.excluded, field_name)
        session.execute(statement.on_conflict_do_update(index_elements=[Users.sub], set_=update_values))
        session.commit()
        user = session.get(Users, sub)
        if user is None:  # pragma: no cover - the upsert and read share a transaction
            raise RuntimeError(f"User {sub!r} was not persisted")
        return user


def get_user_by_sub(sub: str) -> Users | None:
    """Return a user by stable OAuth subject, if one has been seen."""
    with Session(database.db_engine) as session:
        return session.get(Users, sub)


def touch_last_seen_at(sub: str, seen_at: datetime | None = None) -> bool:
    """Update the last-seen timestamp for an existing user."""
    timestamp = seen_at or _utc_now()
    with Session(database.db_engine) as session:
        result: CursorResult = session.execute(update(Users).where(Users.sub == sub).values(last_seen_at=timestamp))  # type: ignore
        session.commit()
    return result.rowcount == 1


# Descriptive alias for callers that prefer a verb-object name.
touch_user_last_seen = touch_last_seen_at
