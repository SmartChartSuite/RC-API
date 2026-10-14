"""Tests pooled HTTP-client profiles, lifecycle, same-origin auth headers, and retry policy.

The module uses a patched sleep seam and fake request clients while mutating settings; router and service
request semantics belong in their respective tests."""

import httpx
import pytest
from pydantic import SecretStr

from src.util import http_clients
from src.util.http_clients import HTTP_CLIENT_PROFILES, ProfileName, fhir_headers, get_http_client, reset_http_clients, send_with_retry, shutdown_http_clients, startup_http_clients
from src.util.settings import settings


@pytest.mark.parametrize(
    ("name", "timeout", "connect", "retries"),
    [
        ("fhir", 60.0, 10.0, 0),
        ("attachments", 60.0, 10.0, 0),
        ("cql", 300.0, 10.0, 0),
        ("external_fhir", 30.0, 10.0, 0),
        ("external_patient", 60.0, 10.0, 0),
        ("jwks", 10.0, 5.0, 0),
        ("langfuse_health", 5.0, 5.0, 0),
    ],
)
def test_profiles_preserve_outbound_policy(name: ProfileName, timeout: float, connect: float, retries: int):
    profile = HTTP_CLIENT_PROFILES[name]

    assert profile.timeout.read == timeout
    assert profile.timeout.write == timeout
    assert profile.timeout.pool == timeout
    assert profile.timeout.connect == connect
    assert profile.retries == retries
    assert profile.follow_redirects is False
    assert profile.limits.max_connections == 100
    assert profile.limits.max_keepalive_connections == 20
    assert profile.limits.keepalive_expiry == 5.0


async def test_lifespan_registry_reuses_clients_and_shutdown_closes_them():
    clients = startup_http_clients()
    try:
        assert get_http_client("fhir") is clients["fhir"]
        assert get_http_client("fhir") is get_http_client("fhir")
        assert get_http_client("fhir").headers.get("Authorization") is None
        assert len({id(client._transport) for client in clients.values()}) == len(clients)
    finally:
        await shutdown_http_clients()

    assert all(client.is_closed for client in clients.values())


async def test_fallback_is_lazy_and_loop_local():
    first = get_http_client("external_fhir")
    second = get_http_client("external_fhir")

    assert first is second
    assert first.headers.get("Authorization") is None
    assert isinstance(first, httpx.AsyncClient)

    await reset_http_clients()
    assert first.is_closed


class _RequestSequenceClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _response(status_code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, headers=headers, request=httpx.Request("GET", "https://fhir.example/Observation"))


@pytest.fixture
def patched_sleep(monkeypatch):
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(http_clients, "_sleep", _sleep)
    return delays


def test_fhir_headers_require_same_origin_for_credentials(monkeypatch):
    monkeypatch.setattr(settings, "external_fhir_server_url", "https://fhir.example/fhir")
    monkeypatch.setattr(settings, "external_fhir_server_auth", SecretStr("Bearer token"))

    assert fhir_headers("https://fhir.example/fhir/Patient/1", with_auth=True) == {
        "Accept": "application/fhir+json",
        "Authorization": "Bearer token",
    }
    assert fhir_headers("https://other.example/Patient/1", with_auth=True) == {"Accept": "application/fhir+json"}


def test_fhir_headers_omit_unconfigured_secret_and_require_target(monkeypatch):
    monkeypatch.setattr(settings, "external_fhir_server_auth", None)
    assert fhir_headers("https://fhir.example/Patient/1", with_auth=True) == {"Accept": "application/fhir+json"}

    with pytest.raises(ValueError):
        fhir_headers(with_auth=True)


_EVALUATE_URL = "https://hapi.example/fhir/Library/Demographics/$evaluate"
_FHIR_URL = "https://fhir.example/Observation"


@pytest.mark.parametrize(
    ("method", "url", "profile", "outcomes", "expected_status", "expected_calls"),
    [
        pytest.param("GET", _FHIR_URL, "fhir", (503, 200), 200, 2, id="get-503-then-ok"),
        pytest.param("GET", _FHIR_URL, "fhir", ("connect", 200), 200, 2, id="get-connect-error-then-ok"),
        pytest.param("GET", _FHIR_URL, "fhir", (503, 503, 503), 503, 3, id="get-exhausts-attempts"),
        pytest.param("GET", _FHIR_URL, "fhir", (500, 200), 500, 1, id="get-500-not-transient"),
        pytest.param("GET", _FHIR_URL, "fhir", (404, 200), 404, 1, id="get-404-not-transient"),
        pytest.param("POST", "https://hapi.example/fhir/Patient/1", "fhir", (503, 200), 503, 1, id="proxy-post"),
        pytest.param("PUT", "https://hapi.example/fhir/Patient/1", "fhir", (503, 200), 503, 1, id="proxy-put"),
        pytest.param("DELETE", "https://hapi.example/fhir/Patient/1", "fhir", (503, 200), 503, 1, id="proxy-delete"),
        pytest.param("POST", _EVALUATE_URL, "cql", (503, 200), 200, 2, id="cql-evaluate-post"),
        pytest.param("POST", "https://hapi.example/fhir/Library", "cql", (503, 200), 503, 1, id="cql-non-evaluate-post"),
        pytest.param("GET", _FHIR_URL, "jwks", (503, 200), 503, 1, id="jwks-no-retry"),
        pytest.param("GET", _FHIR_URL, "langfuse_health", (503, 200), 503, 1, id="langfuse-health-no-retry"),
    ],
)
async def test_send_with_retry_policy(method, url, profile: ProfileName, outcomes, expected_status, expected_calls, patched_sleep):
    client = _RequestSequenceClient(*(httpx.ConnectError("connection failed") if outcome == "connect" else _response(outcome) for outcome in outcomes))

    response = await send_with_retry(client, method, url, profile=profile)

    assert response.status_code == expected_status
    assert len(client.calls) == expected_calls
    assert len(patched_sleep) == expected_calls - 1


@pytest.mark.parametrize("retry_after", ["30", "http-date"])
async def test_send_with_retry_honours_capped_retry_after(retry_after, patched_sleep):
    if retry_after == "http-date":
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        retry_after = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    client = _RequestSequenceClient(_response(429, {"Retry-After": retry_after}), _response(200))

    response = await send_with_retry(client, "GET", _FHIR_URL, profile="fhir")

    assert response.status_code == 200
    assert patched_sleep == [8.0]


async def test_send_with_retry_reraises_final_retryable_exception_and_redacts_query(monkeypatch, patched_sleep):
    messages: list[str] = []
    monkeypatch.setattr(http_clients.logger, "warning", lambda message: messages.append(message))
    error = httpx.ConnectError("connection failed")
    client = _RequestSequenceClient(error, error, error)

    with pytest.raises(httpx.ConnectError):
        await send_with_retry(client, "GET", "https://fhir.example/Observation?patient=secret", profile="fhir")

    assert len(client.calls) == 3
    assert len(patched_sleep) == 2
    assert messages and all("/Observation" in message and "patient=secret" not in message for message in messages)
