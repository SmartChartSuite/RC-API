"""Tests OperationOutcome construction, configuration-error responses, and FHIR proxy status mapping.

The module validates model/error payload selection in `src.services.errorhandler`; proxy transport
behavior is exercised separately in `test_fhir_proxy.py`."""

from typing import cast

import pytest
from fastapi.responses import JSONResponse

from src.models.fhir import FHIRProxyResponse, FHIRProxyResult, LibraryResource, OperationOutcome
from src.services import errorhandler


def test_operation_outcome_response_wraps_outcome_with_status_code():
    response = errorhandler.operation_outcome_response(404, "not-found", "missing resource")

    assert response.status_code == 404
    assert bytes(response.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing resource"}]}'


def test_config_error_for_reports_missing_variables_as_503():
    response = errorhandler.config_error_for({"HAPI_FHIR_CQL_EXECUTION_URL": "missing"}, "HAPI_FHIR_CQL_EXECUTION_URL")

    assert response is not None
    assert response.status_code == 503
    assert "HAPI_FHIR_CQL_EXECUTION_URL" in bytes(response.body).decode("utf-8")
    assert errorhandler.config_error_for({}, "HAPI_FHIR_CQL_EXECUTION_URL") is None


def test_model_or_operation_outcome_validates_resource_or_error():
    library = errorhandler.model_or_operation_outcome({"resourceType": "Library", "id": "lib-1"}, LibraryResource)
    outcome = errorhandler.model_or_operation_outcome(
        {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found"}]},
        LibraryResource,
    )

    assert isinstance(library, LibraryResource)
    assert isinstance(outcome, OperationOutcome)


@pytest.mark.parametrize(
    ("status_code", "error_type", "expected_status"),
    [
        (404, None, 404),
        (302, None, 502),
        (401, None, 502),
        (500, None, 502),
        (504, None, 502),
        (504, "timeout", 504),
        (502, "transport", 502),
        (400, None, 400),
    ],
)
def test_fhir_result_response_maps_proxy_statuses(status_code, error_type, expected_status):
    data = cast(FHIRProxyResult, {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "invalid", "diagnostics": "safe"}]})

    result = FHIRProxyResponse(data=data, status_code=status_code, error_type=error_type)
    response = errorhandler.fhir_result_response(result, LibraryResource)

    assert isinstance(response, JSONResponse)
    assert response.status_code == expected_status
    assert bytes(response.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"invalid","diagnostics":"safe"}]}'
