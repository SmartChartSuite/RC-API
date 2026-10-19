"""Resolve the authenticated user and build trusted QuestionnaireResponse authors.

Authentication remains owned by ``src.util.auth``. This module adds the raw
Bearer token needed for the OAuth userinfo request, caches profile fields in the
job-state users table, and deliberately treats profile lookup as best effort so
an authenticated write is never blocked by an identity-provider outage.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import Security
from fastapi.security import HTTPAuthorizationCredentials
from loguru import logger

from src.models.fhir import Identifier, Reference
from src.services.job_state.users import get_user_by_sub, touch_last_seen_at, upsert_user
from src.util.auth import bearer_scheme, validate_token
from src.util.http_clients import get_http_client
from src.util.settings import settings
from src.util.threading import run_db


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The small trusted identity snapshot used by write paths."""

    sub: str
    name: str | None = None
    email: str | None = None
    email_verified: bool = False
    profile_synced_at: datetime | None = None

    @property
    def display_name(self) -> str:
        return self.name.strip() if isinstance(self.name, str) and self.name.strip() else self.sub


@dataclass(frozen=True, slots=True)
class UserinfoProfile:
    """Profile fields accepted from the OAuth provider's userinfo response."""

    name: str | None
    email: str | None
    email_verified: bool


def _profile_value(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"userinfo field {field_name!r} was not a string")
    return value


async def fetch_userinfo(token: str | None) -> UserinfoProfile | None:
    """Fetch and validate the provider profile, returning ``None`` on any failure."""
    issuer = settings.oauth2_issuer
    if not token or not issuer:
        logger.warning("Skipping OAuth userinfo lookup because the bearer token or issuer is unavailable.")
        return None

    url = f"{issuer.rstrip('/')}/userinfo"
    try:
        response = await get_http_client("userinfo").get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("userinfo response was not a JSON object")
        email_verified = payload.get("email_verified", False)
        if not isinstance(email_verified, bool):
            raise TypeError("userinfo field 'email_verified' was not a boolean")
        return UserinfoProfile(
            name=_profile_value(payload, "name"),
            email=_profile_value(payload, "email"),
            email_verified=email_verified,
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning(f"OAuth userinfo lookup failed at {url}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - profile lookup must never block an authenticated write
        logger.warning(f"OAuth userinfo lookup failed at {url}: {exc}")
        return None


def _profile_is_stale(profile_synced_at: datetime | None, now: datetime) -> bool:
    if profile_synced_at is None:
        return True
    if profile_synced_at.tzinfo is None:
        profile_synced_at = profile_synced_at.replace(tzinfo=UTC)
    else:
        profile_synced_at = profile_synced_at.astimezone(UTC)
    return now - profile_synced_at >= timedelta(seconds=settings.oauth2_userinfo_staleness_seconds)


def _from_record(record: Any, sub: str) -> CurrentUser:
    if record is None:
        return CurrentUser(sub=sub)
    return CurrentUser(
        sub=sub,
        name=record.name,
        email=record.email,
        email_verified=record.email_verified,
        profile_synced_at=record.profile_synced_at,
    )


def user_from_claims(claims: dict) -> CurrentUser:
    """Build the compatibility snapshot used by direct service/route callers."""
    sub = claims.get("sub")
    return CurrentUser(sub=sub if isinstance(sub, str) and sub else "unknown")


async def get_current_user(
    claims: dict = Security(validate_token),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> CurrentUser:
    """Resolve, touch, and best-effort synchronize the current authenticated user."""
    user = user_from_claims(claims)
    if user.sub == "unknown":
        return user

    existing = await run_db(get_user_by_sub, user.sub)
    now = datetime.now(UTC)
    if existing is not None and not _profile_is_stale(existing.profile_synced_at, now):
        await run_db(touch_last_seen_at, user.sub, now)
        return _from_record(existing, user.sub)

    token = credentials.credentials if credentials is not None else None
    try:
        profile = await fetch_userinfo(token)
    except Exception as exc:  # noqa: BLE001 - profile lookup must never block an authenticated write
        logger.warning(f"OAuth userinfo lookup failed for sub {user.sub}: {exc}")
        profile = None

    if profile is None:
        persisted = await run_db(upsert_user, user.sub, seen_at=now)
    else:
        synced_at = datetime.now(UTC)
        persisted = await run_db(
            upsert_user,
            user.sub,
            name=profile.name,
            email=profile.email,
            email_verified=profile.email_verified,
            profile_synced_at=synced_at,
            seen_at=synced_at,
        )
    return _from_record(persisted, user.sub)


def build_questionnaire_response_author(user: CurrentUser) -> dict[str, Any]:
    """Return the server-derived FHIR ``QuestionnaireResponse.author`` value."""
    identifier_values: dict[str, str] = {}
    if settings.oauth2_issuer:
        identifier_values["system"] = settings.oauth2_issuer
    identifier_values["value"] = user.sub
    serialized = Reference(identifier=Identifier(**identifier_values), display=user.display_name).to_fhir_json()
    return {"display": serialized["display"], "identifier": serialized["identifier"]}
