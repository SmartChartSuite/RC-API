from src.models.fhir import LibraryResource, OperationOutcome
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
