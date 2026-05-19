"""Error handling utilities for v1."""

from fastapi.responses import JSONResponse


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


def config_error_response(missing_vars: list[str]) -> JSONResponse:
    """Returns a 503 JSONResponse with a FHIR OperationOutcome listing unconfigured env vars."""
    var_list = ", ".join(missing_vars)
    return JSONResponse(
        status_code=503,
        content=make_operation_outcome(
            "not-supported",
            f"This endpoint is not available because the following required environment variable(s) are not configured: {var_list}. Please check the server logs for details.",
        ),
    )
