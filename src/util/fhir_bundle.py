"""Tolerant helpers for reading FHIR Bundle resources and pagination links."""

from typing import Any


def get_bundle_resources(bundle: object, resource_type: str | None = None) -> list[dict[str, Any]]:
    """Return resource dictionaries from Bundle entries, optionally by type."""
    if not isinstance(bundle, dict):
        return []
    raw_entries = bundle.get("entry")
    if not isinstance(raw_entries, list):
        return []

    resources: list[dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            continue
        if resource_type is not None and resource.get("resourceType") != resource_type:
            continue
        resources.append(resource)
    return resources


def get_bundle_next_link(bundle: object) -> str | None:
    """Return the first string-valued ``next`` link from a Bundle."""
    if not isinstance(bundle, dict):
        return None
    raw_links = bundle.get("link")
    if not isinstance(raw_links, list):
        return None

    for link in raw_links:
        if isinstance(link, dict) and link.get("relation") == "next":
            url = link.get("url")
            if isinstance(url, str):
                return url
    return None
