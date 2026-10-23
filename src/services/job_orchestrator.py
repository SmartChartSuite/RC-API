"""Execute one worker-owned batch attempt from Questionnaire package to result Bundle.

This module owns the execution pipeline for a claimed batch attempt: lease maintenance, supervised
CQL/LLM execution, Langfuse parent-span setup, child callbacks, partial checkpoints, retry handoff,
and finalization. Questionnaire parsing belongs to ``job_package`` and FHIR Observation/Bundle
assembly belongs to ``result_bundle``; external fetching and execution remain in their service
modules, while all durable persistence belongs to the ``job_state`` package.

The pipeline fetches and filters the Questionnaire, prepares child tasks and document context, runs
remaining CQL and LLM work concurrently, records missing-result failures, and either requeues or
finalizes the worker-owned attempt.
"""

import asyncio
import time
import uuid
from collections.abc import Coroutine
from contextlib import asynccontextmanager, suppress
from functools import partial
from typing import Any, cast

from loguru import logger

from src.services import prompt_loader
from src.services.cql_executor import CqlResult, run_cql_libraries
from src.services.external_fhir import fetch_patient as _fetch_patient_resource
from src.services.fhir_context import fetch_patient_documents
from src.services.fhir_proxy import fhir_get
from src.services.job_package import extract_job_lists, filter_requested_jobs
from src.services.job_state.batch_queue import renew_batch_job_lease, requeue_batch_job_for_child_retry
from src.services.job_state.batch_records import get_batch_job_documents, update_batch_job_result, update_batch_job_status, upsert_batch_job_documents
from src.services.job_state.child_jobs import ensure_job, update_job_result
from src.services.llm_executor import LlmResult, run_all_prompts
from src.services.prompt_loader import load_prompts
from src.services.result_bundle import (
    build_document_entries,
    build_result_bundle,
    ordered_document_set,
    ordered_task_entries,
)
from src.services.result_observations import (
    build_cql_observations,
    build_llm_observations,
    cql_result_from_payload,
    llm_result_from_payload,
    stable_id,
    summarize_llm_result,
)
from src.util.settings import settings
from src.util.threading import run_db


class LeaseLostError(RuntimeError):
    """Raised when a worker attempt no longer owns its batch lease."""


_run_state_call = run_db


@asynccontextmanager
async def _langfuse_batch_parent(
    batch_id: str,
    job_package: str,
    questionnaire_id: str,
    job_package_version: str | None,
    requested_jobs: list[str] | None,
):
    """Create a Langfuse root span for one batch and expose its OTel parent."""
    if not settings.use_langfuse:
        yield None
        return

    trace_id = batch_id.replace("-", "")
    if len(trace_id) != 32 or any(char not in "0123456789abcdefABCDEF" for char in trace_id):
        logger.warning(f"[batch={batch_id}] Skipping Langfuse parent span because the batch ID is not a valid trace ID")
        yield None
        return

    try:
        client = prompt_loader.get_langfuse_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[batch={batch_id}] Could not initialize Langfuse parent span: {exc}")
        yield None
        return

    if client is None:
        yield None
        return

    from opentelemetry import trace as otel_trace

    with client.start_as_current_observation(
        trace_context={"trace_id": trace_id},
        name="process-patient-batch",
        as_type="span",
        input={
            "batch_id": batch_id,
            "job_package": job_package,
            "questionnaire_id": questionnaire_id,
            "job_package_version": job_package_version,
            "requested_jobs": requested_jobs or [],
        },
        metadata={
            "batch_id": batch_id,
            "job_package": job_package,
            "questionnaire_id": questionnaire_id,
        },
    ):
        yield otel_trace.get_current_span()


class _BatchAttempt:
    """Hold one worker attempt's identity, task results, entries, and checkpoint state."""

    def __init__(
        self,
        batch_id: str,
        patient_id: str,
        job_package: str,
        worker_id: str,
        attempt_count: int,
        max_attempts: int,
        questionnaire: dict,
        form_name: str,
        cql_names: list[str],
        prompt_paths: list[str],
        prior_bundle: dict | None,
        checkpoint_interval_seconds: float,
        checkpoint_every_tasks: int,
    ) -> None:
        self.batch_id = batch_id
        self.patient_id = patient_id
        self.job_package = job_package
        self.worker_id = worker_id
        self.attempt_count = attempt_count
        self.max_attempts = max_attempts
        self.questionnaire = questionnaire
        self.form_name = form_name
        self.cql_names = cql_names
        self.prompt_paths = prompt_paths
        self.prior_bundle = prior_bundle

        self.task_results: list[dict] = []
        self.job_ids_by_task: dict[tuple[str, str], str] = {}
        self.excluded_documents: list[dict] = []
        self.cql_names_to_run: list[str] = []
        self.prompt_paths_to_run: list[str] = []
        self.restored_cql_results: dict[str, CqlResult] = {}
        self.restored_llm_results: dict[str, LlmResult] = {}
        self.prompts: list = []
        self.documents: list[dict] = []
        self.document_entries: list[dict] = []
        self.cql_entries_by_name: dict[str, list[dict]] = {}
        self.llm_entries_by_path: dict[str, list[dict]] = {}
        self.bundle_id: str | None = None

        self.checkpoint_interval_seconds = checkpoint_interval_seconds
        self.checkpoint_every_tasks = checkpoint_every_tasks
        self.last_partial_write_at: float | None = None
        self.tasks_since_partial_write = 0
        self.partial_snapshot_dirty = False
        self.snapshot_lock = asyncio.Lock()

    def with_excluded_documents(self, payload: dict) -> dict:
        if self.excluded_documents:
            payload["excluded_documents"] = self.excluded_documents
        return payload

    def require_owned(self, updated: bool) -> None:
        if not updated:
            raise LeaseLostError(f"Worker {self.worker_id} lost batch {self.batch_id} attempt {self.attempt_count}")

    async def ensure_task(self, task_name: str, task_type: str):
        task_job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.batch_id}:{task_type}:{task_name}"))
        snapshot = await _run_state_call(
            ensure_job,
            task_job_id,
            self.batch_id,
            self.patient_id,
            self.job_package,
            task_name,
            task_type,
            worker_id=self.worker_id,
            attempt_count=self.attempt_count,
        )
        if snapshot is None:
            self.require_owned(False)
            raise RuntimeError(f"Could not create child job for {task_type}:{task_name}")
        self.job_ids_by_task[(task_type, task_name)] = snapshot.job_id
        return snapshot

    async def update_task(self, job_id: str, status: str, result: dict | None = None) -> None:
        updated = await _run_state_call(
            update_job_result,
            job_id,
            status,
            result,
            batch_id=self.batch_id,
            worker_id=self.worker_id,
            attempt_count=self.attempt_count,
        )
        self.require_owned(updated)

    async def prepare_tasks(self) -> None:
        for library_name in self.cql_names:
            snapshot = await self.ensure_task(library_name, "structured")
            restored = cql_result_from_payload(library_name, self.patient_id, snapshot.result) if snapshot.status == "complete" else None
            if restored is not None:
                self.restored_cql_results[library_name] = restored
                self.task_results.append({"task_name": library_name, "task_type": "structured", "status": snapshot.status})
            elif snapshot.status in {"error", "skipped"}:
                self.task_results.append({"task_name": library_name, "task_type": "structured", "status": snapshot.status})
            else:
                self.cql_names_to_run.append(library_name)

        for prompt_path in self.prompt_paths:
            snapshot = await self.ensure_task(prompt_path, "unstructured")
            restored = llm_result_from_payload(prompt_path, snapshot.result) if snapshot.status == "complete" else None
            if restored is not None:
                self.restored_llm_results[prompt_path] = restored
                self.task_results.append({"task_name": prompt_path, "task_type": "unstructured", "status": snapshot.status})
            elif snapshot.status in {"error", "skipped"}:
                self.task_results.append({"task_name": prompt_path, "task_type": "unstructured", "status": snapshot.status})
            else:
                self.prompt_paths_to_run.append(prompt_path)

    async def prepare_context(self) -> None:
        # Concurrently load prompts and fetch patient documents when LLM jobs still need to run.
        # A failed DocumentReference search raises DocumentFetchError, failing this attempt so the
        # worker retries it — only a successful, empty search marks prompts as skipped.
        if self.prompt_paths_to_run:
            self.prompts, document_fetch_result = await _gather_supervised(
                load_prompts(self.prompt_paths_to_run),
                fetch_patient_documents(self.patient_id),
            )
            self.documents = document_fetch_result.documents
            self.excluded_documents = document_fetch_result.excluded
            stored = await _run_state_call(
                upsert_batch_job_documents,
                self.batch_id,
                self.documents,
                worker_id=self.worker_id,
                attempt_count=self.attempt_count,
            )
            self.require_owned(stored)
            if not self.documents:
                logger.info(f"[batch={self.batch_id}] No DocumentReferences found — skipping LLM execution")
                for path in self.prompt_paths_to_run:
                    job_id = self.job_ids_by_task.get(("unstructured", path))
                    if job_id:
                        await self.update_task(job_id, "skipped", self.with_excluded_documents({"message": "skipped — no supporting documents"}))
                    self.task_results.append(
                        self.with_excluded_documents(
                            {
                                "task_name": path,
                                "task_type": "unstructured",
                                "status": "skipped",
                                "result": "skipped — no supporting documents",
                            }
                        )
                    )
            elif not settings.use_llm:
                logger.info(f"[batch={self.batch_id}] LiteLLM is not configured — skipping LLM execution")
                for path in self.prompt_paths_to_run:
                    job_id = self.job_ids_by_task.get(("unstructured", path))
                    if job_id:
                        await self.update_task(job_id, "skipped", self.with_excluded_documents({"message": "skipped — llm not configured"}))
                    self.task_results.append(
                        self.with_excluded_documents(
                            {
                                "task_name": path,
                                "task_type": "unstructured",
                                "status": "skipped",
                                "result": "skipped — llm not configured",
                            }
                        )
                    )

        stored_documents = await _run_state_call(get_batch_job_documents, self.batch_id)
        bundle_documents = ordered_document_set(self.documents, stored_documents)
        prior_bundle_id = self.prior_bundle.get("id") if isinstance(self.prior_bundle, dict) else None
        self.bundle_id = prior_bundle_id if isinstance(prior_bundle_id, str) and prior_bundle_id else stable_id(self.batch_id, "bundle")
        self.cql_entries_by_name = {name: build_cql_observations([result], self.questionnaire, self.form_name, self.patient_id, self.batch_id) for name, result in self.restored_cql_results.items()}
        self.llm_entries_by_path = {path: build_llm_observations([result], self.questionnaire, self.form_name, self.patient_id, self.batch_id) for path, result in self.restored_llm_results.items()}
        self.document_entries = build_document_entries(bundle_documents, self.patient_id, self.batch_id)
        await self.persist_partial_bundle()

    async def prepare(self) -> None:
        await self.prepare_tasks()
        await self.prepare_context()

    def current_result_entries(self) -> tuple[list[dict], list[dict]]:
        return (
            ordered_task_entries(self.cql_names, self.cql_entries_by_name),
            ordered_task_entries(self.prompt_paths, self.llm_entries_by_path),
        )

    async def persist_partial_bundle(self) -> None:
        current_cql_entries, current_llm_entries = self.current_result_entries()
        partial_bundle = build_result_bundle(
            None,
            current_cql_entries,
            current_llm_entries + self.document_entries,
            "preliminary",
            self.bundle_id,
            batch_id=self.batch_id,
        )
        updated = await _run_state_call(
            update_batch_job_result,
            self.batch_id,
            partial_bundle,
            worker_id=self.worker_id,
            attempt_count=self.attempt_count,
        )
        self.require_owned(updated)
        self.last_partial_write_at = time.monotonic()
        self.tasks_since_partial_write = 0
        self.partial_snapshot_dirty = False

    async def checkpoint_partial_bundle(self) -> None:
        self.tasks_since_partial_write += 1
        self.partial_snapshot_dirty = True
        now = time.monotonic()
        elapsed = now - self.last_partial_write_at if self.last_partial_write_at is not None else self.checkpoint_interval_seconds
        if self.checkpoint_interval_seconds == 0 or elapsed >= self.checkpoint_interval_seconds or self.tasks_since_partial_write >= self.checkpoint_every_tasks:
            await self.persist_partial_bundle()

    async def flush_dirty_partial_bundle(self) -> None:
        if self.partial_snapshot_dirty:
            await self.persist_partial_bundle()

    async def record_cql_result(self, cql_result: CqlResult) -> None:
        async with self.snapshot_lock:
            task_name = cql_result.library_name
            status = "error" if cql_result.error else "complete"
            job_id = self.job_ids_by_task.get(("structured", task_name))
            if job_id:
                await self.update_task(job_id, status, {"error": cql_result.error, "results": cql_result.results})
            self.task_results.append(
                {
                    "task_name": task_name,
                    "task_type": "structured",
                    "status": status,
                    "error": cql_result.error,
                }
            )
            self.cql_entries_by_name[task_name] = build_cql_observations([cql_result], self.questionnaire, self.form_name, self.patient_id, self.batch_id)
            await self.checkpoint_partial_bundle()

    async def record_llm_result(self, llm_result: LlmResult) -> None:
        async with self.snapshot_lock:
            task_name = llm_result.prompt_path
            status, result_payload = summarize_llm_result(llm_result)
            result_payload = self.with_excluded_documents(result_payload)
            job_id = self.job_ids_by_task.get(("unstructured", task_name))
            if job_id:
                await self.update_task(job_id, status, result_payload)
            self.task_results.append(self.with_excluded_documents({"task_name": task_name, "task_type": "unstructured", "status": status}))
            self.llm_entries_by_path[task_name] = build_llm_observations([llm_result], self.questionnaire, self.form_name, self.patient_id, self.batch_id)
            await self.checkpoint_partial_bundle()

    async def empty(self) -> list:
        return []

    async def run_concurrent_tasks(self, parent_otel_span: Any | None):
        cql_task = run_cql_libraries(self.cql_names_to_run, self.patient_id, on_result=self.record_cql_result) if self.cql_names_to_run else self.empty()
        llm_task = (
            run_all_prompts(
                self.prompts,
                self.documents,
                on_result=self.record_llm_result,
                trace_id=self.batch_id,
                job_package=self.job_package,
                parent_otel_span=parent_otel_span,
            )
            if (self.prompt_paths_to_run and self.documents and settings.use_llm)
            else self.empty()
        )
        return await _gather_supervised(cql_task, llm_task)

    async def record_missing_results(self, cql_results: list[CqlResult], llm_results: list[LlmResult]) -> None:
        completed_cql_names = {result.library_name for result in cql_results}
        for library_name in self.cql_names_to_run:
            if library_name in completed_cql_names:
                continue
            logger.warning(f"[batch={self.batch_id}] No CQL result returned for library '{library_name}'")
            job_id = self.job_ids_by_task.get(("structured", library_name))
            if job_id:
                await self.update_task(job_id, "error", {"message": "no cql result returned for library"})
            self.task_results.append({"task_name": library_name, "task_type": "structured", "status": "error"})
            await self.checkpoint_partial_bundle()

        if self.prompt_paths_to_run and self.documents and settings.use_llm:
            completed_llm_paths = {result.prompt_path for result in llm_results}
            for prompt_path in self.prompt_paths_to_run:
                if prompt_path in completed_llm_paths:
                    continue
                logger.warning(f"[batch={self.batch_id}] No LLM result returned for prompt '{prompt_path}'")
                job_id = self.job_ids_by_task.get(("unstructured", prompt_path))
                if job_id:
                    await self.update_task(job_id, "error", self.with_excluded_documents({"message": "no llm result returned for prompt"}))
                self.task_results.append(self.with_excluded_documents({"task_name": prompt_path, "task_type": "unstructured", "status": "error"}))
                await self.checkpoint_partial_bundle()

    async def finalize(self) -> None:
        # Fetch the Patient resource and assemble the final Bundle.
        patient_resource = await _fetch_patient_resource(self.patient_id)
        all_errors = [task for task in self.task_results if task.get("status") == "error"]
        overall_status = "complete" if not all_errors else "preliminary"
        final_cql_entries, final_llm_entries = self.current_result_entries()
        result_bundle = build_result_bundle(
            patient_resource,
            final_cql_entries,
            final_llm_entries + self.document_entries,
            overall_status,
            self.bundle_id,
            batch_id=self.batch_id,
        )

        if all_errors and self.attempt_count < self.max_attempts:
            failed_task_names = [str(task.get("task_name", "unknown")) for task in all_errors]
            failure_message = f"{len(failed_task_names)} child task(s) failed: {', '.join(failed_task_names)}"
            await self.flush_dirty_partial_bundle()
            requeued = await _run_state_call(
                requeue_batch_job_for_child_retry,
                self.batch_id,
                self.worker_id,
                self.attempt_count,
                failure_message,
                settings.batch_job_retry_delay_seconds,
            )
            self.require_owned(requeued)
            logger.info(f"[batch={self.batch_id}] Requeued after child failures for attempt {self.attempt_count + 1}")
            return

        # Child errors keep the Bundle preliminary while the batch status distinguishes an exhausted
        # partial result from a total failure.
        final_status = "complete" if not all_errors else "partial" if any(task.get("status") == "complete" for task in self.task_results) else "error"
        updated = await _run_state_call(
            update_batch_job_status,
            self.batch_id,
            final_status,
            result_bundle,
            worker_id=self.worker_id,
            attempt_count=self.attempt_count,
        )
        self.require_owned(updated)
        logger.info(f"[batch={self.batch_id}] Completed with status={final_status}. Bundle entries: {result_bundle['total']}")


async def _maintain_batch_lease(batch_id: str, worker_id: str, attempt_count: int) -> None:
    """Renew an owned batch lease while long-running external work is in flight."""
    while True:
        await asyncio.sleep(settings.batch_job_heartbeat_interval_seconds)
        renewed = await asyncio.to_thread(
            renew_batch_job_lease,
            batch_id,
            worker_id,
            attempt_count,
            settings.batch_worker_lease_seconds,
        )
        if not renewed:
            raise LeaseLostError(f"Worker {worker_id} lost batch {batch_id} attempt {attempt_count}")


async def _gather_supervised(*coros: Coroutine[Any, Any, Any]) -> list[Any]:
    """Run coroutines concurrently; if one fails, cancel the rest and re-raise that failure.

    Unlike ``asyncio.gather``, siblings never keep running after a failure. A single failure
    is re-raised unwrapped so callers can still ``except LeaseLostError`` etc.
    """
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(coro) for coro in coros]
    except BaseExceptionGroup as group:
        if len(group.exceptions) == 1:
            raise group.exceptions[0] from None
        raise
    return [task.result() for task in tasks]


def _on_heartbeat_done(pipeline_task: asyncio.Task[Any] | None, task: asyncio.Task[None]) -> None:
    if not task.cancelled() and isinstance(task.exception(), LeaseLostError) and pipeline_task is not None:
        pipeline_task.cancel()


async def run_batch_job(
    batch_id: str,
    patient_id: str,
    job_package: str,
    questionnaire_id: str,
    job_package_version: str | None = None,
    requested_jobs: list[str] | None = None,
    *,
    worker_id: str,
    attempt_count: int,
    max_attempts: int | None = None,
    prior_bundle: dict | None = None,
) -> None:
    """Run the full CQL + LLM pipeline for one worker-owned batch attempt."""
    max_attempts = max_attempts if max_attempts is not None else settings.batch_job_max_attempts
    logger.info(f"[batch={batch_id}] Starting batch job for Patient/{patient_id}, pkg={job_package}, jobs={requested_jobs or 'all'}, attempt={attempt_count}/{max_attempts}")
    checkpoint_interval_seconds = settings.batch_job_checkpoint_interval_seconds
    checkpoint_every_tasks = settings.batch_job_checkpoint_every_tasks

    attempt: _BatchAttempt | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        heartbeat_task = asyncio.create_task(_maintain_batch_lease(batch_id, worker_id, attempt_count))
        # If the lease cannot be renewed, stop the pipeline instead of letting it keep calling
        # CQL/LLM services for an attempt another worker may already own.
        pipeline_task = asyncio.current_task()
        heartbeat_task.add_done_callback(partial(_on_heartbeat_done, pipeline_task))

        # 1. Fetch Questionnaire from HAPI FHIR.
        questionnaire_data = await fhir_get("Questionnaire", questionnaire_id)
        if questionnaire_data.get("resourceType") == "OperationOutcome":
            raise RuntimeError(f"Could not fetch Questionnaire/{questionnaire_id}: {questionnaire_data}")

        questionnaire = cast(dict[str, Any], questionnaire_data)
        form_name = questionnaire.get("name", job_package)
        logger.info(f"[batch={batch_id}] Loaded Questionnaire '{form_name}'")

        # 2. Extract task lists and build the attempt state.
        cql_names, prompt_paths = extract_job_lists(questionnaire)
        cql_names, prompt_paths = filter_requested_jobs(cql_names, prompt_paths, requested_jobs)
        logger.info(f"[batch={batch_id}] CQL libs={cql_names}, LLM prompts={prompt_paths}")
        attempt = _BatchAttempt(
            batch_id,
            patient_id,
            job_package,
            worker_id,
            attempt_count,
            max_attempts,
            questionnaire,
            form_name,
            cql_names,
            prompt_paths,
            prior_bundle,
            checkpoint_interval_seconds,
            checkpoint_every_tasks,
        )
        await attempt.prepare()

        # 3. Run remaining CQL and LLM tasks under one Langfuse parent span.
        async with _langfuse_batch_parent(
            batch_id,
            job_package,
            questionnaire_id,
            job_package_version,
            requested_jobs,
        ) as parent_otel_span:
            cql_results, llm_results = await attempt.run_concurrent_tasks(parent_otel_span)
        await attempt.record_missing_results(cql_results, llm_results)

        # 4. Fetch the Patient, then retry or finalize the attempt.
        await attempt.finalize()

    except asyncio.CancelledError:
        heartbeat_error = heartbeat_task.exception() if heartbeat_task is not None and heartbeat_task.done() and not heartbeat_task.cancelled() else None
        if isinstance(heartbeat_error, LeaseLostError):
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            logger.warning(f"[batch={batch_id}] Lease renewal failed — cancelled in-flight work")
            raise heartbeat_error from None
        raise
    except LeaseLostError:
        logger.warning(f"[batch={batch_id}] Stopping work after losing lease ownership")
        raise
    except Exception as exc:
        if attempt is not None and attempt.partial_snapshot_dirty:
            try:
                await attempt.flush_dirty_partial_bundle()
            except Exception as checkpoint_exc:  # noqa: BLE001
                logger.warning(f"[batch={batch_id}] Could not flush dirty partial Bundle before failure: {type(checkpoint_exc).__name__}")
        logger.exception(f"[batch={batch_id}] Unhandled error in run_batch_job: {exc}")
        raise
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError, LeaseLostError):
                await heartbeat_task
