"""Error handling utilities for v1."""

from collections.abc import Mapping
from typing import Any, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.models.fhir import FHIRProxyResponse, OperationOutcome

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def make_operation_outcome(code: str, diagnostics: str, severity: str = "error") -> dict:
    """Returns a FHIR OperationOutcome dict for a given code, diagnostics, and severity."""
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ],
    }


def operation_outcome_response(status_code: int, code: str, diagnostics: str, severity: str = "error") -> JSONResponse:
    """Build a JSONResponse whose content is a FHIR OperationOutcome."""
    return JSONResponse(
        status_code=status_code,
        content=make_operation_outcome(code, diagnostics, severity),
    )


def operation_outcome_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI response metadata for endpoints that return OperationOutcomes."""
    return {status_code: {"model": OperationOutcome} for status_code in status_codes}


FHIR_PROXY_ERROR_RESPONSES = operation_outcome_responses(400, 404, 409, 410, 412, 422, 502, 503, 504)


def config_error_response(missing_vars: list[str]) -> JSONResponse:
    """Returns a 503 JSONResponse with a FHIR OperationOutcome listing unconfigured env vars."""
    var_list = ", ".join(missing_vars)
    return operation_outcome_response(
        503,
        "not-supported",
        f"This endpoint is not available because the following required environment variable(s) are not configured: {var_list}. Please check the server logs for details.",
    )


def config_error_for(errors: Mapping[str, str], *required_vars: str) -> JSONResponse | None:
    """Return the standard 503 response when a required setting is missing."""
    missing_vars = [var for var in required_vars if var in errors]
    return config_error_response(missing_vars) if missing_vars else None


def model_or_operation_outcome(data: Mapping[str, Any], model: type[_ModelT]) -> _ModelT | OperationOutcome:
    """Validate a FHIR payload as an OperationOutcome or the requested resource model."""
    if data.get("resourceType") == "OperationOutcome":
        return OperationOutcome.model_validate(data)
    return model.model_validate(data)


def _map_fhir_status(status_code: int, error_type: str | None = None) -> int:
    """Map an upstream or proxy failure to the status exposed by RC-API."""
    if error_type == "timeout":
        return 504
    if error_type == "transport":
        return 502
    if 200 <= status_code < 300:
        return status_code
    if status_code in {401, 403} or not 400 <= status_code < 500:
        return 502
    return status_code


def fhir_result_response(result: FHIRProxyResponse, model: type[_ModelT]) -> _ModelT | OperationOutcome | JSONResponse:
    """Validate successful FHIR data or return a mapped OperationOutcome response."""
    if 200 <= result.status_code < 300:
        return model_or_operation_outcome(result.data, model)
    return JSONResponse(status_code=_map_fhir_status(result.status_code, result.error_type), content=dict(result.data))
