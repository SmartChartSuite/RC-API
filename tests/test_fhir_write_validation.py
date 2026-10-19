"""Test canonical FHIR write models, round-trip payloads, and PUT invariants.

These tests exercise the hand-written router seams: canonical resource models validate
request bodies, while unknown FHIR fields and explicit values survive serialization.
FastAPI validation failures use the application's FHIR OperationOutcome handler.
"""

from copy import deepcopy
from json import loads
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from main import app
from src.models.fhir import GroupResource, LibraryResource, QuestionnaireResource, QuestionnaireResponseResource
from src.routers import group, jobpackage, library
from src.routers import response as response_router
from src.services.fhir_proxy import FHIRProxyResponse
from src.util.settings import settings

_GROUP_BODY = {
    "resourceType": "Group",
    "id": "resource-1",
    "name": None,
    "type": "person",
    "member": [
        {
            "entity": {"reference": "Patient/patient-1", "display": None},
            "extension": [{"url": "http://example.org/member", "valueBoolean": False}],
            "unknownNested": {"keep": None},
        }
    ],
    "extension": [{"url": "http://example.org/group", "valueString": "preserve"}],
    "item": [{"linkId": "unknown", "answer": [{"valueString": "yes"}]}],
}

_LIBRARY_BODY = {
    "resourceType": "Library",
    "id": "resource-1",
    "name": "RiskLib",
    "version": None,
    "status": "active",
    "content": [{"attachment": {"contentType": "text/cql", "data": None, "unknown": {"flag": True}}}],
    "extension": [{"url": "http://example.org/library", "valueString": "preserve"}],
}

_QUESTIONNAIRE_BODY = {
    "resourceType": "Questionnaire",
    "id": "resource-1",
    "name": None,
    "version": "1.0",
    "status": "active",
    "item": [{"linkId": "1", "text": None, "unknownNested": {"keep": False}, "answerOption": [{"valueString": "yes"}]}],
    "extension": [{"url": "http://example.org/questionnaire", "valueInteger": 0}],
}

_QUESTIONNAIRE_RESPONSE_BODY = {
    "resourceType": "QuestionnaireResponse",
    "id": "response-1",
    "questionnaire": "Questionnaire/ExampleRegistry",
    "status": None,
    "subject": {"reference": "Patient/patient-1", "display": None, "extension": [{"url": "http://example.org/subject", "valueBoolean": False}]},
    "item": [{"linkId": "1", "answer": [{"valueString": "Yes", "unknown": {"keep": None}}]}],
    "extension": [{"url": "http://example.org/response", "valueString": "preserve"}],
}

_PROXY_WRITE_CASES = (
    pytest.param(group, group.create_group, GroupResource, "Group", "post", _GROUP_BODY, id="group-post"),
    pytest.param(group, group.update_group, GroupResource, "Group", "put", _GROUP_BODY, id="group-put"),
    pytest.param(library, library.create_library, LibraryResource, "Library", "post", _LIBRARY_BODY, id="library-post"),
    pytest.param(library, library.update_library, LibraryResource, "Library", "put", _LIBRARY_BODY, id="library-put"),
    pytest.param(jobpackage, jobpackage.create_job_package, QuestionnaireResource, "Questionnaire", "post", _QUESTIONNAIRE_BODY, id="jobpackage-post"),
    pytest.param(jobpackage, jobpackage.update_job_package, QuestionnaireResource, "Questionnaire", "put", _QUESTIONNAIRE_BODY, id="jobpackage-put"),
)

_VALIDATION_CASES = (
    pytest.param(group, "/group", "post", _GROUP_BODY, "fhir_post_with_status", id="group-post"),
    pytest.param(group, "/group/resource-1", "put", _GROUP_BODY, "fhir_put_with_status", id="group-put"),
    pytest.param(library, "/library", "post", _LIBRARY_BODY, "fhir_post_with_status", id="library-post"),
    pytest.param(library, "/library/resource-1", "put", _LIBRARY_BODY, "fhir_put_with_status", id="library-put"),
    pytest.param(jobpackage, "/jobpackage", "post", _QUESTIONNAIRE_BODY, "fhir_post_with_status", id="jobpackage-post"),
    pytest.param(jobpackage, "/jobpackage/resource-1", "put", _QUESTIONNAIRE_BODY, "fhir_put_with_status", id="jobpackage-put"),
    pytest.param(response_router, "/response/response-1", "put", _QUESTIONNAIRE_RESPONSE_BODY, "get_response", id="response-put"),
)

_PUT_MODEL_CASES = (
    pytest.param(group, group.update_group, GroupResource, "Group", _GROUP_BODY, "fhir_put_with_status", id="group"),
    pytest.param(library, library.update_library, LibraryResource, "Library", _LIBRARY_BODY, "fhir_put_with_status", id="library"),
    pytest.param(jobpackage, jobpackage.update_job_package, QuestionnaireResource, "Questionnaire", _QUESTIONNAIRE_BODY, "fhir_put_with_status", id="jobpackage"),
    pytest.param(response_router, response_router.update_response_record, QuestionnaireResponseResource, "QuestionnaireResponse", _QUESTIONNAIRE_RESPONSE_BODY, "get_response", id="response"),
)


def _assert_operation_outcome(response: Any, code: str) -> None:
    assert response.status_code == 400
    payload = response.json() if hasattr(response, "json") else loads(response.body)
    assert payload["resourceType"] == "OperationOutcome"
    assert payload["issue"][0]["code"] == code


@pytest.mark.parametrize("router_module, handler, model, resource_type, method, raw_body", _PROXY_WRITE_CASES)
async def test_proxy_write_models_round_trip_unknown_fields_and_nulls(
    monkeypatch,
    router_module: Any,
    handler: Any,
    model: Any,
    resource_type: str,
    method: str,
    raw_body: dict[str, Any],
) -> None:
    original = deepcopy(raw_body)
    body = model.model_validate(original)
    captured: dict[str, Any] = {}

    async def fake_proxy(proxy_resource_type: str, *args: Any) -> FHIRProxyResponse:
        payload = args[-1]
        captured["resource_type"] = proxy_resource_type
        captured["payload"] = payload
        return FHIRProxyResponse(data=payload, status_code=200)

    monkeypatch.setattr(settings, "config_errors", {})
    monkeypatch.setattr(router_module, f"fhir_{method}_with_status", fake_proxy)

    if method == "post":
        result = await handler(body=body, claims={})
    elif router_module is response_router:
        result = await handler(response_id="response-1", body=body, claims={})
    else:
        result = await handler(resource_id="resource-1", body=body, claims={})

    assert not isinstance(result, JSONResponse)
    assert captured == {"resource_type": resource_type, "payload": original}


@pytest.mark.parametrize("router_module, path, method, raw_body, seam", _VALIDATION_CASES)
def test_wrong_resource_type_is_operation_outcome_and_not_forwarded(
    monkeypatch,
    router_module: Any,
    path: str,
    method: str,
    raw_body: dict[str, Any],
    seam: str,
) -> None:
    monkeypatch.setattr(settings, "oauth2_enabled", False)
    monkeypatch.setattr(settings, "config_errors", {})
    forwarded = False

    async def fail_proxy(*args: Any, **kwargs: Any) -> FHIRProxyResponse:
        del args, kwargs
        nonlocal forwarded
        forwarded = True
        raise AssertionError("invalid body was forwarded")

    def fail_persistence(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        nonlocal forwarded
        forwarded = True
        raise AssertionError("invalid body reached persistence")

    monkeypatch.setattr(router_module, seam, fail_proxy if seam.startswith("fhir_") else fail_persistence)
    invalid = deepcopy(raw_body)
    invalid["resourceType"] = "Patient"

    response = TestClient(app).request(method, path, json=invalid)

    _assert_operation_outcome(response, "structure")
    assert not forwarded


@pytest.mark.parametrize(
    "router_module, path, method, raw_body, field, value, seam",
    (
        pytest.param(group, "/group", "post", _GROUP_BODY, "name", {"not": "a string"}, "fhir_post_with_status", id="group"),
        pytest.param(library, "/library", "post", _LIBRARY_BODY, "version", {"not": "a string"}, "fhir_post_with_status", id="library"),
        pytest.param(jobpackage, "/jobpackage", "post", _QUESTIONNAIRE_BODY, "name", ["not", "a string"], "fhir_post_with_status", id="jobpackage"),
        pytest.param(response_router, "/response/response-1", "put", _QUESTIONNAIRE_RESPONSE_BODY, "subject", ["not", "an object"], "get_response", id="response"),
        pytest.param(response_router, "/response/response-1", "put", _QUESTIONNAIRE_RESPONSE_BODY, "author", {"identifier": "not-an-identifier"}, "get_response", id="response-author"),
    ),
)
def test_wrongly_typed_declared_field_is_operation_outcome_and_not_forwarded(
    monkeypatch,
    router_module: Any,
    path: str,
    method: str,
    raw_body: dict[str, Any],
    field: str,
    value: Any,
    seam: str,
) -> None:
    monkeypatch.setattr(settings, "oauth2_enabled", False)
    monkeypatch.setattr(settings, "config_errors", {})
    forwarded = False

    async def fail_proxy(*args: Any, **kwargs: Any) -> FHIRProxyResponse:
        del args, kwargs
        nonlocal forwarded
        forwarded = True
        raise AssertionError("invalid body was forwarded")

    def fail_persistence(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        nonlocal forwarded
        forwarded = True
        raise AssertionError("invalid body reached persistence")

    monkeypatch.setattr(router_module, seam, fail_proxy if seam.startswith("fhir_") else fail_persistence)
    invalid = deepcopy(raw_body)
    invalid[field] = value

    response = TestClient(app).request(method, path, json=invalid)

    _assert_operation_outcome(response, "structure")
    assert not forwarded


@pytest.mark.parametrize("router_module, handler, model, resource_type, raw_body, seam", _PUT_MODEL_CASES)
async def test_put_id_mismatch_is_invariant_and_not_forwarded(
    monkeypatch,
    router_module: Any,
    handler: Any,
    model: Any,
    resource_type: str,
    raw_body: dict[str, Any],
    seam: str,
) -> None:
    del resource_type
    monkeypatch.setattr(settings, "config_errors", {})
    forwarded = False
    body_data = deepcopy(raw_body)
    body_data["id"] = "other-id"
    body = model.model_validate(body_data)

    async def fail_proxy(*args: Any, **kwargs: Any) -> FHIRProxyResponse:
        del args, kwargs
        nonlocal forwarded
        forwarded = True
        raise AssertionError("mismatched id was forwarded")

    def fail_persistence(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        nonlocal forwarded
        forwarded = True
        raise AssertionError("mismatched id reached persistence")

    monkeypatch.setattr(router_module, seam, fail_proxy if seam.startswith("fhir_") else fail_persistence)
    if router_module is response_router:
        result = await handler(response_id="response-1", body=body, claims={})
    else:
        result = await handler(resource_id="resource-1", body=body, claims={})

    assert isinstance(result, JSONResponse)
    _assert_operation_outcome(result, "invariant")
    assert not forwarded


@pytest.mark.parametrize("router_module, handler, model, resource_type, raw_body, seam", _PUT_MODEL_CASES)
@pytest.mark.parametrize("include_id", [True, False], ids=["matching-id", "absent-id"])
async def test_put_matching_or_absent_id_is_forwarded_or_persisted(
    monkeypatch,
    router_module: Any,
    handler: Any,
    model: Any,
    resource_type: str,
    raw_body: dict[str, Any],
    seam: str,
    include_id: bool,
) -> None:
    monkeypatch.setattr(settings, "config_errors", {})
    body_data = deepcopy(raw_body)
    expected_id = "response-1" if router_module is response_router else "resource-1"
    if include_id:
        body_data["id"] = expected_id
    else:
        body_data.pop("id", None)
    body = model.model_validate(body_data)
    captured: dict[str, Any] = {}

    async def fake_proxy(proxy_resource_type: str, *args: Any) -> FHIRProxyResponse:
        payload = args[-1]
        captured["resource_type"] = proxy_resource_type
        captured["payload"] = payload
        return FHIRProxyResponse(data=payload, status_code=200)

    def fake_get_response(response_id: str) -> SimpleNamespace:
        captured["response_id"] = response_id
        return SimpleNamespace(patient_id="patient-1", job_package="Questionnaire/ExampleRegistry")

    def fake_update_response(*args: Any) -> bool:
        captured["persisted"] = args[1]
        return True

    if seam.startswith("fhir_"):
        monkeypatch.setattr(router_module, seam, fake_proxy)
    else:
        monkeypatch.setattr(router_module, "get_response", fake_get_response)
        monkeypatch.setattr(router_module, "update_response_body", fake_update_response)

    if router_module is response_router:
        result = await handler(response_id="response-1", body=body, claims={})
    else:
        result = await handler(resource_id="resource-1", body=body, claims={})

    assert not isinstance(result, JSONResponse)
    assert captured
    if seam.startswith("fhir_"):
        assert captured["resource_type"] == resource_type
        assert captured["payload"] == body_data
    else:
        assert captured["persisted"]["id"] == expected_id


async def test_response_post_preserves_explicit_nulls_and_unknown_fields(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(response_router.uuid, "uuid4", lambda: "response-new")
    monkeypatch.setattr(response_router, "get_batch_job", lambda batch_id: SimpleNamespace(batch_id=batch_id, patient_id="patient-1"))

    def fake_create_response(*args: Any) -> bool:
        captured["body"] = args[5]
        return True

    monkeypatch.setattr(response_router, "create_response", fake_create_response)
    raw_body = deepcopy(_QUESTIONNAIRE_RESPONSE_BODY)
    raw_body.pop("id")
    body = QuestionnaireResponseResource.model_validate(raw_body)

    result = await response_router.create_response_record(body, batch_job_id="batch-1", response=Response(), claims={})

    assert isinstance(result, QuestionnaireResponseResource)
    persisted = captured["body"]
    assert persisted["status"] is None
    assert persisted["item"] == raw_body["item"]
    assert persisted["extension"] == raw_body["extension"]
    assert persisted["subject"] == raw_body["subject"]
    assert persisted["questionnaire"] == raw_body["questionnaire"]


async def test_response_put_keeps_subject_questionnaire_immutability_and_unknown_fields(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    existing = SimpleNamespace(patient_id="patient-1", job_package="Questionnaire/ExampleRegistry")
    monkeypatch.setattr(response_router, "get_response", lambda response_id: existing)

    def fake_update_response(*args: Any) -> bool:
        captured["body"] = args[1]
        return True

    monkeypatch.setattr(response_router, "update_response_body", fake_update_response)
    body = QuestionnaireResponseResource.model_validate(deepcopy(_QUESTIONNAIRE_RESPONSE_BODY))

    result = await response_router.update_response_record("response-1", body, claims={})

    assert isinstance(result, QuestionnaireResponseResource)
    persisted = captured["body"]
    assert result.author is not None
    assert result.author.to_fhir_json() == persisted["author"]
    assert persisted["subject"] == {"reference": "Patient/patient-1"}
    assert persisted["questionnaire"] == "Questionnaire/ExampleRegistry"
    assert persisted["item"] == _QUESTIONNAIRE_RESPONSE_BODY["item"]
    assert persisted["extension"] == _QUESTIONNAIRE_RESPONSE_BODY["extension"]
