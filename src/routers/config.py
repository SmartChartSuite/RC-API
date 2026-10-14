"""HTTP adapter for the client-facing `/config` endpoint.

This module authenticates requests and exposes the configuration projection returned
by `src.util.settings`. It contains no settings loading or validation and no
application behavior; configuration parsing belongs to `src.util.settings`, while
other HTTP endpoints belong in their respective routers.
"""

from fastapi import APIRouter, Security

from src.models.config import ConfigEndpointModel
from src.util.auth import validate_token
from src.util.settings import settings

router = APIRouter(tags=["Config"])


@router.get("/config", response_model=ConfigEndpointModel | dict)
async def get_config(_claims: dict = Security(validate_token)) -> ConfigEndpointModel | dict:
    """Return public API configuration values exposed for clients."""
    return settings.config_endpoint
