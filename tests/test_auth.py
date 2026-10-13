"""Security tests for OAuth token validation (src/util/auth.py)."""

import time
from typing import ClassVar, cast

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwk, jwt

from src.util import auth

ISSUER = "https://idp.example/"
AUDIENCE = "rc-api"
JWKS_URL = "https://idp.example/jwks"


def _keypair(kid: str):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    public_pem = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    public_jwk = jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    public_jwk = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in public_jwk.items()}
    return pem, public_jwk


@pytest.fixture(scope="module")
def key_a():
    return _keypair("key-a")


@pytest.fixture(scope="module")
def key_b():
    return _keypair("key-b")


def _token(private_pem: str, kid: str, **overrides) -> str:
    claims = {"sub": "user-1", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300, "scope": "read"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": kid})


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class _JWKSClient:
    """Fake httpx.AsyncClient serving a mutable JWKS document."""

    jwks: ClassVar[dict | Exception] = {"keys": []}
    fetches: ClassVar[int] = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        type(self).fetches += 1
        payload = type(self).jwks
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))


@pytest.fixture
def oauth(monkeypatch, key_a):
    auth._reset_jwks_cache()
    _JWKSClient.jwks = {"keys": [key_a[1]]}
    _JWKSClient.fetches = 0
    monkeypatch.setattr(auth, "oauth2_enabled", True)
    monkeypatch.setattr(auth, "oauth2_missing_config", [])
    monkeypatch.setattr(auth, "oauth2_jwks_url", JWKS_URL)
    monkeypatch.setattr(auth, "oauth2_issuer", ISSUER)
    monkeypatch.setattr(auth, "oauth2_audience", AUDIENCE)
    monkeypatch.setattr(auth, "_JWKS_MIN_REFRESH_SECONDS", 0)
    monkeypatch.setattr(auth.httpx, "AsyncClient", _JWKSClient)
    yield
    auth._reset_jwks_cache()


async def test_valid_token_returns_claims(oauth, key_a):
    claims = await auth.validate_token(_creds(_token(key_a[0], "key-a")))

    assert claims["sub"] == "user-1"


@pytest.mark.parametrize(
    "overrides",
    [{"iss": "https://evil.example/"}, {"aud": "some-other-service"}, {"exp": int(time.time()) - 10}, {"aud": None}],
    ids=["wrong-issuer", "wrong-audience", "expired", "no-audience"],
)
async def test_invalid_claims_are_rejected_without_leaking_details(oauth, key_a, overrides):
    with pytest.raises(HTTPException) as exc_info:
        await auth.validate_token(_creds(_token(key_a[0], "key-a", **overrides)))

    assert exc_info.value.status_code == 401
    detail = cast(dict, exc_info.value.detail)
    assert detail["issue"][0]["diagnostics"] == "The bearer token is invalid or expired."


async def test_unknown_kid_triggers_jwks_refresh_for_key_rotation(oauth, key_a, key_b):
    await auth.validate_token(_creds(_token(key_a[0], "key-a")))
    await auth.validate_token(_creds(_token(key_a[0], "key-a")))
    assert _JWKSClient.fetches == 1  # cached

    _JWKSClient.jwks = {"keys": [key_a[1], key_b[1]]}
    claims = await auth.validate_token(_creds(_token(key_b[0], "key-b")))

    assert claims["sub"] == "user-1"
    assert _JWKSClient.fetches == 2


async def test_jwks_outage_returns_503(oauth, key_a):
    _JWKSClient.jwks = httpx.ConnectError("idp down")

    with pytest.raises(HTTPException) as exc_info:
        await auth.validate_token(_creds(_token(key_a[0], "key-a")))

    assert exc_info.value.status_code == 503


async def test_partial_oauth_config_fails_closed_with_503(oauth, monkeypatch, key_a):
    monkeypatch.setattr(auth, "oauth2_missing_config", ["OAUTH2_AUDIENCE"])

    with pytest.raises(HTTPException) as exc_info:
        await auth.validate_token(_creds(_token(key_a[0], "key-a")))

    assert exc_info.value.status_code == 503


@pytest.mark.parametrize(("claims", "allowed"), [({"scope": "read admin"}, True), ({"scope": "administrator"}, False)])
async def test_require_admin_scope(monkeypatch, claims, allowed):
    monkeypatch.setattr(auth, "oauth2_enabled", True)

    if allowed:
        assert await auth.require_admin(claims) is None
    else:
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_admin(claims)
        assert exc_info.value.status_code == 403
