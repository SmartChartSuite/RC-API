"""Router file for CQL-related operations"""

import logging

import requests
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from src.models.functions import make_operation_outcome
from src.services.libraryhandler import create_cql, get_library
from src.util.settings import cqfr4_fhir

logger: logging.Logger = logging.getLogger("rcapi.routers.cql_router")

router = APIRouter()


@router.get("/forms/cql")
def get_cql_libraries():
    """Pulls list of CQL libraries from CQF Ruler"""

    req = requests.get(cqfr4_fhir + "Library?content-type=text/cql")
    if req.status_code == 200:
        return req.json()

    logger.error(f"Getting CQL Libraries from server failed with status code {req.status_code}")
    return make_operation_outcome("transient", f"Getting CQL Libraries from server failed with code {req.status_code}")


@router.get("/forms/cql/{library_name}")
def get_cql(library_name: str) -> str | dict:
    """Return CQL library based on name"""
    return get_library(library_name=library_name, library_type="cql")


@router.post("/forms/cql")
def save_cql(code: str = Body(...)):
    """Persist CQL as a Library Resource on CQF Ruler"""
    resource_id = create_cql(code)
    # Empty body is handled by FASTAPI when parsing the request body. This handling is used as a fallback for any other potential ValueErrors.
    if isinstance(resource_id, ValueError):
        return JSONResponse(content=make_operation_outcome("invalid", "Value Error"), status_code=400)
    # TODO: Add additional error handling.
    return JSONResponse(content=make_operation_outcome("informational", f"Resource successfully posted with id {resource_id}", severity="information"), status_code=201)


@router.put("/forms/cql/{library_name}")
def update_cql(library_name: str, code: str = Body(...)):
    """Update CQL Library in CQF Ruler by name"""
    resource_id = create_cql(code)
    # Empty body is handled by FASTAPI when parsing the request body. This handling is used as a fallback for any other potential ValueErrors.
    if isinstance(resource_id, ValueError):
        return JSONResponse(content=make_operation_outcome("invalid", "Value Error"), status_code=400)
    return JSONResponse(content=make_operation_outcome("informational", f"Resource successfully PUT with id {resource_id}", severity="information"), status_code=201)
