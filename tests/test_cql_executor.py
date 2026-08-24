import asyncio

from src.services import cql_executor
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


async def test_run_cql_libraries_reports_each_result_as_it_finishes(monkeypatch):
    callback_order: list[str] = []

    class _FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def _fake_evaluate_library(client, library_name, patient_id):
        if library_name == "slow":
            await asyncio.sleep(0.01)
        return cql_executor.CqlResult(library_name=library_name, patient_id=patient_id)

    async def _on_result(result):
        callback_order.append(result.library_name)

    monkeypatch.setattr(cql_executor.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient())
    monkeypatch.setattr(cql_executor, "_evaluate_library", _fake_evaluate_library)

    results = await cql_executor.run_cql_libraries(["slow", "fast"], "patient-1", on_result=_on_result)

    assert callback_order == ["fast", "slow"]
    assert [result.library_name for result in results] == ["slow", "fast"]
