"""Tests the cached job-package title list behind `GET /jobpackage/title`.

HAPI FHIR is mocked per test with respx; the autouse conftest fixture clears the cache and disables the
lifespan warm-up, so each test controls exactly when the upstream search runs."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.services import job_package_titles
from src.services.job_package_titles import JobPackageTitle, get_job_package_titles, warm_job_package_titles
from src.util import http_clients
from src.util.settings import settings

HAPI_BASE = "http://localhost:8080/fhir"
SEARCH_PARAMS = {"context": "smartchartui", "_elements": "name,title", "_count": "500"}


def _bundle(*resources: dict, next_url: str | None = None) -> dict:
    bundle: dict = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": resource} for resource in resources]}
    if next_url:
        bundle["link"] = [{"relation": "next", "url": next_url}]
    return bundle


def _questionnaire(name: str | None, title: str | None) -> dict:
    resource: dict = {"resourceType": "Questionnaire"}
    if name is not None:
        resource["name"] = name
    if title is not None:
        resource["title"] = title
    return resource


@pytest.fixture(autouse=True)
def _hapi_base(monkeypatch):
    async def _no_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(settings, "hapi_fhir_cql_execution_url", HAPI_BASE)
    monkeypatch.setattr(http_clients, "_sleep", _no_sleep)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_enabled", False)
    monkeypatch.setattr(settings, "config_errors", {})
    with TestClient(app) as test_client:
        yield test_client


def test_title_list_is_cached_until_refresh(client, respx_mock):
    route = respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS)
    route.side_effect = [
        httpx.Response(200, json=_bundle(_questionnaire("Syphilis", "Syphilis Follow-Up"), _questionnaire("Hep", "hepatitis B Case"))),
        httpx.Response(200, json=_bundle(_questionnaire("Hep", "Hepatitis B Case"))),
    ]

    first = client.get("/jobpackage/title")
    cached = client.get("/jobpackage/title")

    assert first.status_code == 200
    assert first.json() == [{"name": "Hep", "title": "hepatitis B Case"}, {"name": "Syphilis", "title": "Syphilis Follow-Up"}]
    assert cached.json() == first.json()
    assert route.call_count == 1

    refreshed = client.get("/jobpackage/title", params={"refresh": "true"})

    assert refreshed.status_code == 200
    assert refreshed.json() == [{"name": "Hep", "title": "Hepatitis B Case"}]
    assert route.call_count == 2
    assert client.get("/jobpackage/title").json() == refreshed.json()
    assert route.call_count == 2


def test_title_route_is_not_treated_as_a_questionnaire_id(client, respx_mock):
    respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS).mock(return_value=httpx.Response(200, json=_bundle()))
    by_id = respx_mock.get(f"{HAPI_BASE}/Questionnaire/title")

    response = client.get("/jobpackage/title")

    assert response.status_code == 200
    assert response.json() == []
    assert not by_id.called


async def test_titles_follow_same_origin_next_links_and_keep_missing_fields(respx_mock):
    next_url = f"{HAPI_BASE}?_getpages=abc&_getpagesoffset=1"
    respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS).mock(return_value=httpx.Response(200, json=_bundle(_questionnaire("B", "Beta"), next_url=next_url)))
    page_two = respx_mock.get(next_url).mock(return_value=httpx.Response(200, json=_bundle(_questionnaire("NoTitle", None), _questionnaire(None, "Alpha"))))

    result = await get_job_package_titles()

    assert page_two.called
    assert result == [JobPackageTitle(name="NoTitle"), JobPackageTitle(title="Alpha"), JobPackageTitle(name="B", title="Beta")]


async def test_titles_refuse_cross_origin_next_link(respx_mock):
    respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS).mock(return_value=httpx.Response(200, json=_bundle(_questionnaire("A", "Alpha"), next_url="http://evil.example/fhir?page=2")))

    result = await get_job_package_titles()

    assert not isinstance(result, list)
    assert result.status_code == 502


def test_failed_refresh_keeps_previous_cache(client, respx_mock):
    route = respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS)
    responses = iter([httpx.Response(200, json=_bundle(_questionnaire("A", "Alpha")))])

    def _first_ok_then_down(request):
        response = next(responses, None)
        if response is None:
            raise httpx.ConnectError("down", request=request)
        return response

    route.side_effect = _first_ok_then_down

    assert client.get("/jobpackage/title").json() == [{"name": "A", "title": "Alpha"}]

    failed = client.get("/jobpackage/title", params={"refresh": "true"})

    assert failed.status_code == 502
    assert failed.json()["resourceType"] == "OperationOutcome"
    assert client.get("/jobpackage/title").json() == [{"name": "A", "title": "Alpha"}]


def test_upstream_error_is_not_cached(client, respx_mock):
    route = respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS)
    route.side_effect = [httpx.Response(500, text="boom"), httpx.Response(200, json=_bundle(_questionnaire("A", "Alpha")))]

    assert client.get("/jobpackage/title").status_code == 502
    assert client.get("/jobpackage/title").json() == [{"name": "A", "title": "Alpha"}]
    assert route.call_count == 2


def test_title_list_returns_config_error(client, monkeypatch):
    monkeypatch.setattr(settings, "config_errors", {"HAPI_FHIR_CQL_EXECUTION_URL": "missing"})

    assert client.get("/jobpackage/title").status_code == 503


async def test_concurrent_cold_requests_share_one_fetch(monkeypatch):
    release = asyncio.Event()
    calls = 0

    async def _slow_fetch():
        nonlocal calls
        calls += 1
        await release.wait()
        return [JobPackageTitle(name="A", title="Alpha")]

    monkeypatch.setattr(job_package_titles, "_fetch_all", _slow_fetch)
    first = asyncio.create_task(get_job_package_titles())
    second = asyncio.create_task(get_job_package_titles())
    await asyncio.sleep(0)
    release.set()

    assert await first == await second == [JobPackageTitle(name="A", title="Alpha")]
    assert calls == 1


async def test_warm_up_loads_cache_and_swallows_failures(monkeypatch, respx_mock):
    route = respx_mock.get(f"{HAPI_BASE}/Questionnaire", params=SEARCH_PARAMS)
    route.side_effect = [httpx.Response(200, json=_bundle(_questionnaire("A", "Alpha")))]

    await warm_job_package_titles()
    assert await get_job_package_titles() == [JobPackageTitle(name="A", title="Alpha")]
    assert route.call_count == 1

    async def _boom(**_):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(job_package_titles, "get_job_package_titles", _boom)
    await warm_job_package_titles()
