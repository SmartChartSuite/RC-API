"""Helpers for representing persisted document text in FHIR resources."""

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
