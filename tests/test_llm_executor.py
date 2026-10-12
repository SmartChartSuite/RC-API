import asyncio
from types import SimpleNamespace

from src.models.prompt import Prompt, PromptMetadata
from src.services import llm_executor


def _prompt() -> Prompt:
    return Prompt(metadata=PromptMetadata(name="Prompt One", path="prompts/example"), content="system instructions")


async def test_run_prompt_on_document_returns_config_error_when_llm_disabled(monkeypatch):
    monkeypatch.setattr(llm_executor, "use_llm", False)

    result = await llm_executor.run_prompt_on_document(_prompt(), {"id": "doc-1", "type": "Visit Note", "date": "2026-05-22", "text": "sample"})

    assert result.error == "LLM not configured"
    assert result.response == ""


async def test_run_prompt_on_document_calls_litellm_responses(monkeypatch):
    monkeypatch.setattr(llm_executor, "use_llm", True)
    monkeypatch.setattr(llm_executor, "litellm_model", "gpt-test")
    monkeypatch.setattr(llm_executor, "litellm_model_reasoning_effort", None)
    monkeypatch.setattr(llm_executor, "litellm_prompt_cache_enabled", True)
    captured: dict = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text="answer text")

    monkeypatch.setattr(llm_executor.litellm, "aresponses", _fake_aresponses)

    result = await llm_executor.run_prompt_on_document(
        _prompt(),
        {"id": "doc-1", "type": "Visit Note", "date": "2026-05-22", "text": "patient text"},
        trace_id="batch-1",
        trace_metadata={"job_package": "package-a"},
    )

    assert result.response == "answer text"
    assert captured["model"] == "gpt-test"
    assert captured["instructions"] == "system instructions"
    assert captured["input"] == "patient text"
    assert captured["caching"] is False
    assert captured["extra_body"]["prompt_cache_key"].startswith("rcapi-")
    assert captured["allowed_openai_params"] == ["prompt_cache_key"]
    assert captured["metadata"] == {
        "generation_name": "analyze-document",
        "trace_name": "rc-api-batch-job",
        "tags": ["rc-api", "batch-job", "unstructured-llm"],
        "trace_id": "batch-1",
        "trace_metadata": {"job_package": "package-a"},
    }
    assert "patient text" not in str(captured["metadata"])
    assert "messages" not in captured
    assert "reasoning_effort" not in captured


async def test_run_prompt_on_document_passes_configured_reasoning_effort(monkeypatch):
    monkeypatch.setattr(llm_executor, "use_llm", True)
    monkeypatch.setattr(llm_executor, "litellm_model", "gpt-test")
    monkeypatch.setattr(llm_executor, "litellm_model_reasoning_effort", "high")
    monkeypatch.setattr(llm_executor, "litellm_prompt_cache_enabled", False)
    captured: dict = {}

    async def _fake_aresponses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text="answer text")

    monkeypatch.setattr(llm_executor.litellm, "aresponses", _fake_aresponses)

    result = await llm_executor.run_prompt_on_document(_prompt(), {"id": "doc-1", "type": "Visit Note", "date": "2026-05-22", "text": "patient text"})

    assert result.response == "answer text"
    assert captured["reasoning_effort"] == "high"
    assert captured["caching"] is False
    assert "extra_body" not in captured


def test_prompt_cache_key_identifies_shared_prompt_template():
    prompt = _prompt()
    same_key = llm_executor._prompt_cache_key("gpt-test", prompt)
    repeated_key = llm_executor._prompt_cache_key("gpt-test", prompt)
    changed_content_key = llm_executor._prompt_cache_key("gpt-test", Prompt(metadata=prompt.metadata, content="changed"))
    different_path_key = llm_executor._prompt_cache_key(
        "gpt-test",
        Prompt(metadata=PromptMetadata(name="Prompt Two", path="prompts/other"), content=prompt.content),
    )
    new_version_key = llm_executor._prompt_cache_key(
        "gpt-test",
        Prompt(metadata=PromptMetadata(name=prompt.metadata.name, path=prompt.metadata.path, version="2"), content=prompt.content),
    )

    assert same_key == repeated_key
    assert same_key == changed_content_key
    assert same_key.startswith("rcapi-")
    assert same_key != different_path_key
    assert same_key != new_version_key


async def test_run_prompt_across_documents_collects_all_document_results(monkeypatch):
    async def _fake_run_prompt_on_document(prompt, document, **kwargs):
        return llm_executor.LlmDocumentResult(doc_id=document["id"], doc_type=document["type"], doc_date=document["date"], response=f"done:{document['id']}")

    monkeypatch.setattr(llm_executor, "run_prompt_on_document", _fake_run_prompt_on_document)

    result = await llm_executor.run_prompt_across_documents(
        _prompt(),
        [
            {"id": "doc-1", "type": "Visit Note", "date": "2026-05-22"},
            {"id": "doc-2", "type": "Lab Note", "date": "2026-05-23"},
        ],
    )

    assert result.prompt_path == "prompts/example"
    assert [doc.response for doc in result.document_results] == ["done:doc-1", "done:doc-2"]


async def test_run_all_prompts_returns_empty_when_missing_inputs():
    prompt = _prompt()

    assert await llm_executor.run_all_prompts([], [{"id": "doc-1"}]) == []
    assert await llm_executor.run_all_prompts([prompt], []) == []


async def test_run_all_prompts_reports_each_result_as_it_finishes(monkeypatch):
    callback_order: list[str] = []
    prompts = [
        Prompt(metadata=PromptMetadata(name="Slow", path="prompts/slow"), content="slow"),
        Prompt(metadata=PromptMetadata(name="Fast", path="prompts/fast"), content="fast"),
    ]

    async def _fake_run_prompt_across_documents(prompt, documents, **kwargs):
        if prompt.metadata.path == "prompts/slow":
            await asyncio.sleep(0.01)
        return llm_executor.LlmResult(prompt_path=prompt.metadata.path)

    async def _on_result(result):
        callback_order.append(result.prompt_path)

    monkeypatch.setattr(llm_executor, "run_prompt_across_documents", _fake_run_prompt_across_documents)

    results = await llm_executor.run_all_prompts(prompts, [{"id": "doc-1"}], on_result=_on_result)

    assert callback_order == ["prompts/fast", "prompts/slow"]
    assert [result.prompt_path for result in results] == ["prompts/slow", "prompts/fast"]
