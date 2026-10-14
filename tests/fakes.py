"""Reusable HTTP and FHIR resource fakes for the test suite.

This module supplies queued and failing clients, response wrappers, and a Patient factory; test-specific
fixtures and assertions stay in their owning modules."""

from typing import Any, ClassVar

import httpx


class FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200, text: str | None = None, headers: dict[str, str] | None = None):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text if text is not None else str(payload)
        self.headers = headers or {}
        self.encoding = "utf-8"

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return self._payload

    async def aiter_bytes(self):
        yield self.text.encode("utf-8")


class FakeAsyncClient:
    queued_responses: ClassVar[list[list[FakeResponse]]] = []
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, timeout: Any = None, transport: Any = None, **kwargs: Any):
        del timeout, transport, kwargs
        self._responses = type(self).queued_responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, params=None, json=None):
        if method != "GET":
            raise AssertionError(f"Unexpected fake HTTP method: {method}")
        return await self.get(url, params=params, headers=headers)

    async def get(self, url, params=None, headers=None):
        type(self).calls.append({"url": url, "params": params, "headers": headers})
        return self._responses.pop(0)

    def stream(self, method, url, headers=None, params=None, follow_redirects=None):
        if method != "GET":
            raise AssertionError(f"Unexpected fake HTTP method: {method}")
        type(self).calls.append({"url": url, "params": params, "headers": headers})
        return _FakeStream(self._responses.pop(0))


class _FakeStream:
    def __init__(self, response: FakeResponse):
        self._response = response

    async def __aenter__(self) -> FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FailingAsyncClient:
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
