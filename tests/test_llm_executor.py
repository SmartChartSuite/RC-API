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


async def test_run_prompt_on_document_calls_litellm_completion(monkeypatch):
    monkeypatch.setattr(llm_executor, "use_llm", True)
    monkeypatch.setattr(llm_executor, "litellm_model", "gpt-test")
    captured: dict = {}

    def _fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer text"))])

    monkeypatch.setattr(llm_executor.litellm, "completion", _fake_completion)

    result = await llm_executor.run_prompt_on_document(_prompt(), {"id": "doc-1", "type": "Visit Note", "date": "2026-05-22", "text": "patient text"})

    assert result.response == "answer text"
    assert captured["model"] == "gpt-test"
    assert captured["messages"][0]["content"] == "system instructions"
    assert captured["messages"][1]["content"] == "patient text"


async def test_run_prompt_across_documents_collects_all_document_results(monkeypatch):
    async def _fake_run_prompt_on_document(prompt, document):
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
