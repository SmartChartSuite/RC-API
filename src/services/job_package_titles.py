"""In-process cache of job-package Questionnaire names and titles for client type-ahead search.

The cache is loaded once at startup (in the background, so a slow HAPI FHIR server cannot delay boot) and
reloaded only when a caller forces a refresh. Concurrent cold or forced loads share one upstream fetch.
A failed load never replaces a previously cached list. HTTP binding belongs in `src.routers.jobpackage`
and transport in `src.services.fhir_proxy`.
"""

import asyncio

from loguru import logger
from pydantic import BaseModel

from src.models.fhir import FHIRProxyResponse
from src.services.fhir_proxy import fhir_get_page_with_status, fhir_get_with_status
from src.util.fhir_bundle import get_bundle_next_link, get_bundle_resources

# Same Questionnaire context as GET /jobpackage; only the fields the title list needs are requested.
_SEARCH_PARAMS = {"context": "smartchartui", "_elements": "name,title", "_count": "500"}
_MAX_PAGES = 100


class JobPackageTitle(BaseModel):
    """One job package's display title and computable name."""

    name: str | None = None
    title: str | None = None


_cache: list[JobPackageTitle] | None = None
_lock = asyncio.Lock()


def _entries(bundle: object) -> list[JobPackageTitle]:
    entries = []
    for resource in get_bundle_resources(bundle, "Questionnaire"):
        name, title = resource.get("name"), resource.get("title")
        entries.append(JobPackageTitle(name=name if isinstance(name, str) else None, title=title if isinstance(title, str) else None))
    return entries


async def _fetch_all() -> list[JobPackageTitle] | FHIRProxyResponse:
    """Fetch every job-package page, or return the first failing upstream response."""
    result = await fhir_get_with_status("Questionnaire", params=dict(_SEARCH_PARAMS))
    titles: list[JobPackageTitle] = []
    for _ in range(_MAX_PAGES):
        if not 200 <= result.status_code < 300 or result.data.get("resourceType") != "Bundle":
            return result
        page = _entries(result.data)
        titles.extend(page)
        next_url = get_bundle_next_link(result.data)
        if not next_url or not page:
            break
        result = await fhir_get_page_with_status("Questionnaire", next_url)
    else:
        logger.warning(f"Job package title list truncated after {_MAX_PAGES} pages")
    return sorted(titles, key=lambda item: ((item.title or "").casefold(), (item.name or "").casefold()))


async def get_job_package_titles(*, refresh: bool = False) -> list[JobPackageTitle] | FHIRProxyResponse:
    """Return cached titles, loading them when absent or when ``refresh`` is requested."""
    global _cache
    if not refresh and (cached := _cache) is not None:
        return list(cached)
    async with _lock:
        # Another caller may have finished a load while this one waited; a forced refresh still refetches.
        if not refresh and _cache is not None:
            return list(_cache)
        result = await _fetch_all()
        if isinstance(result, FHIRProxyResponse):
            logger.warning(f"Job package title load failed with HTTP {result.status_code}; keeping the previous cache")
            return result
        _cache = result
        logger.info(f"Cached {len(result)} job package titles")
        return list(result)


async def warm_job_package_titles() -> None:
    """Startup load; failures are logged and the first request retries."""
    try:
        await get_job_package_titles(refresh=True)
    except Exception as exc:  # noqa: BLE001 - a background warm-up must never crash the app
        logger.warning(f"Job package title warm-up failed: {type(exc).__name__}")


def clear_job_package_titles() -> None:
    """Drop the cache (used by tests)."""
    global _cache
    _cache = None
