"""Error handling utilities for v1."""

from collections.abc import Mapping
from typing import Any, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.models.fhir import OperationOutcome

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
