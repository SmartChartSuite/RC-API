import json
import typing

from fastapi import Response


class PrettyJSONResponse(Response):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, allow_nan=False, indent=4, separators=(", ", ": "), default=str).encode("utf-8")
