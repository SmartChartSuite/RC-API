"""Tests CQL Parameters parsing and concurrent library evaluation in `src.services.cql_executor`.

The tests patch the evaluator and synchronization behavior, including shared limits and completion-order
callbacks; FHIR transport behavior belongs in `test_fhir_proxy.py`."""

import asyncio

from src.services import cql_executor
from src.services.cql_executor import _parse_parameters_response
from src.util.settings import settings


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
    assert result.results == {"simple": [{"value": "value-1", "type": "String"}, {"value": True, "type": "Boolean"}]}


def test_parse_parameters_response_extracts_generic_values_and_parts():
    result = _parse_parameters_response(
        "ExampleLibrary",
        "patient-123",
        {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "integer", "valueInteger": 7},
                {"name": "decimal", "valueDecimal": 1.25},
                {"name": "boolean", "valueBoolean": True},
                {"name": "code", "valueCode": "final"},
                {"name": "quantity", "valueQuantity": {"value": 12.5, "unit": "mg"}},
                {"name": "concept", "valueCodeableConcept": {"coding": [{"code": "abc"}]}},
                {"name": "null_result"},
                {"name": "repeat", "valueString": "first"},
                {"name": "repeat", "valueInteger": 2},
                {
                    "name": "parted",
                    "part": [
                        {"name": "amount", "valueDecimal": 3.5},
                        {"name": "ignored_concept", "valueString": "ignored"},
                        {
                            "name": "nested",
                            "part": [
                                {"name": "flag", "valueBoolean": False},
                                {"name": "nested_concept", "valueCode": "ignored"},
                            ],
                        },
                    ],
                },
            ],
        },
    )

    assert result.error is None
    assert result.results["integer"] == [{"value": 7, "type": "Integer"}]
    assert result.results["decimal"] == [{"value": 1.25, "type": "Decimal"}]
    assert result.results["boolean"] == [{"value": True, "type": "Boolean"}]
    assert result.results["code"] == [{"value": "final", "type": "Code"}]
    assert result.results["quantity"] == [{"value": {"value": 12.5, "unit": "mg"}, "type": "Quantity"}]
    assert result.results["concept"] == [{"value": {"coding": [{"code": "abc"}]}, "type": "CodeableConcept"}]
    assert "null_result" not in result.results
    assert result.results["repeat"] == [{"value": "first", "type": "String"}, {"value": 2, "type": "Integer"}]
    assert result.results["parted"] == [
        {
            "value": {
                "amount": [{"value": 3.5, "type": "Decimal"}],
                "nested": [{"value": {"flag": [{"value": False, "type": "Boolean"}]}, "type": "part"}],
            },
            "type": "part",
        }
    ]


def test_parse_parameters_response_returns_error_for_nested_operation_outcome():
    result = _parse_parameters_response(
        "ExampleLibrary",
        "patient-123",
        {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "outer",
                    "part": [
                        {
                            "name": "inner",
                            "part": [
                                {
                                    "name": "failure",
                                    "resource": {
                                        "resourceType": "OperationOutcome",
                                        "issue": [{"code": "exception", "diagnostics": "nested failure"}],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )

    assert result.error == "nested failure"
    assert result.results == {}


async def test_run_cql_libraries_reports_each_result_as_it_finishes(monkeypatch):
    callback_order: list[str] = []

    async def _fake_evaluate_library(client, library_name, patient_id):
        del client
        if library_name == "slow":
            await asyncio.sleep(0.01)
        return cql_executor.CqlResult(library_name=library_name, patient_id=patient_id)

    async def _on_result(result):
        callback_order.append(result.library_name)

    monkeypatch.setattr(cql_executor, "get_http_client", lambda _profile: object())
    monkeypatch.setattr(cql_executor, "_evaluate_library", _fake_evaluate_library)

    results = await cql_executor.run_cql_libraries(["slow", "fast"], "patient-1", on_result=_on_result)

    assert callback_order == ["fast", "slow"]
    assert [result.library_name for result in results] == ["slow", "fast"]


async def test_run_cql_libraries_shares_limit_across_calls_and_preserves_order(monkeypatch):
    monkeypatch.setattr(settings, "cql_max_concurrency", 2)
    cql_executor._cql_semaphores.clear()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    async def _fake_evaluate_library(client, library_name, patient_id):
        nonlocal in_flight, max_in_flight
        del client
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        if patient_id == "patient-a":
            first_started.set()
        else:
            second_started.set()
        await release.wait()
        in_flight -= 1
        return cql_executor.CqlResult(library_name=library_name, patient_id=patient_id)

    monkeypatch.setattr(cql_executor, "_evaluate_library", _fake_evaluate_library)
    first_task = asyncio.create_task(cql_executor.run_cql_libraries(["first"], "patient-a"))
    await first_started.wait()
    second_libraries = [f"library-{index}" for index in range(1, 6)]
    second_task = asyncio.create_task(cql_executor.run_cql_libraries(second_libraries, "patient-b"))
    await second_started.wait()

    assert max_in_flight == 2
    release.set()
    first_results, second_results = await asyncio.gather(first_task, second_task)

    assert [result.library_name for result in first_results] == ["first"]
    assert [result.library_name for result in second_results] == second_libraries
