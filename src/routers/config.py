from fastapi import APIRouter

from src.models.config import ConfigEndpointModel
from src.util.settings import config_endpoint

router = APIRouter(tags=["Config"])


@router.get("/config", response_model=ConfigEndpointModel | dict)
async def get_config() -> ConfigEndpointModel | dict:
    """Return public API configuration values exposed for clients."""
    return config_endpoint
