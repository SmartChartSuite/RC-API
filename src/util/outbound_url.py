"""Helpers for safely resolving, validating, and comparing outbound HTTP URLs."""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterable
from urllib.parse import quote, urlparse

# FHIR R4 `id` datatype: 1–64 characters from [A-Za-z0-9\-\.].
_FHIR_ID_RE = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")


def is_valid_fhir_id(value: str | None) -> bool:
    """Return whether value is a syntactically valid FHIR resource id."""
    return isinstance(value, str) and bool(_FHIR_ID_RE.fullmatch(value)) and value not in {".", ".."}


def path_segment(value: str) -> str:
    """Percent-encode a value so it can only ever occupy one URL path segment."""
    return quote(value, safe="")


def resolve_http_reference(base_url: str, reference: str) -> str | None:
    """Resolve a relative reference against a base URL and reject unsafe URL forms."""
    candidate = reference.strip()
    if not candidate:
        return None

    parsed_reference = urlparse(candidate)
    resolved = candidate if parsed_reference.scheme else f"{base_url.rstrip('/')}/{candidate.lstrip('/')}"
    parsed = urlparse(resolved)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return resolved


def _origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse(url)
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname.casefold() if parsed.hostname else None
    if scheme not in {"http", "https"} or hostname is None:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def is_same_origin(first_url: str, second_url: str) -> bool:
    """Return whether two HTTP(S) URLs share scheme, hostname, and effective port."""
    first_origin = _origin(first_url)
    return first_origin is not None and first_origin == _origin(second_url)


def _is_internal_ip_literal(hostname: str) -> bool:
    """Return True when hostname is an IP literal in a non-public address range."""
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return _is_internal_address(address)


def _is_internal_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global


async def resolves_to_internal_address(url: str) -> bool:
    """Return True when any DNS answer for the URL's hostname is a non-public address.

    Guards allowlisted hostnames against pointing (or being re-pointed) at internal
    infrastructure. Unresolvable hosts return False — the subsequent fetch fails anyway.
    """
    origin = _origin(url)
    if origin is None:
        return True
    _, hostname, port = origin
    if _is_internal_ip_literal(hostname):
        return True
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    for info in infos:
        try:
            if _is_internal_address(ipaddress.ip_address(info[4][0])):
                return True
        except ValueError:
            continue
    return False


def is_allowed_attachment_url(url: str, trusted_base_url: str | None, allowed_hosts: Iterable[str]) -> bool:
    """Decide whether an attachment URL may be fetched.

    Allowed: the trusted FHIR server origin, or an explicitly allowlisted hostname
    (exact match, or a leading-dot entry such as ``.example.org`` for subdomains).
    Rejected: everything else, including IP literals in private, loopback,
    link-local (cloud metadata), and other reserved ranges on allowlisted hosts.
    """
    origin = _origin(url)
    if origin is None:
        return False
    _, hostname, _ = origin

    if trusted_base_url and is_same_origin(url, trusted_base_url):
        return True

    if _is_internal_ip_literal(hostname):
        return False

    for entry in allowed_hosts:
        allowed = entry.casefold()
        if allowed.startswith("."):
            if hostname.endswith(allowed):
                return True
        elif hostname == allowed:
            return True
    return False
