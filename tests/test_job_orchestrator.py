"""Tests batch orchestration from questionnaire job extraction through CQL/LLM result Bundle assembly.

The `run_harness` fixture and autouse document-state stubs patch persistence and execution seams to cover
checkpointing, retries, deterministic IDs, and child finalization."""

import json
from dataclasses import dataclass, field

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


def _questionnaire(*, cql: list[str] | None = None, prompts: list[str] | None = None, items: list[dict] | None = None) -> dict:
    extensions = []
    if cql:
        extensions.append({"url": job_orchestrator._CQL_JOB_LIST_URL, "extension": [{"valueString": name} for name in cql]})
    if prompts:
        extensions.append({"url": job_orchestrator._LLM_JOB_LIST_URL, "extension": [{"valueString": path} for path in prompts]})
    return {"resourceType": "Questionnaire", "name": "RegistryForm", "extension": extensions, "item": items or []}


def _structured_item(link_id: str, task: str) -> dict:
    return {"linkId": link_id, "text": f"Question {link_id}", "extension": [{"url": job_orchestrator._STRUCTURED_TASK_URL, "valueString": task}]}


def _observations_for(bundle: dict, link_id: str) -> list[dict]:
    return [entry["resource"] for entry in bundle["entry"] if entry["resource"].get("code", {}).get("coding", [{}])[0].get("code") == link_id]


@dataclass
class _RunHarness:
    """Stubbed run_batch_job dependencies that record every persisted write."""

    monkeypatch: pytest.MonkeyPatch
    partial_bundles: list[tuple[int, dict]] = field(default_factory=list)
    final_updates: list[tuple[str, dict | None]] = field(default_factory=list)
    child_updates: list[tuple[tuple, dict]] = field(default_factory=list)
    requeues: list[tuple] = field(default_factory=list)
    cql_calls: list[list[str]] = field(default_factory=list)

    def questionnaire(self, questionnaire: dict) -> None:
        async def _fake_fhir_get(resource_type, resource_id):
            return questionnaire

        self.monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)

    def cql(self, results_for_call) -> None:
        """Run CQL with ``results_for_call(call_index, library_names, patient_id) -> list[CqlResult]``."""

        async def _fake_run_cql_libraries(library_names, patient_id, on_result=None):
            self.cql_calls.append(list(library_names))
            results = results_for_call(len(self.cql_calls) - 1, library_names, patient_id)
            for result in results:
                if on_result:
                    await on_result(result)
            return results

        self.monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _fake_run_cql_libraries)

    @property
    def last_final_bundle(self) -> dict:
        bundle = self.final_updates[-1][1]
        assert bundle is not None
        return bundle


@pytest.fixture
def run_harness(monkeypatch) -> _RunHarness:
    harness = _RunHarness(monkeypatch)

    async def _fake_fetch_patient_resource(patient_id):
        return {"resourceType": "Patient", "id": patient_id}

    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob(f"job-{args[4]}", "running", None))
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: harness.child_updates.append((args, kwargs)) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", lambda batch_id, bundle, **kwargs: harness.partial_bundles.append((kwargs["attempt_count"], bundle)) or True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", lambda batch_id, status, bundle=None, **kwargs: harness.final_updates.append((status, bundle)) or True)
    monkeypatch.setattr(job_orchestrator, "requeue_batch_job_for_child_retry", lambda *args, **kwargs: harness.requeues.append(args) or True)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)
    return harness


async def _run(batch_id: str = "batch-1", **kwargs) -> None:
    await job_orchestrator.run_batch_job(batch_id, "patient-1", "RegistryForm", "questionnaire-1", worker_id="worker-1", **{"attempt_count": 1, **kwargs})


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


def _component(code: str, display: str, value: str) -> dict:
    return {
        "code": {"coding": [{"system": "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label", "code": code, "display": display}]},
        "valueString": value,
    }


@pytest.mark.parametrize(
    ("response", "expected_components"),
    [
        pytest.param(
            '{"nlp-answer-type":"SectionFinderTask","section-header":"head_review [5.39.115.133.89]","section-text":"closed fontanelles"}',
            [
                _component("nlp-answer-type", "Nlp Answer Type", "SectionFinderTask"),
                _component("section-header", "Section Header", "head_review [5.39.115.133.89]"),
                _component("section-text", "Section Text", "closed fontanelles"),
            ],
            id="plain-json",
        ),
        pytest.param(
            '```json\n{\n  "resultValue": "Normal",\n  "evidenceText": "Lungs clear.",\n  "reasoning": "Exam is normal."\n}\n```',
            [
                _component("resultValue", "Result Value", "Normal"),
                _component("evidenceText", "Evidence Text", "Lungs clear."),
                _component("reasoning", "Reasoning", "Exam is normal."),
            ],
            id="fenced-json",
        ),
    ],
)
def test_build_llm_observations_maps_json_response_to_components(response, expected_components):
    questionnaire = {"item": [{"item": [{"linkId": "1.1", "text": "Question text", "extension": [{"url": job_orchestrator._UNSTRUCTURED_TASK_URL, "valueString": "prompts/a"}]}]}]}
    llm_results = [LlmResult(prompt_path="prompts/a", document_results=[LlmDocumentResult(doc_id="doc-1", doc_type="Visit Note", doc_date="2026-05-22", response=response)])]

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123", "batch-1")

    assert len(entries) == 1
    resource = entries[0]["resource"]
    assert "valueString" not in resource
    assert resource["focus"] == [{"reference": "DocumentReference/doc-1"}]
    assert resource["component"] == expected_components


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


@pytest.mark.parametrize(
    ("checkpoint_interval", "checkpoint_every_tasks", "expected_partial_writes"),
    [pytest.param(3600.0, 2, 3, id="throttled"), pytest.param(0.0, 10, 6, id="every-task")],
)
async def test_run_batch_job_checkpoints_partial_bundles_and_finalizes(run_harness, monkeypatch, checkpoint_interval, checkpoint_every_tasks, expected_partial_writes):
    monkeypatch.setattr(settings, "batch_job_checkpoint_interval_seconds", checkpoint_interval)
    monkeypatch.setattr(settings, "batch_job_checkpoint_every_tasks", checkpoint_every_tasks)
    monkeypatch.setattr(settings, "use_llm", False)
    library_names = [f"Lib{index}" for index in range(5)]
    run_harness.questionnaire(_questionnaire(cql=library_names, items=[_structured_item(f"{name}-question", f"{name}.TaskA") for name in library_names]))
    run_harness.cql(lambda call, names, patient_id: [CqlResult(library_name=name, patient_id=patient_id, results={"TaskA": [{"value": name}]}) for name in names])

    await _run("batch-throttled")

    partial_bundles = [bundle for _, bundle in run_harness.partial_bundles]
    assert len(partial_bundles) == expected_partial_writes
    assert len(run_harness.child_updates) == len(library_names)
    assert partial_bundles[0]["entry"][0]["resource"]["status"] == "preliminary"
    if checkpoint_interval == 0:
        # Each completed task is visible in a snapshot as soon as it finishes.
        assert [len(_observations_for(bundle, "Lib0-question")) for bundle in partial_bundles[:2]] == [0, 1]
        assert _observations_for(partial_bundles[1], "Lib0-question")[0]["valueString"] == "Lib0"

    final_status, final_bundle = run_harness.final_updates[-1]
    assert final_status == "complete"
    assert final_bundle is not None
    assert final_bundle["id"] == partial_bundles[0]["id"]
    assert final_bundle["entry"][0]["resource"]["status"] == "complete"
    assert any(entry["resource"].get("resourceType") == "Patient" for entry in final_bundle["entry"])
    assert all(len(_observations_for(final_bundle, f"{name}-question")) == 1 for name in library_names)


async def test_run_batch_job_requeues_missing_llm_result_when_attempts_remain(run_harness, monkeypatch):
    run_harness.questionnaire(_questionnaire(prompts=["prompts/a"]))
    trace_context: dict = {}

    async def _fake_load_prompts(prompt_paths):
        return [Prompt(metadata=PromptMetadata(name="a", path="prompts/a"), content="prompt")]

    async def _fake_fetch_patient_documents(patient_id):
        return DocumentFetchResult(documents=[{"id": "doc-1", "text": "note", "date": "2026-05-22"}], excluded=[])

    async def _fake_run_all_prompts(prompts, documents, on_result=None, **kwargs):
        trace_context.update(kwargs)
        return []

    monkeypatch.setattr(job_orchestrator, "load_prompts", _fake_load_prompts)
    monkeypatch.setattr(job_orchestrator, "fetch_patient_documents", _fake_fetch_patient_documents)
    monkeypatch.setattr(job_orchestrator, "run_all_prompts", _fake_run_all_prompts)
    monkeypatch.setattr(settings, "use_llm", True)

    await _run(max_attempts=2)

    assert trace_context["trace_id"] == "batch-1"
    assert trace_context["job_package"] == "RegistryForm"
    assert run_harness.child_updates == [(("job-prompts/a", "error", {"message": "no llm result returned for prompt"}), {"batch_id": "batch-1", "worker_id": "worker-1", "attempt_count": 1})]
    assert run_harness.final_updates == []
    assert run_harness.requeues == [("batch-1", "worker-1", 1, "1 child task(s) failed: prompts/a", settings.batch_job_retry_delay_seconds)]


@pytest.mark.parametrize("successful_library, expected_status", [("LibGood", "partial"), (None, "error")])
async def test_run_batch_job_finalizes_exhausted_child_errors(run_harness, successful_library, expected_status):
    run_harness.questionnaire(_questionnaire(cql=["LibGood", "LibBad"] if successful_library else ["LibBad"]))
    run_harness.cql(
        lambda call, names, patient_id: [
            CqlResult(library_name=name, patient_id=patient_id, results={"TaskA": [{"value": "positive"}]})
            if name == successful_library
            else CqlResult(library_name=name, patient_id=patient_id, error="evaluation failed")
            for name in names
        ]
    )

    await _run(max_attempts=1)

    assert [status for status, _ in run_harness.final_updates] == [expected_status]
    status_observation = run_harness.last_final_bundle["entry"][0]["resource"]
    assert status_observation["status"] == "preliminary"
    assert status_observation["valueCodeableConcept"]["coding"][0]["code"] == "in-progress"
    assert run_harness.requeues == []


async def test_run_batch_job_records_excluded_documents_when_llm_is_skipped(run_harness, monkeypatch):
    run_harness.questionnaire(_questionnaire(prompts=["prompts/a"]))

    async def _fake_load_prompts(prompt_paths):
        return [Prompt(metadata=PromptMetadata(name="a", path="prompts/a"), content="prompt")]

    async def _fake_fetch_patient_documents(patient_id):
        return DocumentFetchResult(documents=[], excluded=[{"doc_id": "doc-oversized", "reason": "exceeds_max_bytes"}])

    monkeypatch.setattr(job_orchestrator, "load_prompts", _fake_load_prompts)
    monkeypatch.setattr(job_orchestrator, "fetch_patient_documents", _fake_fetch_patient_documents)
    monkeypatch.setattr(settings, "use_llm", True)

    await _run()

    assert run_harness.child_updates == [
        (
            (
                "job-prompts/a",
                "skipped",
                {"message": "skipped — no supporting documents", "excluded_documents": [{"doc_id": "doc-oversized", "reason": "exceeds_max_bytes"}]},
            ),
            {"batch_id": "batch-1", "worker_id": "worker-1", "attempt_count": 1},
        )
    ]


async def test_retry_restores_completed_children_without_rerunning_and_keeps_identity(run_harness, monkeypatch):
    monkeypatch.setattr(settings, "batch_job_checkpoint_interval_seconds", 0.0)
    run_harness.questionnaire(_questionnaire(cql=["LibDone", "LibFail"], items=[_structured_item("done-question", "LibDone.TaskA"), _structured_item("failed-question", "LibFail.TaskA")]))
    current_attempt = [1]

    def _ensure_job(*args, **kwargs):
        task_name = args[4]
        if current_attempt[0] == 2 and task_name == "LibDone":
            return LogicalJob("job-LibDone", "complete", {"results": {"TaskA": [{"value": "done"}]}, "error": None})
        return LogicalJob(f"job-{task_name}", "running", None)

    monkeypatch.setattr(job_orchestrator, "ensure_job", _ensure_job)
    run_harness.cql(
        lambda call, names, patient_id: (
            [
                CqlResult(library_name="LibDone", patient_id=patient_id, results={"TaskA": [{"value": "done"}]}),
                CqlResult(library_name="LibFail", patient_id=patient_id, results={}, error="temporary"),
            ]
            if call == 0
            else [CqlResult(library_name="LibFail", patient_id=patient_id, results={"TaskA": [{"value": "fixed"}]})]
        )
    )

    await _run("batch-retry", max_attempts=2)
    first_partial = run_harness.partial_bundles[-1][1]
    current_attempt[0] = 2
    await _run("batch-retry", attempt_count=2, max_attempts=2, prior_bundle=first_partial)

    # The completed child is restored from its persisted result, not re-evaluated.
    assert run_harness.cql_calls == [["LibDone", "LibFail"], ["LibFail"]]
    assert run_harness.requeues
    retry_partial = next(bundle for attempt, bundle in run_harness.partial_bundles if attempt == 2)
    retry_final = run_harness.last_final_bundle
    assert retry_partial["id"] == first_partial["id"] == retry_final["id"]

    first_observation = _observations_for(first_partial, "done-question")[0]
    retry_observation = _observations_for(retry_final, "done-question")[0]
    assert retry_observation["valueString"] == "done"
    assert (first_observation["id"], first_observation["identifier"]) == (retry_observation["id"], retry_observation["identifier"])
    assert _observations_for(retry_final, "failed-question")[0]["valueString"] == "fixed"
