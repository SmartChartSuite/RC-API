import json
import threading

import pytest

from src.models.prompt import Prompt, PromptMetadata
from src.services import job_orchestrator
from src.services.cql_executor import CqlResult
from src.services.fhir_context import DocumentFetchResult
from src.services.job_state import LogicalJob
from src.services.llm_executor import LlmDocumentResult, LlmResult
from src.util.settings import settings


@pytest.fixture(autouse=True)
def _stub_document_state(monkeypatch):
    monkeypatch.setattr(job_orchestrator, "upsert_batch_job_documents", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "get_batch_job_documents", lambda *args, **kwargs: [])


def test_extract_job_lists_reads_both_extension_types():
    questionnaire = {
        "extension": [
            {
                "url": job_orchestrator._CQL_JOB_LIST_URL,
                "extension": [{"valueString": "LibOne"}, {"valueString": "LibTwo"}],
            },
            {
                "url": job_orchestrator._LLM_JOB_LIST_URL,
                "extension": [{"valueString": "prompts/a"}, {"valueString": "prompts/b"}],
            },
        ]
    }

    cql_names, prompt_paths = job_orchestrator._extract_job_lists(questionnaire)

    assert cql_names == ["LibOne", "LibTwo"]
    assert prompt_paths == ["prompts/a", "prompts/b"]


def test_build_tuple_observation_returns_supporting_condition_resource():
    pipe_value = "2026-05-22T12:00:00|http://loinc.org|1234-5|Example|positive"
    obs, supporting = job_orchestrator._build_tuple_observation(
        pipe_value,
        "1.1",
        "Question text",
        "patient-123",
        "RegistryForm",
        "Condition_History",
        "batch-1",
    )

    assert supporting is not None
    assert obs["id"] == job_orchestrator._stable_id("batch-1", "cql", "Condition_History", "1.1", 0, "String", pipe_value, "observation")
    assert obs["identifier"] == [{"system": settings.deploy_url, "value": f"Observation/{obs['id']}"}]
    assert obs["focus"] == [{"reference": f"Condition/{supporting['id']}"}]
    assert supporting["id"] == job_orchestrator._stable_id("batch-1", "cql", "Condition_History", "1.1", 0, "String", pipe_value, "supporting", "Condition")
    assert supporting["resourceType"] == "Condition"
    assert supporting["code"]["coding"][0]["code"] == "1234-5"


def test_build_llm_observations_deduplicates_same_document_and_text():
    questionnaire = {
        "item": [
            {
                "item": [
                    {
                        "linkId": "1.1",
                        "text": "Question text",
                        "extension": [{"url": job_orchestrator._UNSTRUCTURED_TASK_URL, "valueString": "prompts/a"}],
                    }
                ]
            }
        ]
    }
    llm_results = [
        LlmResult(
            prompt_path="prompts/a",
            document_results=[
                LlmDocumentResult(doc_id="doc-1", doc_type="Visit Note", doc_date="2026-05-22", response="same answer"),
                LlmDocumentResult(doc_id="doc-1", doc_type="Visit Note", doc_date="2026-05-22", response="same answer"),
            ],
        )
    ]

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    assert entries[0]["resource"]["valueString"] == "same answer"
    assert entries[0]["resource"]["focus"] == [{"reference": "DocumentReference/doc-1"}]


def test_build_llm_observations_maps_json_response_to_components():
    questionnaire = {
        "item": [
            {
                "item": [
                    {
                        "linkId": "1.1",
                        "text": "Question text",
                        "extension": [{"url": job_orchestrator._UNSTRUCTURED_TASK_URL, "valueString": "prompts/a"}],
                    }
                ]
            }
        ]
    }
    llm_results = [
        LlmResult(
            prompt_path="prompts/a",
            document_results=[
                LlmDocumentResult(
                    doc_id="doc-1",
                    doc_type="Visit Note",
                    doc_date="2026-05-22",
                    response='{"nlp-answer-type":"SectionFinderTask","section-header":"head_review [5.39.115.133.89]","section-text":"closed fontanelles"}',
                )
            ],
        )
    ]

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    resource = entries[0]["resource"]
    assert "valueString" not in resource
    assert resource["focus"] == [{"reference": "DocumentReference/doc-1"}]
    assert resource["component"] == [
        {
            "code": {
                "coding": [
                    {
                        "system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label",
                        "code": "nlp-answer-type",
                        "display": "Nlp Answer Type",
                    }
                ]
            },
            "valueString": "SectionFinderTask",
        },
        {
            "code": {
                "coding": [
                    {
                        "system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label",
                        "code": "section-header",
                        "display": "Section Header",
                    }
                ]
            },
            "valueString": "head_review [5.39.115.133.89]",
        },
        {
            "code": {
                "coding": [
                    {
                        "system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label",
                        "code": "section-text",
                        "display": "Section Text",
                    }
                ]
            },
            "valueString": "closed fontanelles",
        },
    ]


def test_build_llm_observations_maps_fenced_json_response_to_components():
    questionnaire = {
        "item": [
            {
                "item": [
                    {
                        "linkId": "1.1",
                        "text": "Question text",
                        "extension": [{"url": job_orchestrator._UNSTRUCTURED_TASK_URL, "valueString": "prompts/a"}],
                    }
                ]
            }
        ]
    }
    llm_results = [
        LlmResult(
            prompt_path="prompts/a",
            document_results=[
                LlmDocumentResult(
                    doc_id="doc-1",
                    doc_type="Visit Note",
                    doc_date="2026-05-22",
                    response='```json\n{\n  "resultValue": "Normal",\n  "evidenceText": "Chest: Lungs clear to auscultation, respirations unlabored.",\n  "reasoning": "The physical exam explicitly documents that the lungs are clear to auscultation and respirations are unlabored, indicating a normal pulmonary and respiratory assessment for this encounter."\n}\n```',
                )
            ],
        )
    ]

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    resource = entries[0]["resource"]
    assert "valueString" not in resource
    assert resource["component"] == [
        {
            "code": {
                "coding": [
                    {
                        "system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label",
                        "code": "resultValue",
                        "display": "Result Value",
                    }
                ]
            },
            "valueString": "Normal",
        },
        {
            "code": {
                "coding": [
                    {
                        "system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label",
                        "code": "evidenceText",
                        "display": "Evidence Text",
                    }
                ]
            },
            "valueString": "Chest: Lungs clear to auscultation, respirations unlabored.",
        },
        {
            "code": {
                "coding": [
                    {
                        "system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label",
                        "code": "reasoning",
                        "display": "Reasoning",
                    }
                ]
            },
            "valueString": "The physical exam explicitly documents that the lungs are clear to auscultation and respirations are unlabored, indicating a normal pulmonary and respiratory assessment for this encounter.",
        },
    ]


@pytest.mark.parametrize(
    ("document_result", "expected_status"),
    [
        (LlmDocumentResult(doc_id="doc-1", doc_type="Note", doc_date="2026-05-22", response="answer"), "complete"),
        (LlmDocumentResult(doc_id="doc-1", doc_type="Note", doc_date="2026-05-22", response="", error="provider failure"), "error"),
        (LlmDocumentResult(doc_id="doc-1", doc_type="Note", doc_date="2026-05-22", response=""), "skipped"),
    ],
)
def test_summarize_llm_result_omits_document_text(document_result, expected_status):
    status, payload = job_orchestrator._summarize_llm_result(LlmResult(prompt_path="prompts/a", document_results=[document_result]))

    assert status == expected_status
    assert all("doc_text" not in document for document in payload["document_results"])


def test_llm_result_restore_ignores_legacy_document_text():
    restored = job_orchestrator._llm_result_from_payload(
        "prompts/a",
        {
            "document_results": [
                {
                    "doc_id": "doc-1",
                    "doc_type": "Note",
                    "doc_date": "2026-05-22",
                    "response": "answer",
                    "error": None,
                    "doc_text": "legacy source text",
                }
            ]
        },
    )

    assert restored is not None
    assert restored.document_results[0].response == "answer"
    assert not hasattr(restored.document_results[0], "doc_text")


def test_build_cql_observations_uses_matching_structured_task():
    questionnaire = {
        "item": [
            {
                "item": [
                    {
                        "linkId": "1.1",
                        "text": "Question text",
                        "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
                    }
                ]
            }
        ]
    }
    cql_results = [CqlResult(library_name="LibOne", patient_id="patient-123", results={"TaskA": [{"value": "positive"}]})]

    entries = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    assert entries[0]["resource"]["valueString"] == "positive"


@pytest.mark.parametrize(
    ("value_type", "raw_value", "expected"),
    [
        ("String", "positive", {"valueString": "positive"}),
        ("Boolean", True, {"valueBoolean": True}),
        ("Integer", 4, {"valueInteger": 4}),
        ("DateTime", "2026-05-22T12:00:00Z", {"valueDateTime": "2026-05-22T12:00:00Z"}),
        ("Time", "12:00:00", {"valueTime": "12:00:00"}),
        ("Period", {"start": "2026-05-22"}, {"valuePeriod": {"start": "2026-05-22"}}),
        ("Quantity", {"value": 12.5, "unit": "mg"}, {"valueQuantity": {"value": 12.5, "unit": "mg"}}),
        ("CodeableConcept", {"coding": [{"code": "abc"}]}, {"valueCodeableConcept": {"coding": [{"code": "abc"}]}}),
        ("Range", {"low": {"value": 1}, "high": {"value": 2}}, {"valueRange": {"low": {"value": 1}, "high": {"value": 2}}}),
        ("Ratio", {"numerator": {"value": 1}, "denominator": {"value": 2}}, {"valueRatio": {"numerator": {"value": 1}, "denominator": {"value": 2}}}),
        ("SampledData", {"origin": {"value": 0}, "data": "1 2"}, {"valueSampledData": {"origin": {"value": 0}, "data": "1 2"}}),
        ("Decimal", 1.25, {"valueQuantity": {"value": 1.25}}),
        ("Coding", {"system": "http://loinc.org", "code": "1234-5"}, {"valueCodeableConcept": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]}}),
        ("Code", "final", {"valueCodeableConcept": {"coding": [{"code": "final"}]}}),
        ("Date", "2026-05-22", {"valueDateTime": "2026-05-22"}),
        ("Reference", {"reference": "Patient/patient-123"}, {"focus": [{"reference": "Patient/patient-123"}]}),
    ],
)
def test_build_cql_observations_maps_cql_value_types(value_type, raw_value, expected):
    questionnaire = {
        "item": [
            {
                "linkId": "1.1",
                "text": "Question text",
                "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
            }
        ]
    }
    cql_results = [CqlResult(library_name="LibOne", patient_id="patient-123", results={"TaskA": [{"value": raw_value, "type": value_type}]})]

    entries = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    resource = entries[0]["resource"]
    for key, expected_value in expected.items():
        assert resource[key] == expected_value


@pytest.mark.parametrize(
    ("value_type", "raw_value"),
    [
        ("part", {"component": [{"value": 1, "type": "Integer"}]}),
        ("Attachment", {"contentType": "text/plain", "data": "abc"}),
        ("Unknown", "not-a-string-observation"),
    ],
)
def test_build_cql_observations_falls_back_to_compact_json(value_type, raw_value):
    questionnaire = {
        "item": [
            {
                "linkId": "1.1",
                "text": "Question text",
                "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
            }
        ]
    }
    cql_results = [CqlResult(library_name="LibOne", patient_id="patient-123", results={"TaskA": [{"value": raw_value, "type": value_type}]})]

    entries = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    assert entries[0]["resource"]["valueString"] == json.dumps(raw_value, separators=(",", ":"), sort_keys=True)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [(True, {"valueBoolean": True}), ("positive", {"valueString": "positive"})],
)
def test_build_cql_observations_infers_legacy_value_types(raw_value, expected):
    questionnaire = {
        "item": [
            {
                "linkId": "1.1",
                "text": "Question text",
                "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
            }
        ]
    }
    cql_results = [CqlResult(library_name="LibOne", patient_id="patient-123", results={"TaskA": [{"value": raw_value}]})]

    entries = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    for key, expected_value in expected.items():
        assert entries[0]["resource"][key] == expected_value


def test_generated_result_ids_are_stable_and_distinct_per_batch():
    questionnaire = {
        "item": [
            {
                "linkId": "cql-question",
                "text": "CQL question",
                "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
            },
            {
                "linkId": "llm-question",
                "text": "LLM question",
                "extension": [{"url": job_orchestrator._UNSTRUCTURED_TASK_URL, "valueString": "prompts/a"}],
            },
        ]
    }
    cql_results = [
        CqlResult(
            library_name="LibOne",
            patient_id="patient-123",
            results={"TaskA": [{"value": "first"}, {"value": "second"}]},
        )
    ]
    llm_results = [
        LlmResult(
            prompt_path="prompts/a",
            document_results=[
                LlmDocumentResult(doc_id="doc-1", doc_type="Visit Note", doc_date="2026-05-22", response="finding one"),
                LlmDocumentResult(doc_id="doc-1", doc_type="Visit Note", doc_date="2026-05-22", response="finding two"),
            ],
        )
    ]

    def _build(batch_id: str) -> list[dict]:
        return job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", batch_id) + job_orchestrator._build_llm_observations(
            llm_results, questionnaire, "RegistryForm", "patient-123", batch_id
        )

    first = _build("batch-1")
    repeat = _build("batch-1")
    other_batch = _build("batch-2")

    assert first == repeat
    assert [entry["fullUrl"] for entry in first] == [entry["fullUrl"] for entry in repeat]
    assert {entry["resource"]["id"] for entry in first}.isdisjoint({entry["resource"]["id"] for entry in other_batch})
    assert len({entry["resource"]["id"] for entry in first}) == 4
    assert len([entry for entry in first if entry["resource"]["id"]]) == 4
    for entry in first:
        resource = entry["resource"]
        assert entry["fullUrl"] == f"{resource['resourceType']}/{resource['id']}"
        assert resource["identifier"] == [{"system": settings.deploy_url, "value": f"Observation/{resource['id']}"}]


def test_cql_resource_without_id_gets_stable_supporting_resource():
    questionnaire = {
        "item": [
            {
                "linkId": "1.1",
                "text": "Question text",
                "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
            }
        ]
    }
    cql_results = [
        CqlResult(
            library_name="LibOne",
            patient_id="patient-123",
            results={"TaskA": [{"resourceType": "Condition", "code": {"text": "stable"}}]},
        )
    ]

    first = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", "batch-1")
    repeat = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert first == repeat
    assert len(first) == 2
    observation, supporting = first
    supporting_resource = supporting["resource"]
    assert supporting_resource["id"]
    assert supporting_resource["id"] == supporting["resource"]["id"]
    assert supporting["fullUrl"] == f"Condition/{supporting_resource['id']}"
    assert observation["resource"]["focus"] == [{"reference": supporting["fullUrl"]}]


def test_build_result_bundle_includes_status_and_patient():

    bundle = job_orchestrator._build_result_bundle(
        {"resourceType": "Patient", "id": "patient-123"},
        [{"fullUrl": "Observation/1", "resource": {"resourceType": "Observation", "id": "1"}}],
        [{"fullUrl": "Observation/2", "resource": {"resourceType": "Observation", "id": "2"}}],
        "complete",
        batch_id="batch-1",
    )

    assert bundle["resourceType"] == "Bundle"
    assert bundle["id"] == job_orchestrator._stable_id("batch-1", "bundle")
    assert bundle["total"] == 4
    assert bundle["entry"][0]["resource"]["id"] == "status-observation"


def test_document_bundle_storage_strips_content_and_retry_order_is_deterministic():
    fetched = [
        {"id": "doc-b", "type": "Note", "date": "2026-05-24", "text": "B"},
        {"id": "doc-c", "type": "Note", "date": "2026-05-23", "text": "C"},
    ]
    stored_only = [
        {"id": "doc-z", "type": "Note", "date": "2026-05-20", "text": "Z"},
        {"id": "doc-a", "type": "Note", "date": "2026-05-22", "text": "A"},
        {"id": "doc-b", "type": "Old Note", "date": "2026-05-01", "text": "old B"},
    ]

    document_entries = job_orchestrator._build_document_entries(fetched, "patient-1", "batch-1")
    stored_bundle = job_orchestrator._build_result_bundle(None, [], document_entries, "preliminary", batch_id="batch-1")
    ordered = job_orchestrator._ordered_document_set(fetched, stored_only)

    assert [document["id"] for document in ordered] == ["doc-b", "doc-c", "doc-z", "doc-a"]
    assert document_entries[0]["resource"]["content"][0]["attachment"] == {"contentType": "text/plain", "data": "Qg=="}
    assert all("content" not in entry["resource"] for entry in stored_bundle["entry"])


async def test_run_batch_job_persists_completed_task_results_while_running(monkeypatch):
    monkeypatch.setattr(settings, "batch_job_checkpoint_interval_seconds", 0.0)
    partial_bundles: list[dict] = []
    status_updates: list[tuple[str, dict | None]] = []

    async def _fake_fhir_get(resource_type, resource_id):
        assert resource_type == "Questionnaire"
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._CQL_JOB_LIST_URL,
                    "extension": [{"valueString": "LibOne"}],
                }
            ],
            "item": [
                {
                    "item": [
                        {
                            "linkId": "1.1",
                            "text": "Question text",
                            "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibOne.TaskA"}],
                        }
                    ]
                }
            ],
        }

    async def _fake_run_cql_libraries(library_names, patient_id, on_result=None):
        assert library_names == ["LibOne"]
        result = CqlResult(library_name="LibOne", patient_id=patient_id, results={"TaskA": [{"value": "positive"}]})
        if on_result:
            await on_result(result)
        return [result]

    async def _fake_fetch_patient_resource(patient_id):
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob("job-123", "running", None))
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        job_orchestrator,
        "update_batch_job_result",
        lambda batch_id, bundle, **kwargs: not partial_bundles.append(bundle),
    )
    monkeypatch.setattr(
        job_orchestrator,
        "update_batch_job_status",
        lambda batch_id, status, bundle=None, **kwargs: not status_updates.append((status, bundle)),
    )
    monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _fake_run_cql_libraries)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)

    await job_orchestrator.run_batch_job(
        "batch-1",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
        worker_id="worker-1",
        attempt_count=1,
    )

    assert len(partial_bundles) == 2
    assert partial_bundles[0]["entry"][0]["resource"]["status"] == "preliminary"
    completed_snapshot = partial_bundles[1]
    completed_observations = [entry["resource"] for entry in completed_snapshot["entry"] if entry["resource"].get("code", {}).get("coding", [{}])[0].get("code") == "1.1"]
    assert len(completed_observations) == 1
    assert completed_observations[0]["valueString"] == "positive"

    final_status, final_bundle = status_updates[-1]
    assert final_status == "complete"
    assert final_bundle is not None
    assert final_bundle["id"] == completed_snapshot["id"]
    assert final_bundle["entry"][0]["resource"]["status"] == "complete"
    assert any(entry["resource"].get("resourceType") == "Patient" for entry in final_bundle["entry"])


@pytest.mark.parametrize(
    ("checkpoint_interval", "checkpoint_every_tasks", "expected_partial_writes"),
    [(3600.0, 2, 3), (0.0, 10, 6)],
)
async def test_run_batch_job_throttles_partial_bundle_checkpoints(monkeypatch, checkpoint_interval, checkpoint_every_tasks, expected_partial_writes):
    monkeypatch.setattr(settings, "batch_job_checkpoint_interval_seconds", checkpoint_interval)
    monkeypatch.setattr(settings, "batch_job_checkpoint_every_tasks", checkpoint_every_tasks)
    monkeypatch.setattr(settings, "use_llm", False)
    library_names = [f"Lib{index}" for index in range(5)]
    partial_bundles: list[dict] = []
    child_updates: list[tuple[tuple, dict]] = []
    final_updates: list[tuple[str, dict | None]] = []

    async def _fake_fhir_get(resource_type, resource_id):
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._CQL_JOB_LIST_URL,
                    "extension": [{"valueString": name} for name in library_names],
                }
            ],
            "item": [
                {
                    "linkId": f"{name}-question",
                    "text": f"Question for {name}",
                    "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": f"{name}.TaskA"}],
                }
                for name in library_names
            ],
        }

    async def _fake_run_cql_libraries(names, patient_id, on_result=None):
        results = []
        for name in names:
            result = CqlResult(library_name=name, patient_id=patient_id, results={"TaskA": [{"value": name}]})
            results.append(result)
            if on_result:
                await on_result(result)
        return results

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob(args[0], "running", None))
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: child_updates.append((args, kwargs)) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", lambda batch_id, bundle, **kwargs: partial_bundles.append(bundle) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", lambda batch_id, status, bundle=None, **kwargs: final_updates.append((status, bundle)) or True)

    async def _fake_fetch_patient_resource(patient_id):
        return None

    monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _fake_run_cql_libraries)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)

    await job_orchestrator.run_batch_job(
        "batch-throttled",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
        worker_id="worker-1",
        attempt_count=1,
    )

    assert len(partial_bundles) == expected_partial_writes
    if checkpoint_interval == 0:
        assert len(partial_bundles) == len(library_names) + 1
    else:
        assert len(partial_bundles) < len(library_names)
    assert len(child_updates) == len(library_names)
    assert final_updates[-1][0] == "complete"
    final_bundle = final_updates[-1][1]
    assert final_bundle is not None
    observations = [entry["resource"] for entry in final_bundle["entry"] if entry["resource"].get("resourceType") == "Observation" and entry["resource"].get("id") != "status-observation"]
    assert len(observations) == len(library_names)


async def test_run_batch_job_requeues_missing_llm_result_when_attempts_remain(monkeypatch):
    updates = []
    requeues = []
    status_updates = []

    async def _fake_fhir_get(resource_type, resource_id):
        assert resource_type == "Questionnaire"
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._LLM_JOB_LIST_URL,
                    "extension": [{"valueString": "prompts/a"}],
                }
            ],
            "item": [],
        }

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob("job-123", "running", None))
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", lambda *args, **kwargs: status_updates.append((args, kwargs)) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "requeue_batch_job_for_child_retry", lambda *args, **kwargs: requeues.append((args, kwargs)) or True)
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: not updates.append((args, kwargs)))

    async def _fake_load_prompts(prompt_paths):
        return [Prompt(metadata=PromptMetadata(name="a", path="prompts/a"), content="prompt")]

    async def _fake_fetch_patient_documents(patient_id):
        return DocumentFetchResult(documents=[{"id": "doc-1", "text": "note", "date": "2026-05-22"}], excluded=[])

    trace_context: dict = {}

    async def _fake_run_all_prompts(prompts, documents, on_result=None, **kwargs):
        trace_context.update(kwargs)
        return []

    async def _fake_fetch_patient_resource(patient_id):
        return None

    monkeypatch.setattr(job_orchestrator, "load_prompts", _fake_load_prompts)
    monkeypatch.setattr(job_orchestrator, "fetch_patient_documents", _fake_fetch_patient_documents)
    monkeypatch.setattr(job_orchestrator, "run_all_prompts", _fake_run_all_prompts)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)
    monkeypatch.setattr(settings, "use_llm", True)

    await job_orchestrator.run_batch_job(
        "batch-1",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
        worker_id="worker-1",
        attempt_count=1,
        max_attempts=2,
    )

    assert trace_context["trace_id"] == "batch-1"
    assert trace_context["job_package"] == "RegistryForm"
    assert updates == [
        (
            ("job-123", "error", {"message": "no llm result returned for prompt"}),
            {"batch_id": "batch-1", "worker_id": "worker-1", "attempt_count": 1},
        )
    ]
    assert status_updates == []
    assert requeues == [
        (
            ("batch-1", "worker-1", 1, "1 child task(s) failed: prompts/a", settings.batch_job_retry_delay_seconds),
            {},
        )
    ]


@pytest.mark.parametrize("successful_library, expected_status", [("LibGood", "partial"), (None, "error")])
async def test_run_batch_job_finalizes_exhausted_child_errors(monkeypatch, successful_library, expected_status):
    final_updates = []
    requeues = []

    async def _fake_fhir_get(resource_type, resource_id):
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._CQL_JOB_LIST_URL,
                    "extension": [{"valueString": name} for name in (["LibGood", "LibBad"] if successful_library else ["LibBad"])],
                }
            ],
            "item": [],
        }

    async def _fake_run_cql_libraries(library_names, patient_id, on_result=None):
        results = []
        for library_name in library_names:
            result = (
                CqlResult(library_name=library_name, patient_id=patient_id, results={"TaskA": [{"value": "positive"}]})
                if library_name == successful_library
                else CqlResult(library_name=library_name, patient_id=patient_id, error="evaluation failed")
            )
            results.append(result)
            if on_result:
                await on_result(result)
        return results

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _fake_run_cql_libraries)
    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob(args[0], "running", None))
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "requeue_batch_job_for_child_retry", lambda *args, **kwargs: requeues.append((args, kwargs)) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", lambda *args, **kwargs: final_updates.append((args, kwargs)) or True)

    async def _fake_fetch_patient_resource(patient_id):
        return None

    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)

    await job_orchestrator.run_batch_job(
        "batch-1",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
        worker_id="worker-1",
        attempt_count=1,
        max_attempts=1,
    )

    assert len(final_updates) == 1
    assert final_updates[0][0][1] == expected_status
    final_bundle = final_updates[0][0][2]
    assert final_bundle["entry"][0]["resource"]["status"] == "preliminary"
    assert final_bundle["entry"][0]["resource"]["valueCodeableConcept"]["coding"][0]["code"] == "in-progress"
    assert requeues == []


async def test_run_batch_job_records_excluded_documents_when_llm_is_skipped(monkeypatch):
    updates = []

    async def _fake_fhir_get(resource_type, resource_id):
        assert resource_type == "Questionnaire"
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._LLM_JOB_LIST_URL,
                    "extension": [{"valueString": "prompts/a"}],
                }
            ],
            "item": [],
        }

    async def _fake_load_prompts(prompt_paths):
        return [Prompt(metadata=PromptMetadata(name="a", path="prompts/a"), content="prompt")]

    async def _fake_fetch_patient_documents(patient_id):
        return DocumentFetchResult(documents=[], excluded=[{"doc_id": "doc-oversized", "reason": "exceeds_max_bytes"}])

    async def _fake_fetch_patient_resource(patient_id):
        return None

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "load_prompts", _fake_load_prompts)
    monkeypatch.setattr(job_orchestrator, "fetch_patient_documents", _fake_fetch_patient_documents)
    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob("job-123", "running", None))
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: not updates.append((args, kwargs)))
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)
    monkeypatch.setattr(settings, "use_llm", True)

    await job_orchestrator.run_batch_job(
        "batch-1",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
        worker_id="worker-1",
        attempt_count=1,
    )

    assert updates == [
        (
            (
                "job-123",
                "skipped",
                {
                    "message": "skipped — no supporting documents",
                    "excluded_documents": [{"doc_id": "doc-oversized", "reason": "exceeds_max_bytes"}],
                },
            ),
            {"batch_id": "batch-1", "worker_id": "worker-1", "attempt_count": 1},
        )
    ]


async def test_worker_retry_restores_completed_cql_without_rerunning(monkeypatch):
    partial_bundles: list[dict] = []
    final_bundles: list[dict] = []

    async def _fake_fhir_get(resource_type, resource_id):
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._CQL_JOB_LIST_URL,
                    "extension": [{"valueString": "LibOne"}],
                }
            ],
            "item": [
                {
                    "item": [
                        {
                            "linkId": "1.1",
                            "text": "Question text",
                            "extension": [
                                {
                                    "url": job_orchestrator._STRUCTURED_TASK_URL,
                                    "valueString": "LibOne.TaskA",
                                }
                            ],
                        }
                    ]
                }
            ],
        }

    async def _unexpected_cql_execution(*args, **kwargs):
        raise AssertionError("completed CQL task should not be rerun")

    async def _fake_fetch_patient_resource(patient_id):
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(
        job_orchestrator,
        "ensure_job",
        lambda *args, **kwargs: LogicalJob(
            "job-1",
            "complete",
            {"results": {"TaskA": [{"value": "positive"}]}, "error": None},
        ),
    )
    monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _unexpected_cql_execution)
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        job_orchestrator,
        "update_batch_job_result",
        lambda batch_id, bundle, **kwargs: not partial_bundles.append(bundle),
    )

    def _capture_final_bundle(batch_id, status, bundle=None, **kwargs):
        assert bundle is not None
        final_bundles.append(bundle)
        return True

    monkeypatch.setattr(
        job_orchestrator,
        "update_batch_job_status",
        _capture_final_bundle,
    )
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)

    await job_orchestrator.run_batch_job(
        "batch-1",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
        worker_id="worker-1",
        attempt_count=2,
        prior_bundle={"resourceType": "Bundle", "id": "stable-bundle"},
    )

    assert partial_bundles[0]["id"] == "stable-bundle"
    assert final_bundles[-1]["id"] == "stable-bundle"
    observations = [entry["resource"] for entry in final_bundles[-1]["entry"] if entry["resource"].get("code", {}).get("coding", [{}])[0].get("code") == "1.1"]
    assert len(observations) == 1
    assert observations[0]["valueString"] == "positive"


async def test_retry_restored_child_keeps_partial_observation_identity(monkeypatch):
    monkeypatch.setattr(settings, "batch_job_checkpoint_interval_seconds", 0.0)
    partial_bundles: list[tuple[int, dict]] = []
    final_bundles: list[dict] = []
    requeues: list[tuple] = []
    current_attempt = [1]
    cql_calls: list[list[str]] = []

    async def _fake_fhir_get(resource_type, resource_id):
        return {
            "resourceType": "Questionnaire",
            "name": "RegistryForm",
            "extension": [
                {
                    "url": job_orchestrator._CQL_JOB_LIST_URL,
                    "extension": [{"valueString": "LibDone"}, {"valueString": "LibFail"}],
                }
            ],
            "item": [
                {
                    "linkId": "done-question",
                    "text": "Done question",
                    "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibDone.TaskA"}],
                },
                {
                    "linkId": "failed-question",
                    "text": "Failed question",
                    "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": "LibFail.TaskA"}],
                },
            ],
        }

    def _ensure_job(*args, **kwargs):
        task_name = args[4]
        if current_attempt[0] == 2 and task_name == "LibDone":
            return LogicalJob("job-LibDone", "complete", {"results": {"TaskA": [{"value": "done"}]}, "error": None})
        return LogicalJob(f"job-{task_name}", "running", None)

    async def _fake_run_cql_libraries(library_names, patient_id, on_result=None):
        cql_calls.append(list(library_names))
        if len(cql_calls) == 1:
            results = [
                CqlResult(library_name="LibDone", patient_id=patient_id, results={"TaskA": [{"value": "done"}]}),
                CqlResult(library_name="LibFail", patient_id=patient_id, results={}, error="temporary"),
            ]
        else:
            results = [CqlResult(library_name="LibFail", patient_id=patient_id, results={"TaskA": [{"value": "fixed"}]})]
        for result in results:
            if on_result:
                await on_result(result)
        return results

    def _capture_partial(batch_id, bundle, **kwargs):
        partial_bundles.append((kwargs["attempt_count"], bundle))
        return True

    def _capture_final(batch_id, status, bundle=None, **kwargs):
        assert bundle is not None
        final_bundles.append(bundle)
        return True

    async def _fake_fetch_patient_resource(patient_id):
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "ensure_job", _ensure_job)
    monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _fake_run_cql_libraries)
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", _capture_partial)
    monkeypatch.setattr(job_orchestrator, "requeue_batch_job_for_child_retry", lambda *args, **kwargs: requeues.append(args) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", _capture_final)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)

    run_args = (
        "batch-retry",
        "patient-1",
        "RegistryForm",
        "questionnaire-1",
    )
    await job_orchestrator.run_batch_job(*run_args, worker_id="worker-1", attempt_count=1, max_attempts=2)

    first_partial = partial_bundles[-1][1]
    current_attempt[0] = 2
    await job_orchestrator.run_batch_job(
        *run_args,
        worker_id="worker-1",
        attempt_count=2,
        max_attempts=2,
        prior_bundle=first_partial,
    )

    def _find_observation(bundle, link_id):
        return next(entry for entry in bundle["entry"] if entry["resource"].get("code", {}).get("coding", [{}])[0].get("code") == link_id)

    first_observation = _find_observation(first_partial, "done-question")["resource"]
    retry_final = final_bundles[-1]
    retry_observation = _find_observation(retry_final, "done-question")["resource"]

    assert cql_calls == [["LibDone", "LibFail"], ["LibFail"]]
    assert requeues
    assert first_observation["id"] == retry_observation["id"]
    assert first_observation["identifier"] == retry_observation["identifier"]
    assert first_partial["id"] == retry_final["id"]


async def test_run_state_call_executes_synchronous_persistence_off_event_loop():
    event_loop_thread = threading.get_ident()
    observed_threads: list[int] = []

    def _state_operation(value: str) -> str:
        observed_threads.append(threading.get_ident())
        return value

    result = await job_orchestrator._run_state_call(_state_operation, "persisted")

    assert result == "persisted"
    assert observed_threads
    assert observed_threads[0] != event_loop_thread
