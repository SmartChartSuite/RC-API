"""Reusable stateless HTTP and FHIR resource fakes for the test suite.

HTTP responses are mocked per test with respx (the `respx_mock` fixture); this module keeps only a client
whose every call raises `httpx.RequestError` and a minimal Patient resource builder. Test-specific doubles
and assertions stay in their owning modules, with per-instance state only."""

from typing import Any

import httpx


class FailingAsyncClient:
    """Stand-in `httpx.AsyncClient` whose requests always fail with `httpx.RequestError`."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, params=None, json=None):
        raise httpx.RequestError("boom")

    async def get(self, url, params=None, headers=None):
        raise httpx.RequestError("boom")

    def stream(self, *args, **kwargs):
        raise httpx.RequestError("boom")


def patient_resource(patient_id: str, **fields: Any) -> dict[str, Any]:
    return {"resourceType": "Patient", "id": patient_id, **fields}
