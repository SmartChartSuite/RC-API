"""Helpers for safely resolving and comparing outbound HTTP URLs."""

from urllib.parse import urlparse


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


def is_same_origin(first_url: str, second_url: str) -> bool:
    """Return whether two HTTP(S) URLs share scheme, hostname, and effective port."""

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

    first_origin = _origin(first_url)
    return first_origin is not None and first_origin == _origin(second_url)
