"""Router file for NLPQL-related operations"""

import logging

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from src.models.functions import make_operation_outcome
from src.services.libraryhandler import create_nlpql, get_library
from src.util.settings import cqfr4_fhir, session

logger: logging.Logger = logging.getLogger("rcapi.routers.nlpql_router")

router = APIRouter()


@router.get("/forms/nlpql")
def get_nlpql_libraries():
    """Pulls list of CQL libraries from CQF Ruler"""

    req = session.get(cqfr4_fhir + "Library?content-type=text/nlpql")
    if req.status_code == 200:
        return req.json()

    logger.error(f"Getting NLPQL Libraries from server failed with status code {req.status_code}")
    return make_operation_outcome("transient", f"Getting NLPQL Libraries from server failed with code {req.status_code}")


@router.get("/forms/nlpql/{library_name}")
def get_nlpql(library_name: str) -> str | dict:
    """Return NLPQL library by name"""
    return get_library(library_name=library_name, library_type="nlpql")


@router.post("/forms/nlpql")
def save_nlpql(code: str = Body(...)):
    """Persist NLPQL as a Library Resource on CQF Ruler"""
    resource_id = create_nlpql(code)
    if isinstance(resource_id, str):
        return JSONResponse(content=make_operation_outcome("informational", f"Resource successfully posted with id {resource_id}", severity="information"), status_code=201)
    elif isinstance(resource_id, dict):
        return JSONResponse(content=resource_id, status_code=400)


@router.put("/forms/nlpql/{library_name}")
def update_nlpql(library_name: str, code: str = Body(...)):
    """Update NLPQL Library on CQF Ruler"""
    resource_id = create_nlpql(code)
    if isinstance(resource_id, str):
        return JSONResponse(content=make_operation_outcome("informational", f"Resource successfully PUT with id {resource_id}", severity="information"), status_code=201)
    elif isinstance(resource_id, dict):
        return JSONResponse(content=resource_id, status_code=400)
