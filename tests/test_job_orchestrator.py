from src.services.cql_executor import CqlResult
from src.models.prompt import Prompt, PromptMetadata
from src.services.llm_executor import LlmDocumentResult, LlmResult
from src.services.job_state import LogicalJob
from src.services import job_orchestrator


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


def test_build_tuple_observation_returns_supporting_condition_resource(monkeypatch):
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: "resource-123")

    obs, supporting = job_orchestrator._build_tuple_observation(
        "2026-05-22T12:00:00|http://loinc.org|1234-5|Example|positive",
        "1.1",
        "Question text",
        "patient-123",
        "RegistryForm",
        "Condition_History",
    )

    assert obs["focus"] == [{"reference": "Condition/resource-123"}]
    assert supporting is not None
    assert supporting["resourceType"] == "Condition"
    assert supporting["code"]["coding"][0]["code"] == "1234-5"


def test_build_llm_observations_deduplicates_same_document_and_text(monkeypatch):
    ids = iter(["obs-1", "obs-2"])
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: next(ids))
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

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123")

    assert len(entries) == 1
    assert entries[0]["resource"]["valueString"] == "same answer"
    assert entries[0]["resource"]["focus"] == [{"reference": "DocumentReference/doc-1"}]


def test_build_llm_observations_maps_json_response_to_components(monkeypatch):
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: "obs-1")
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

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123")

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


def test_build_llm_observations_maps_fenced_json_response_to_components(monkeypatch):
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: "obs-1")
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

    entries = job_orchestrator._build_llm_observations(llm_results, questionnaire, "RegistryForm", "patient-123")

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


def test_build_cql_observations_uses_matching_structured_task(monkeypatch):
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: "obs-123")
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

    entries = job_orchestrator._build_cql_observations(cql_results, questionnaire, "RegistryForm", "patient-123")

    assert len(entries) == 1
    assert entries[0]["resource"]["valueString"] == "positive"


def test_build_result_bundle_includes_status_and_patient(monkeypatch):
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: "bundle-123")

    bundle = job_orchestrator._build_result_bundle(
        {"resourceType": "Patient", "id": "patient-123"},
        [{"fullUrl": "Observation/1", "resource": {"resourceType": "Observation", "id": "1"}}],
        [{"fullUrl": "Observation/2", "resource": {"resourceType": "Observation", "id": "2"}}],
        "complete",
    )

    assert bundle["resourceType"] == "Bundle"
    assert bundle["id"] == "bundle-123"
    assert bundle["total"] == 4
    assert bundle["entry"][0]["resource"]["id"] == "status-observation"


async def test_run_batch_job_persists_completed_task_results_while_running(monkeypatch):
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
    monkeypatch.setattr(job_orchestrator, "start_batch_job_attempt", lambda batch_id: status_updates.append(("running", None)))
    monkeypatch.setattr(
        job_orchestrator,
        "update_batch_job_status",
        lambda batch_id, status, bundle=None, **kwargs: not status_updates.append((status, bundle)),
    )
    monkeypatch.setattr(job_orchestrator, "run_cql_libraries", _fake_run_cql_libraries)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)

    await job_orchestrator.run_batch_job("batch-1", "patient-1", "RegistryForm", "questionnaire-1")

    assert len(partial_bundles) == 2
    assert partial_bundles[0]["entry"][0]["resource"]["status"] == "preliminary"
    completed_snapshot = partial_bundles[1]
    completed_observations = [entry["resource"] for entry in completed_snapshot["entry"] if entry["resource"].get("code", {}).get("coding", [{}])[0].get("code") == "1.1"]
    assert len(completed_observations) == 1
    assert completed_observations[0]["valueString"] == "positive"

    assert status_updates[0] == ("running", None)
    final_status, final_bundle = status_updates[-1]
    assert final_status == "complete"
    assert final_bundle is not None
    assert final_bundle["id"] == completed_snapshot["id"]
    assert final_bundle["entry"][0]["resource"]["status"] == "complete"
    assert any(entry["resource"].get("resourceType") == "Patient" for entry in final_bundle["entry"])


async def test_run_batch_job_marks_missing_llm_result_as_error(monkeypatch):
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

    monkeypatch.setattr(job_orchestrator, "fhir_get", _fake_fhir_get)
    monkeypatch.setattr(job_orchestrator, "ensure_job", lambda *args, **kwargs: LogicalJob("job-123", "running", None))
    monkeypatch.setattr(job_orchestrator, "start_batch_job_attempt", lambda batch_id: None)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_status", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "update_batch_job_result", lambda *args, **kwargs: True)
    monkeypatch.setattr(job_orchestrator, "update_job_result", lambda *args, **kwargs: not updates.append((args, kwargs)))

    async def _fake_load_prompts(prompt_paths):
        return [Prompt(metadata=PromptMetadata(name="a", path="prompts/a"), content="prompt")]

    async def _fake_fetch_patient_documents(patient_id):
        return [{"id": "doc-1", "text": "note", "date": "2026-05-22"}]

    async def _fake_run_all_prompts(prompts, documents, on_result=None):
        return []

    async def _fake_fetch_patient_resource(patient_id):
        return None

    monkeypatch.setattr(job_orchestrator, "load_prompts", _fake_load_prompts)
    monkeypatch.setattr(job_orchestrator, "fetch_patient_documents", _fake_fetch_patient_documents)
    monkeypatch.setattr(job_orchestrator, "run_all_prompts", _fake_run_all_prompts)
    monkeypatch.setattr(job_orchestrator, "_fetch_patient_resource", _fake_fetch_patient_resource)
    monkeypatch.setattr(job_orchestrator, "use_llm", True)
    monkeypatch.setattr(job_orchestrator.uuid, "uuid4", lambda: "job-123")

    await job_orchestrator.run_batch_job("batch-1", "patient-1", "RegistryForm", "questionnaire-1")

    assert updates == [(("job-123", "error", {"message": "no llm result returned for prompt"}), {})]


async def test_run_batch_job_marks_only_unfinished_jobs_after_late_failure(monkeypatch):
    marked_errors: list[tuple[str, str]] = []
    status_updates: list[tuple[str, dict | None]] = []

    async def _failing_fhir_get(resource_type, resource_id):
        raise RuntimeError("late failure")

    monkeypatch.setattr(job_orchestrator, "fhir_get", _failing_fhir_get)
    monkeypatch.setattr(job_orchestrator, "mark_unfinished_jobs_error", lambda batch_id, message: marked_errors.append((batch_id, message)))
    monkeypatch.setattr(job_orchestrator, "start_batch_job_attempt", lambda batch_id: status_updates.append(("running", None)))
    monkeypatch.setattr(
        job_orchestrator,
        "update_batch_job_status",
        lambda batch_id, status, bundle=None, **kwargs: not status_updates.append((status, bundle)),
    )

    await job_orchestrator.run_batch_job("batch-1", "patient-1", "RegistryForm", "questionnaire-1")

    assert marked_errors == [("batch-1", "late failure")]
    assert status_updates[0] == ("running", None)
    assert status_updates[-1][0] == "error"
    assert status_updates[-1][1] is not None
    assert status_updates[-1][1]["resourceType"] == "OperationOutcome"


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
