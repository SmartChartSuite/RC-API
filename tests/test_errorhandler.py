from src.services import errorhandler


def test_make_operation_outcome_builds_fhir_shape():
    outcome = errorhandler.make_operation_outcome("not-found", "missing resource", severity="warning")

    assert outcome == {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "warning",
                "code": "not-found",
                "diagnostics": "missing resource",
            }
        ],
    }


def test_operation_outcome_response_wraps_outcome_with_status_code():
    response = errorhandler.operation_outcome_response(404, "not-found", "missing resource")

    assert response.status_code == 404
    assert bytes(response.body).decode("utf-8") == '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"not-found","diagnostics":"missing resource"}]}'


def test_operation_outcome_responses_maps_status_codes_to_model():
    responses = errorhandler.operation_outcome_responses(400, 404)

    assert 400 in responses
    assert 404 in responses
    assert responses[400]["model"].__name__ == "OperationOutcome"


def test_config_error_response_returns_503_with_missing_vars():
    response = errorhandler.config_error_response(["FIRST_VAR", "SECOND_VAR"])

    assert response.status_code == 503
    body = bytes(response.body).decode("utf-8")
    assert "FIRST_VAR, SECOND_VAR" in body
    assert '"code":"not-supported"' in body
