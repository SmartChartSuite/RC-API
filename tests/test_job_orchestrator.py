from src.services.cql_executor import CqlResult
from src.services.llm_executor import LlmDocumentResult, LlmResult
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
