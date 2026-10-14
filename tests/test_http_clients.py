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


async def test_send_with_retry_get_retries_503_then_succeeds(patched_sleep):
    client = _RequestSequenceClient(_response(503), _response(200))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation?patient=secret", profile="fhir")

    assert response.status_code == 200
    assert len(client.calls) == 2
    assert len(patched_sleep) == 1


async def test_send_with_retry_honours_capped_retry_after_seconds(patched_sleep):
    client = _RequestSequenceClient(_response(429, {"Retry-After": "30"}), _response(200))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation", profile="fhir")

    assert response.status_code == 200
    assert patched_sleep == [8.0]


@pytest.mark.parametrize("status_code", [500, 404])
async def test_send_with_retry_does_not_retry_non_transient_status(status_code, patched_sleep):
    client = _RequestSequenceClient(_response(status_code), _response(200))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation", profile="fhir")

    assert response.status_code == status_code
    assert len(client.calls) == 1
    assert patched_sleep == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_send_with_retry_does_not_retry_non_idempotent_fhir_proxy_methods(method, patched_sleep):
    client = _RequestSequenceClient(_response(503), _response(200))

    response = await send_with_retry(client, method, "https://hapi.example/fhir/Patient/1", profile="fhir")

    assert response.status_code == 503
    assert len(client.calls) == 1
    assert patched_sleep == []


async def test_send_with_retry_retries_connect_error_then_succeeds(patched_sleep):
    client = _RequestSequenceClient(httpx.ConnectError("connection failed"), _response(200))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation", profile="fhir")

    assert response.status_code == 200
    assert len(client.calls) == 2
    assert len(patched_sleep) == 1


async def test_send_with_retry_returns_final_response_after_max_attempts(patched_sleep):
    client = _RequestSequenceClient(_response(503), _response(503), _response(503))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation", profile="fhir")

    assert response.status_code == 503
    assert len(client.calls) == 3
    assert len(patched_sleep) == 2


async def test_send_with_retry_reraises_final_retryable_exception(patched_sleep):
    error = httpx.ConnectError("connection failed")
    client = _RequestSequenceClient(error, error, error)

    with pytest.raises(httpx.ConnectError):
        await send_with_retry(client, "GET", "https://fhir.example/Observation", profile="fhir")

    assert len(client.calls) == 3
    assert len(patched_sleep) == 2


async def test_retry_warning_omits_query_string(monkeypatch, patched_sleep):
    messages: list[str] = []
    monkeypatch.setattr(http_clients.logger, "warning", lambda message: messages.append(message))
    client = _RequestSequenceClient(_response(503), _response(200))

    await send_with_retry(client, "GET", "https://fhir.example/Observation?patient=secret", profile="fhir")

    assert messages
    assert "/Observation" in messages[0]
    assert "patient=secret" not in messages[0]


async def test_send_with_retry_allows_read_only_cql_evaluate_post(patched_sleep):
    client = _RequestSequenceClient(_response(503), _response(200))

    response = await send_with_retry(client, "POST", "https://hapi.example/fhir/Library/Demographics/$evaluate", profile="cql")

    assert response.status_code == 200
    assert len(client.calls) == 2
    assert len(patched_sleep) == 1


async def test_send_with_retry_rejects_non_evaluate_cql_post(patched_sleep):
    client = _RequestSequenceClient(_response(503), _response(200))

    response = await send_with_retry(client, "POST", "https://hapi.example/fhir/Library", profile="cql")

    assert response.status_code == 503
    assert len(client.calls) == 1
    assert patched_sleep == []


async def test_send_with_retry_honours_capped_retry_after_http_date(monkeypatch, patched_sleep):
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    retry_at = datetime.now(UTC) + timedelta(seconds=30)
    client = _RequestSequenceClient(_response(503, {"Retry-After": format_datetime(retry_at, usegmt=True)}), _response(200))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation", profile="fhir")

    assert response.status_code == 200
    assert patched_sleep == [8.0]


@pytest.mark.parametrize("profile", ["jwks", "langfuse_health"])
async def test_send_with_retry_disables_profiles_without_retry(profile: ProfileName, patched_sleep):
    client = _RequestSequenceClient(_response(503), _response(200))

    response = await send_with_retry(client, "GET", "https://fhir.example/Observation", profile=profile)

    assert response.status_code == 503
    assert len(client.calls) == 1
    assert patched_sleep == []
