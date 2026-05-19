"""CRUD /jobpackage — Questionnaire resources proxied to HAPI FHIR."""

from fastapi import APIRouter, Security

from src.services.errorhandler import config_error_response
from src.services.fhir_proxy import fhir_delete, fhir_get, fhir_post, fhir_put
from src.util.auth import require_admin, validate_token
from src.util.settings import config_errors

router = APIRouter(tags=["Job Packages"])


def _check_config():
    if "HAPI_FHIR_CQL_EXECUTION_URL" in config_errors:
        return config_error_response(["HAPI_FHIR_CQL_EXECUTION_URL"])
    return None


@router.get("/jobpackage")
async def search_job_packages(
    name: str | None = None,
    version: str | None = None,
    claims: dict = Security(validate_token),
):
    """Search job package Questionnaires on HAPI FHIR."""
    if err := _check_config():
        return err
    params: dict = {}
    if name:
        params["name"] = name
    if version:
        params["version"] = version
    return await fhir_get("Questionnaire", params=params)


@router.get("/jobpackage/{resource_id}")
async def get_job_package(
    resource_id: str,
    claims: dict = Security(validate_token),
):
    """Get a specific job package Questionnaire by ID."""
    if err := _check_config():
        return err
    return await fhir_get("Questionnaire", resource_id)


@router.post("/jobpackage")
async def create_job_package(
    body: dict,
    claims: dict = Security(validate_token),
):
    """Create a new job package Questionnaire on HAPI FHIR."""
    if err := _check_config():
        return err
    return await fhir_post("Questionnaire", body)


@router.put("/jobpackage/{resource_id}")
async def update_job_package(
    resource_id: str,
    body: dict,
    claims: dict = Security(validate_token),
):
    """Update an existing job package Questionnaire."""
    if err := _check_config():
        return err
    return await fhir_put("Questionnaire", resource_id, body)


@router.delete("/jobpackage/{resource_id}")
async def delete_job_package(
    resource_id: str,
    _: None = Security(require_admin),
):
    """Delete a job package Questionnaire. Requires 'admin' scope."""
    if err := _check_config():
        return err
    return await fhir_delete("Questionnaire", resource_id)
