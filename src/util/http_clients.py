"""Long-lived outbound HTTP clients grouped by upstream policy.

The application lifespan owns the clients used in production. Direct service
callers (including tests) get a client lazily from a registry keyed by the
currently running event loop, which prevents an ``AsyncClient`` pool from
being reused across pytest or TestClient loops.
"""

import asyncio
import inspect
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

import httpx

ProfileName = Literal[
    "fhir",
    "attachments",
    "cql",
    "external_fhir",
    "external_patient",
    "jwks",
    "langfuse_health",
]


@dataclass(frozen=True)
class HTTPClientProfile:
    """Connection policy for one long-lived outbound client."""

    name: ProfileName
    timeout: httpx.Timeout
    retries: int
    follow_redirects: bool
    limits: httpx.Limits


def _limits() -> httpx.Limits:
    """Use httpx's documented default pool sizes explicitly."""
    return httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)


HTTP_CLIENT_PROFILES: dict[ProfileName, HTTPClientProfile] = {
    "fhir": HTTPClientProfile("fhir", httpx.Timeout(60.0), 3, False, _limits()),
    "attachments": HTTPClientProfile("attachments", httpx.Timeout(60.0), 3, False, _limits()),
    "cql": HTTPClientProfile("cql", httpx.Timeout(300.0), 5, False, _limits()),
    "external_fhir": HTTPClientProfile("external_fhir", httpx.Timeout(30.0), 0, False, _limits()),
    # Patient router reads and Group member reads had the same 60-second policy.
    "external_patient": HTTPClientProfile("external_patient", httpx.Timeout(60.0), 0, False, _limits()),
    "jwks": HTTPClientProfile("jwks", httpx.Timeout(10.0, connect=5.0), 0, False, _limits()),
    "langfuse_health": HTTPClientProfile("langfuse_health", httpx.Timeout(5.0), 0, False, _limits()),
}

# Short aliases make the profile table convenient for callers and tests while
# keeping one canonical mapping used by the registry.
PROFILES = HTTP_CLIENT_PROFILES

TransportFactory = Callable[[HTTPClientProfile], httpx.AsyncBaseTransport]
_transport_factories: dict[ProfileName, TransportFactory] = {}
_lifespan_clients: dict[ProfileName, httpx.AsyncClient] | None = None
_lazy_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[ProfileName, httpx.AsyncClient]] = weakref.WeakKeyDictionary()


def _profile_name(profile: str) -> ProfileName:
    if profile not in HTTP_CLIENT_PROFILES:
        raise ValueError(f"Unknown HTTP client profile: {profile}")
    return cast(ProfileName, profile)


def _new_transport(profile: HTTPClientProfile) -> httpx.AsyncBaseTransport:
    factory = _transport_factories.get(profile.name)
    if factory is not None:
        return factory(profile)
    return httpx.AsyncHTTPTransport(retries=profile.retries)


def _build_client(profile: HTTPClientProfile) -> httpx.AsyncClient:
    """Build one client and one transport; no credentials are installed here."""
    return httpx.AsyncClient(
        timeout=profile.timeout,
        limits=profile.limits,
        transport=_new_transport(profile),
        follow_redirects=profile.follow_redirects,
    )


def create_http_clients() -> dict[ProfileName, httpx.AsyncClient]:
    """Create one independent client for every configured profile."""
    return {name: _build_client(profile) for name, profile in HTTP_CLIENT_PROFILES.items()}


def startup_http_clients() -> dict[ProfileName, httpx.AsyncClient]:
    """Register and return the clients owned by the current application lifespan."""
    global _lifespan_clients
    if _lifespan_clients is not None:
        raise RuntimeError("HTTP clients are already registered")
    _lifespan_clients = create_http_clients()
    return _lifespan_clients


def get_http_client(profile: ProfileName) -> httpx.AsyncClient:
    """Return the lifespan client, or a loop-local lazy fallback before startup."""
    profile_name = _profile_name(profile)
    if _lifespan_clients is not None:
        return _lifespan_clients[profile_name]

    loop = asyncio.get_running_loop()
    clients_for_loop = _lazy_clients.setdefault(loop, {})
    client = clients_for_loop.get(profile_name)
    if client is None or getattr(client, "is_closed", False):
        client = _build_client(HTTP_CLIENT_PROFILES[profile_name])
        clients_for_loop[profile_name] = client
    return client


def set_transport_factory(profile: ProfileName, factory: TransportFactory) -> None:
    """Override transport creation for tests; production uses fresh HTTP transports."""
    _transport_factories[_profile_name(profile)] = factory


def clear_transport_factories() -> None:
    """Clear transport overrides installed by tests."""
    _transport_factories.clear()


async def _close_client(client: object) -> None:
    close = getattr(client, "aclose", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _close_clients(clients: list[httpx.AsyncClient]) -> None:
    seen: set[int] = set()
    for client in clients:
        if id(client) in seen:
            continue
        seen.add(id(client))
        await _close_client(client)


async def shutdown_http_clients() -> None:
    """Close lifespan and fallback clients and release all registry references."""
    global _lazy_clients, _lifespan_clients
    lifespan_clients = list(_lifespan_clients.values()) if _lifespan_clients is not None else []
    lazy_clients = [client for clients in _lazy_clients.values() for client in clients.values()]
    _lifespan_clients = None
    _lazy_clients = weakref.WeakKeyDictionary()
    await _close_clients([*lifespan_clients, *lazy_clients])


# This explicit name is useful to autouse test fixtures and documents that a
# reset closes clients rather than merely dropping references to open pools.
reset_http_clients = shutdown_http_clients
