"""Build encoded FHIR attachment content for persisted document text.

This module converts text to `text/plain` base64 attachment structures and inserts
that content into DocumentReference-like dictionaries without overwriting existing
content. It contains representation helpers only; document retrieval and filtering
belong to `src.services.fhir_context`, and outbound HTTP belongs to
`src.util.http_clients`.
"""

import base64
from typing import Any


def encode_document_text(text: str) -> dict[str, dict[str, str]]:
    """Return the text/plain FHIR attachment used for a DocumentReference."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"attachment": {"contentType": "text/plain", "data": encoded}}


def add_document_text_content(resource: dict[str, Any], text: str) -> None:
    """Add encoded text content after a DocumentReference's date element."""
    content = [encode_document_text(text)]
    if "content" in resource:
        return
    if "date" not in resource:
        resource["content"] = content
        return

    with_content: dict[str, Any] = {}
    for key, value in resource.items():
        with_content[key] = value
        if key == "date":
            with_content["content"] = content
    resource.clear()
    resource.update(with_content)
