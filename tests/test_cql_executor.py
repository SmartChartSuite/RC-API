from src.services.cql_executor import _parse_parameters_response


def test_parse_parameters_response_returns_error_for_top_level_operation_outcome():
    result = _parse_parameters_response(
        "ExampleLibrary",
        "patient-123",
        {
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "exception",
                    "diagnostics": "top-level failure",
                }
            ],
        },
    )

    assert result.error == "top-level failure"
    assert result.results == {}


def test_parse_parameters_response_returns_error_for_embedded_evaluation_error():
    result = _parse_parameters_response(
        "ExampleLibrary",
        "patient-123",
        {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "evaluation error",
                    "resource": {
                        "resourceType": "OperationOutcome",
                        "issue": [
                            {
                                "severity": "error",
                                "code": "exception",
                                "diagnostics": "Expected a list with at most one element, but found a list with multiple elements.",
                            }
                        ],
                    },
                }
            ],
        },
    )

    assert result.error == "Expected a list with at most one element, but found a list with multiple elements."
    assert result.results == {}


def test_parse_parameters_response_groups_normal_results():
    result = _parse_parameters_response(
        "ExampleLibrary",
        "patient-123",
        {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "simple", "valueString": "value-1"},
                {"name": "simple", "valueBoolean": True},
                {"name": "simple_concept", "valueString": "ignore-me"},
            ],
        },
    )

    assert result.error is None
    assert result.results == {"simple": [{"value": "value-1"}, {"value": True}]}
