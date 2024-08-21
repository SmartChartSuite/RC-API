"""Routing module for the API"""

import logging

from fastapi import APIRouter

from src.models.functions import get_health_of_stack, make_operation_outcome
from src.util.settings import ConfigEndpointModel, config_endpoint

# Create logger
logger: logging.Logger = logging.getLogger("rcapi.routers.routers")

router = APIRouter()


@router.get("/")
def root():
    """Root return function for the API"""
    return make_operation_outcome("processing", "This is the base URL of API. Unable to handle this request as it is the root.")


@router.get("/health")
def health_check() -> dict:
    """Health check endpoint"""
    return get_health_of_stack()


@router.get("/config", response_model_exclude_none=True)
def return_config() -> ConfigEndpointModel | dict:
    return config_endpoint