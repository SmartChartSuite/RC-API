"""LiteLLM-based LLM execution service.

Mirrors v0 NLPaaS approach: one LLM call per document per prompt, results collapsed.
All calls use asyncio.gather for concurrency.

Guarded by use_llm — if LiteLLM is not configured, run_all_prompts() should not be called.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import litellm
from loguru import logger

from src.models.prompt import Prompt
from src.util.settings import (
    litellm_api_base,
    litellm_api_key,
    litellm_model,
    litellm_model_reasoning_effort,
    litellm_prompt_cache_enabled,
    llm_max_concurrency,
    use_llm,
)

litellm.suppress_debug_info = True

# Bounds the number of concurrent in-flight LLM calls per job so large
# prompt/document fan-outs don't overwhelm the provider or exhaust connections.
_llm_semaphore = asyncio.Semaphore(llm_max_concurrency)

_LANGFUSE_GENERATION_NAME = "analyze-document"
_LANGFUSE_TRACE_NAME = "rc-api-batch-job"
_LANGFUSE_TAGS = ["rc-api", "batch-job", "unstructured-llm"]


def _langfuse_metadata(
    trace_id: str | None,
    trace_metadata: dict[str, object] | None,
    parent_otel_span: Any | None = None,
) -> dict[str, object]:
    """Build low-cardinality metadata for LiteLLM's Langfuse OTEL callback."""
    metadata: dict[str, object] = {
        "generation_name": _LANGFUSE_GENERATION_NAME,
        "trace_name": _LANGFUSE_TRACE_NAME,
        "tags": list(_LANGFUSE_TAGS),
    }
    if trace_id:
        metadata["trace_id"] = trace_id
    if trace_metadata:
        metadata["trace_metadata"] = dict(trace_metadata)
    if parent_otel_span is not None:
        metadata["litellm_parent_otel_span"] = parent_otel_span
    return metadata


def _prompt_cache_key(model: str, prompt: Prompt) -> str:
    """Build a stable key for the shared model/prompt template prefix."""
    prompt_version = prompt.metadata.version or prompt.metadata.last_updated or ""
    cache_material = f"{model}\x00{prompt.metadata.path}\x00{prompt_version}"
    digest = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()[:32]
    return f"rcapi-{digest}"


@dataclass
class LlmDocumentResult:
    """Result of running a single prompt against a single document."""

    doc_id: str
    doc_type: str
    doc_date: str
    response: str  # raw LLM response text
    error: str | None = None
    doc_text: str | None = None


@dataclass
class LlmResult:
    """Results of running a single prompt across all patient documents."""

    prompt_path: str  # matches the unstructuredTask extension value on Questionnaire items
    document_results: list[LlmDocumentResult] = field(default_factory=list)


async def run_prompt_on_document(
    prompt: Prompt,
    document: dict,
    *,
    trace_id: str | None = None,
    trace_metadata: dict[str, object] | None = None,
    parent_otel_span: Any | None = None,
) -> LlmDocumentResult:
    """Run a single prompt against a single DocumentReference's plain text.

    Appends the document text to the end of the prompt content before calling the LLM.
    Returns an LlmDocumentResult with the document metadata and LLM response.
    """
    doc_id = document["id"]
    doc_type = document.get("type", "Unknown")
    doc_date = document.get("date", "")

    if not use_llm:
        logger.warning("LLM not configured — skipping prompt execution")
        return LlmDocumentResult(doc_id=doc_id, doc_type=doc_type, doc_date=doc_date, response="", error="LLM not configured", doc_text=document.get("text"))

    try:
        assert litellm_model
        request_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "api_base": litellm_api_base,
            "api_key": litellm_api_key,
            "input": document["text"],
            "instructions": prompt.content,
            "caching": False,
            "metadata": _langfuse_metadata(trace_id, trace_metadata, parent_otel_span),
        }
        if litellm_prompt_cache_enabled:
            request_kwargs["extra_body"] = {"prompt_cache_key": _prompt_cache_key(litellm_model, prompt)}
            request_kwargs["allowed_openai_params"] = ["prompt_cache_key"]
        if litellm_model_reasoning_effort:
            request_kwargs["reasoning_effort"] = litellm_model_reasoning_effort

        async with _llm_semaphore:
            response = await litellm.aresponses(**request_kwargs)
        answer = getattr(response, "output_text", "") or ""
        logger.debug(f"LLM response received for prompt '{prompt.metadata.path}' / doc {doc_id}")
        return LlmDocumentResult(doc_id=doc_id, doc_type=doc_type, doc_date=doc_date, response=answer, doc_text=document.get("text"))
    except Exception as exc:  # noqa: BLE001
        # Isolate provider failures to this document so other calls can complete.
        logger.error(f"LLM call failed for prompt '{prompt.metadata.path}' / doc {doc_id}: {exc}")
        return LlmDocumentResult(doc_id=doc_id, doc_type=doc_type, doc_date=doc_date, response="", error=str(exc))


async def run_prompt_across_documents(
    prompt: Prompt,
    documents: list[dict],
    *,
    trace_id: str | None = None,
    trace_metadata: dict[str, object] | None = None,
    parent_otel_span: Any | None = None,
) -> LlmResult:
    """Run a single prompt against all patient documents concurrently.

    Returns a single LlmResult containing all per-document findings.
    Emits a single aggregate INFO summary per prompt; per-document detail is at DEBUG.
    """
    doc_results = await asyncio.gather(
        *[
            run_prompt_on_document(
                prompt,
                doc,
                trace_id=trace_id,
                trace_metadata=trace_metadata,
                parent_otel_span=parent_otel_span,
            )
            for doc in documents
        ]
    )
    doc_results = list(doc_results)
    errors = sum(1 for r in doc_results if r.error)
    succeeded = len(doc_results) - errors
    logger.info(f"Prompt '{prompt.metadata.path}': {succeeded}/{len(doc_results)} document(s) succeeded" + (f", {errors} error(s)" if errors else ""))
    return LlmResult(
        prompt_path=prompt.metadata.path,
        document_results=doc_results,
    )


async def run_all_prompts(
    prompts: list[Prompt],
    documents: list[dict],
    on_result: Callable[[LlmResult], Awaitable[None]] | None = None,
    *,
    trace_id: str | None = None,
    job_package: str | None = None,
    parent_otel_span: Any | None = None,
) -> list[LlmResult]:
    """Run all prompts across all documents concurrently.

    Only called when len(documents) > 0; orchestrator skips this if no documents found.
    The optional callback runs when all document calls for one prompt have completed.
    """
    if not prompts or not documents:
        return []

    trace_metadata: dict[str, object] | None = None
    if trace_id or job_package:
        trace_metadata = {
            "prompt_paths": list(dict.fromkeys(prompt.metadata.path for prompt in prompts)),
            "document_count": len(documents),
        }
        if trace_id:
            trace_metadata["batch_id"] = trace_id
        if job_package:
            trace_metadata["job_package"] = job_package

    async def _run_and_report(prompt: Prompt) -> LlmResult:
        result = await run_prompt_across_documents(
            prompt,
            documents,
            trace_id=trace_id,
            trace_metadata=trace_metadata,
            parent_otel_span=parent_otel_span,
        )
        if on_result:
            await on_result(result)
        return result

    results = await asyncio.gather(*[_run_and_report(prompt) for prompt in prompts])
    return list(results)
