from typing import Any, ClassVar

import httpx


class FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200, text: str | None = None):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text if text is not None else str(payload)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return self._payload


class FakeAsyncClient:
    queued_responses: ClassVar[list[list[FakeResponse]]] = []
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, timeout: Any = None, transport: Any = None):
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


def patient_resource(patient_id: str, **fields: Any) -> dict[str, Any]:
    return {"resourceType": "Patient", "id": patient_id, **fields}
