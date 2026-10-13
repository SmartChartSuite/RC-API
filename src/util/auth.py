"""OAuth 2 Bearer token validation and scope-based authorization dependencies.

Authentication fails closed: it is enforced unless AUTH_DISABLED=true is set
explicitly (local development only). When enforced, OAUTH2_JWKS_URL,
OAUTH2_ISSUER, and OAUTH2_AUDIENCE must all be configured; otherwise protected
endpoints return 503 instead of silently allowing anonymous access.

Dependencies:
    validate_token — required on all endpoints; returns decoded JWT claims dict.
    require_admin  — required on DELETE endpoints; asserts 'admin' in scope claim.
"""

import asyncio
import time

import httpx
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from loguru import logger

from src.services.errorhandler import make_operation_outcome
from src.util.settings import (
    oauth2_audience,
    oauth2_enabled,
    oauth2_issuer,
    oauth2_jwks_cache_seconds,
    oauth2_jwks_url,
    oauth2_missing_config,
)

bearer_scheme = HTTPBearer(auto_error=False)

_JWKS_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# Minimum seconds between forced refreshes triggered by an unknown key id, to stop
# tokens with random `kid` values from hammering the identity provider.
_JWKS_MIN_REFRESH_SECONDS = 60

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()


class JWKSUnavailableError(Exception):
    """The signing keys could not be retrieved or were malformed."""


def _reset_jwks_cache() -> None:
    """Clear the cached JWKS (used by tests and key-rotation tooling)."""
    global _jwks_cache, _jwks_fetched_at
    _jwks_cache = None
    _jwks_fetched_at = 0.0


async def _fetch_jwks() -> dict:
    if not oauth2_jwks_url:
        raise JWKSUnavailableError("OAUTH2_JWKS_URL is not configured")
    try:
        async with httpx.AsyncClient(timeout=_JWKS_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(oauth2_jwks_url)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise JWKSUnavailableError(f"JWKS fetch failed: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list) or not data["keys"]:
        raise JWKSUnavailableError("JWKS response did not contain a non-empty 'keys' list")
    logger.info(f"JWKS fetched from {oauth2_jwks_url} ({len(data['keys'])} key(s))")
    return data


async def _get_jwks(*, force_refresh: bool = False) -> dict:
    """Return cached signing keys, refreshing after the TTL or when forced."""
    global _jwks_cache, _jwks_fetched_at
    async with _jwks_lock:
        now = time.monotonic()
        age = now - _jwks_fetched_at
        expired = _jwks_cache is None or age >= oauth2_jwks_cache_seconds
        refresh_allowed = force_refresh and age >= _JWKS_MIN_REFRESH_SECONDS
        if expired or refresh_allowed:
            try:
                _jwks_cache = await _fetch_jwks()
                _jwks_fetched_at = now
            except JWKSUnavailableError:
                if _jwks_cache is None:
                    raise
                # Keep serving the previous keys during a transient provider outage.
                logger.warning("JWKS refresh failed; continuing with previously cached keys.", exc_info=True)
        if _jwks_cache is None:
            raise JWKSUnavailableError("JWKS is unavailable")
        return _jwks_cache


def _has_kid(jwks: dict, kid: str | None) -> bool:
    if kid is None:
        return True
    return any(isinstance(key, dict) and key.get("kid") == kid for key in jwks.get("keys", []))


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=make_operation_outcome("security", message),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def validate_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict:
    """FastAPI dependency — validates the Bearer JWT and returns decoded claims.

    Returns an empty dict only when authentication is explicitly disabled.
    Raises 401 for a missing/invalid token and 503 when auth is misconfigured
    or the identity provider's signing keys are unavailable.
    """
    if not oauth2_enabled:
        return {}

    if credentials is None:
        raise _unauthorized("Authorization header with Bearer token is required.")

    if oauth2_missing_config:
        raise HTTPException(
            status_code=503,
            detail=make_operation_outcome("not-supported", "Authentication is not configured on this server. See the server logs for details."),
        )

    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        jwks = await _get_jwks()
        if not _has_kid(jwks, header.get("kid")):
            jwks = await _get_jwks(force_refresh=True)
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            issuer=oauth2_issuer,
            audience=oauth2_audience,
            options={"verify_aud": True, "verify_iss": True, "require_exp": True, "require_iss": True, "require_aud": True},
        )
    except JWKSUnavailableError as exc:
        logger.error(f"Cannot validate tokens: {exc}")
        raise HTTPException(
            status_code=503,
            detail=make_operation_outcome("transient", "Token signing keys are temporarily unavailable. Please retry."),
        ) from exc
    except JWTError as exc:
        logger.warning(f"JWT validation failed: {exc}")
        raise _unauthorized("The bearer token is invalid or expired.") from exc

    return claims


def _scopes(claims: dict) -> set[str]:
    """Read OAuth scopes from a space-delimited `scope` string or a list-valued `scp` claim."""
    scope = claims.get("scope", claims.get("scp", ""))
    if isinstance(scope, str):
        return set(scope.split())
    if isinstance(scope, list):
        return {item for item in scope if isinstance(item, str)}
    return set()


async def require_admin(
    claims: dict = Security(validate_token),
) -> None:
    """FastAPI dependency — asserts that the validated token has 'admin' scope.

    Chains from validate_token. Raises HTTP 403 if 'admin' is not in the scope claim.
    Use as `Security(require_admin)` on DELETE endpoints.
    """
    if not oauth2_enabled:
        return

    scopes = _scopes(claims)
    if "admin" not in scopes:
        raise HTTPException(
            status_code=403,
            detail=make_operation_outcome("forbidden", "This operation requires 'admin' scope."),
        )
