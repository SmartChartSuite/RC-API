import httpx
import pytest

from src.util.http_clients import HTTP_CLIENT_PROFILES, ProfileName, get_http_client, reset_http_clients, shutdown_http_clients, startup_http_clients


@pytest.mark.parametrize(
    ("name", "timeout", "connect", "retries"),
    [
        ("fhir", 60.0, 60.0, 3),
        ("attachments", 60.0, 60.0, 3),
        ("cql", 300.0, 300.0, 5),
        ("external_fhir", 30.0, 30.0, 0),
        ("external_patient", 60.0, 60.0, 0),
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
