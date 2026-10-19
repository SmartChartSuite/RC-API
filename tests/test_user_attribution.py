"""Test OAuth userinfo synchronization and users-table persistence."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.pool import StaticPool

from src.services.job_state import database, models
from src.services.job_state.users import get_user_by_sub, upsert_user
from src.util.current_user import CurrentUser, build_questionnaire_response_author, get_current_user
from src.util.settings import settings

ISSUER = "https://idp.example/"
USERINFO_URL = f"{ISSUER}userinfo"
BEARER_VALUE = "access-token"


@pytest.fixture
def users_db(monkeypatch):
    engine = sqlalchemy.create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database, "db_engine", engine)
    models.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def userinfo_settings(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_issuer", ISSUER)
    monkeypatch.setattr(settings, "oauth2_userinfo_staleness_seconds", 86_400.0)


def test_author_omits_identifier_system_when_issuer_is_unset(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_issuer", None)

    assert build_questionnaire_response_author(CurrentUser(sub="subject", name="Named User")) == {
        "display": "Named User",
        "identifier": {"value": "subject"},
    }


def _credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=BEARER_VALUE)


async def test_first_sight_syncs_user_and_returns_named_author(users_db, userinfo_settings, respx_mock):
    route = respx_mock.get(USERINFO_URL).mock(
        return_value=httpx.Response(
            200,
            json={"name": "Ada Lovelace", "email": "ada@example.org", "email_verified": True},
        )
    )

    user = await get_current_user({"sub": "auth0|first"}, _credentials())

    assert user == CurrentUser("auth0|first", "Ada Lovelace", "ada@example.org", True, user.profile_synced_at)
    assert user.display_name == "Ada Lovelace"
    assert build_questionnaire_response_author(user) == {
        "display": "Ada Lovelace",
        "identifier": {"system": ISSUER, "value": "auth0|first"},
    }
    assert route.call_count == 1
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {BEARER_VALUE}"
    stored = get_user_by_sub("auth0|first")
    assert stored is not None
    assert stored.name == "Ada Lovelace"
    assert stored.email == "ada@example.org"
    assert stored.email_verified is True
    assert stored.profile_synced_at is not None

    again = await get_current_user({"sub": "auth0|first"}, _credentials())

    assert again.display_name == "Ada Lovelace"
    assert route.call_count == 1, "a fresh profile must not trigger another userinfo call"


async def test_stale_user_refreshes_profile(users_db, userinfo_settings, respx_mock):
    old_sync = datetime.now(UTC) - timedelta(days=2)
    upsert_user(
        "stale",
        name="Old Name",
        email="old@example.org",
        email_verified=False,
        profile_synced_at=old_sync,
        seen_at=old_sync,
    )
    route = respx_mock.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={"name": "New Name", "email": "new@example.org", "email_verified": True}))

    user = await get_current_user({"sub": "stale"}, _credentials())

    assert user.display_name == "New Name"
    assert user.email == "new@example.org"
    assert user.email_verified is True
    assert route.call_count == 1
    stored = get_user_by_sub("stale")
    assert stored is not None
    assert stored.name == "New Name"
    assert stored.profile_synced_at is not None
    assert stored.profile_synced_at > old_sync.replace(tzinfo=None)
    assert stored.first_seen_at == old_sync.replace(tzinfo=None), "upsert must not reset first_seen_at"


async def test_first_sight_userinfo_failure_still_creates_sub_only_user(users_db, userinfo_settings, respx_mock):
    route = respx_mock.get(USERINFO_URL).mock(return_value=httpx.Response(503))

    user = await get_current_user({"sub": "unavailable"}, _credentials())

    assert user.sub == "unavailable"
    assert user.display_name == "unavailable"
    assert route.call_count == 1
    stored = get_user_by_sub("unavailable")
    assert stored is not None
    assert stored.name is None
    assert stored.email is None
    assert stored.email_verified is False
    assert stored.profile_synced_at is None


async def test_userinfo_failure_keeps_previously_synced_profile(users_db, userinfo_settings, respx_mock):
    old_sync = datetime.now(UTC) - timedelta(days=2)
    upsert_user(
        "outage",
        name="Existing Name",
        email="existing@example.org",
        email_verified=True,
        profile_synced_at=old_sync,
        seen_at=old_sync,
    )
    route = respx_mock.get(USERINFO_URL).mock(return_value=httpx.Response(500))

    user = await get_current_user({"sub": "outage"}, _credentials())

    assert user.display_name == "Existing Name"
    assert user.email == "existing@example.org"
    assert user.email_verified is True
    assert route.call_count == 1
    stored = get_user_by_sub("outage")
    assert stored is not None
    assert stored.name == "Existing Name"
    assert stored.profile_synced_at == old_sync.replace(tzinfo=None)
