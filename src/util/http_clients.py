"""Manage pooled outbound HTTP clients grouped by upstream policy.

This module defines named timeout, connection, authentication-header, retry, and
cleanup behavior; production clients are owned by the application lifespan, while
direct callers can use loop-local lazy clients before startup or in tests. It does
not implement domain workflows or persistence; callers supply requests, and URL
safety belongs to `src.util.outbound_url`.
"""

import asyncio
import inspect
import random
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx
from loguru import logger

from src.util.outbound_url import is_same_origin
from src.util.settings import secret_value, settings

ProfileName = Literal[
    "fhir",
    "attachments",
    "cql",
    "external_fhir",
    "external_patient",
    "jwks",
    "userinfo",
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


@dataclass(frozen=True)
class RetryPolicy:
    """Application-level retry policy for one outbound client profile."""

    attempts: int
    methods: frozenset[str]
    url_suffix: str | None = None
    base_delay: float = 0.5
    max_delay: float = 8.0

    def allows(self, method: str, url: str) -> bool:
        """Return whether this policy may retry the request."""
        if method not in self.methods:
            return False
        if self.url_suffix is None:
            return True
        return urlparse(url).path.rstrip("/").endswith(self.url_suffix)


def _limits() -> httpx.Limits:
    """Use httpx's documented default pool sizes explicitly."""
    return httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)


HTTP_CLIENT_PROFILES: dict[ProfileName, HTTPClientProfile] = {
    # Transport retries are disabled because send_with_retry owns the complete
    # retry budget, including connection failures, without multiplying attempts.
    "fhir": HTTPClientProfile("fhir", httpx.Timeout(60.0, connect=10.0), 0, False, _limits()),
    "attachments": HTTPClientProfile("attachments", httpx.Timeout(60.0, connect=10.0), 0, False, _limits()),
    "cql": HTTPClientProfile("cql", httpx.Timeout(300.0, connect=10.0), 0, False, _limits()),
    "external_fhir": HTTPClientProfile("external_fhir", httpx.Timeout(30.0, connect=10.0), 0, False, _limits()),
    # Patient router reads and Group member reads had the same 60-second policy.
    "external_patient": HTTPClientProfile("external_patient", httpx.Timeout(60.0, connect=10.0), 0, False, _limits()),
    "jwks": HTTPClientProfile("jwks", httpx.Timeout(10.0, connect=5.0), 0, False, _limits()),
    "userinfo": HTTPClientProfile(
        "userinfo",
        httpx.Timeout(settings.oauth2_userinfo_timeout_seconds, connect=settings.oauth2_userinfo_timeout_seconds),
        0,
        False,
        _limits(),
    ),
    "langfuse_health": HTTPClientProfile("langfuse_health", httpx.Timeout(5.0, connect=5.0), 0, False, _limits()),
}

# Short aliases make the profile table convenient for callers and tests while
# keeping one canonical mapping used by the registry.
PROFILES = HTTP_CLIENT_PROFILES

_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.PoolTimeout)

# Retry counts are code policy, not environment settings. The CQL POST is safe
# to retry because `$evaluate` is a read-only evaluation operation; no other
# POST, PUT, or DELETE is admitted by these policies.
RETRY_POLICIES: dict[ProfileName, RetryPolicy] = {
    "fhir": RetryPolicy(attempts=3, methods=frozenset({"GET"})),
    "attachments": RetryPolicy(attempts=3, methods=frozenset({"GET"})),
    "cql": RetryPolicy(attempts=3, methods=frozenset({"POST"}), url_suffix="$evaluate"),
    "external_fhir": RetryPolicy(attempts=3, methods=frozenset({"GET"})),
    "external_patient": RetryPolicy(attempts=3, methods=frozenset({"GET"})),
    "jwks": RetryPolicy(attempts=1, methods=frozenset()),
    "userinfo": RetryPolicy(attempts=1, methods=frozenset()),
    "langfuse_health": RetryPolicy(attempts=1, methods=frozenset()),
}

TransportFactory = Callable[[HTTPClientProfile], httpx.AsyncBaseTransport]
_transport_factories: dict[ProfileName, TransportFactory] = {}
_lifespan_clients: dict[ProfileName, httpx.AsyncClient] | None = None
_lazy_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[ProfileName, httpx.AsyncClient]] = weakref.WeakKeyDictionary()

# Tests can replace this without sleeping through the backoff schedule.
_sleep = asyncio.sleep


def _profile_name(profile: str) -> ProfileName:
    if profile not in HTTP_CLIENT_PROFILES:
        raise ValueError(f"Unknown HTTP client profile: {profile}")
    return cast(ProfileName, profile)


def fhir_headers(target_url: str | None = None, *, content_type: bool = False, with_auth: bool = False) -> dict[str, str]:
    """Build FHIR headers and attach credentials only after an origin check.

    ``target_url`` is mandatory when ``with_auth`` is true. A configured secret
    is sent only to the configured external FHIR server origin; callers cannot
    opt into credentials without supplying the URL used by that check.
    """
    if with_auth and target_url is None:
        raise ValueError("target_url is required when with_auth is true")

    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = "application/fhir+json"
    headers["Accept"] = "application/fhir+json"

    if with_auth:
        target = target_url
        if target is None:
            raise ValueError("target_url is required when with_auth is true")
        auth = secret_value(settings.external_fhir_server_auth)
        external_base = settings.external_fhir_server_url
        if auth and external_base and is_same_origin(target, external_base):
            headers["Authorization"] = auth
    return headers


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


def _retry_after_delay(response: httpx.Response) -> float | None:
    """Parse a Retry-After seconds value or HTTP-date, if present."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except TypeError, ValueError, OverflowError:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _retry_delay(policy: RetryPolicy, response: httpx.Response | None, attempt: int) -> float:
    """Return a capped Retry-After delay or an exponential full-jitter delay."""
    if response is not None and response.status_code in {429, 503}:
        retry_after = _retry_after_delay(response)
        if retry_after is not None:
            return min(policy.max_delay, retry_after)
    jitter_cap = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    return random.uniform(0.0, jitter_cap)  # noqa: S311 - retry jitter, not a security value


def _retry_log_path(url: str) -> str:
    """Return a log-safe path without a query string or fragment."""
    try:
        return urlparse(url).path or "/"
    except ValueError:
        return "<upstream>"


async def _close_response(response: object) -> None:
    """Release a response discarded before a retry."""
    close = getattr(response, "aclose", None)
    if not callable(close):
        close = getattr(response, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _send_once(client: Any, method: str, url: str, kwargs: dict[str, Any]) -> httpx.Response:
    """Call a verb-specific async method, retaining compatibility with request-only fakes."""
    verb_method = getattr(client, method.lower(), None)
    if callable(verb_method):
        result = verb_method(url, **kwargs)
        if not inspect.isawaitable(result):
            raise TypeError(f"HTTP client method {method.lower()} did not return an awaitable")
        return cast(httpx.Response, await result)
    return await client.request(method, url, **kwargs)


async def send_with_retry(client: Any, method: str, url: str, *, profile: ProfileName, **kwargs: Any) -> httpx.Response:
    """Send an allowed outbound request with bounded transient-failure retries.

    Retryable responses are closed before sleeping. The final response is
    returned unchanged, while the final retryable exception is re-raised so
    each caller preserves its existing error/status mapping.
    """
    profile_name = _profile_name(profile)
    policy = RETRY_POLICIES[profile_name]
    method = method.upper()
    attempts = policy.attempts if policy.allows(method, url) else 1
    attempt = 1

    while True:
        try:
            response = await _send_once(client, method, url, kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            if attempt >= attempts:
                raise
            delay = _retry_delay(policy, None, attempt)
            logger.warning(f"Retrying {method} {_retry_log_path(url)} after {type(exc).__name__}; attempt {attempt + 1}/{attempts} in {delay:.2f}s")
            await _sleep(delay)
            attempt += 1
            continue

        if response.status_code not in _RETRYABLE_STATUSES or attempt >= attempts:
            return response

        delay = _retry_delay(policy, response, attempt)
        logger.warning(f"Retrying {method} {_retry_log_path(url)} after HTTP {response.status_code}; attempt {attempt + 1}/{attempts} in {delay:.2f}s")
        await _close_response(response)
        await _sleep(delay)
        attempt += 1


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
