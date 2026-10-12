"""OAuth 2 Bearer token validation and scope-based authorization dependencies.

Auth is gracefully disabled when OAUTH2_JWKS_URL is not set — all endpoints are
fully open without a token. In production, set OAUTH2_JWKS_URL to enable validation.

Dependencies:
    validate_token — required on all endpoints; returns decoded JWT claims dict.
    require_admin  — required on DELETE endpoints; asserts 'admin' in scope claim.
"""

import httpx
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from loguru import logger

from src.services.errorhandler import make_operation_outcome
from src.util.settings import oauth2_audience, oauth2_enabled, oauth2_issuer, oauth2_jwks_url

bearer_scheme = HTTPBearer(auto_error=False)

# JWKS cache — fetched once on first request; cleared by process restart
_jwks_cache: dict | None = None


async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        if not oauth2_jwks_url:
            return {}
        async with httpx.AsyncClient() as client:
            resp = await client.get(oauth2_jwks_url)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            logger.info(f"JWKS fetched from {oauth2_jwks_url}")
    assert _jwks_cache
    return _jwks_cache


async def validate_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict:
    """FastAPI dependency — validates the Bearer JWT and returns decoded claims.

    Returns an empty dict (no-op) when oauth2_enabled is False.
    Raises HTTP 401 if the token is missing, expired, or has an invalid signature.
    """
    if not oauth2_enabled:
        return {}

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail=make_operation_outcome("security", "Authorization header with Bearer token is required."),
        )

    token = credentials.credentials
    try:
        jwks = await _get_jwks()
        decode_options = {"verify_aud": bool(oauth2_audience)}
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            issuer=oauth2_issuer,
            audience=oauth2_audience,
            options=decode_options,
        )
    except JWTError as exc:
        logger.warning(f"JWT validation failed: {exc}")
        raise HTTPException(
            status_code=401,
            detail=make_operation_outcome("security", f"Token validation failed: {exc}"),
        )

    return claims


async def require_admin(
    claims: dict = Security(validate_token),
) -> None:
    """FastAPI dependency — asserts that the validated token has 'admin' scope.

    Chains from validate_token. Raises HTTP 403 if 'admin' is not in the scope claim.
    Use as `Security(require_admin)` on DELETE endpoints.
    """
    if not oauth2_enabled:
        return

    scopes: list[str] = claims.get("scope", "").split()
    if "admin" not in scopes:
        raise HTTPException(
            status_code=403,
            detail=make_operation_outcome("forbidden", "This operation requires 'admin' scope."),
        )
