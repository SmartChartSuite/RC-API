from fastapi import APIRouter, Security

from src.models.config import ConfigEndpointModel
from src.util.auth import validate_token
from src.util.settings import config_endpoint

router = APIRouter(tags=["Config"])


@router.get("/config", response_model=ConfigEndpointModel | dict)
async def get_config(_claims: dict = Security(validate_token)) -> ConfigEndpointModel | dict:
    """Return public API configuration values exposed for clients."""
    return config_endpoint
