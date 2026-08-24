"""Async batch job orchestrator.

Orchestrates the full CQL + LLM pipeline for a batch job submission:
1. Fetch Questionnaire (job package) from HAPI FHIR
2. Extract structured (CQL) library names and unstructured (LLM) prompt paths
3. Concurrently load prompts + fetch patient DocumentReferences
4. Concurrently run CQL libraries + LLM prompts (skip LLM if no documents)
5. Build result FHIR Bundle (Observations + supporting resources)
6. Persist to DB; update batch job status to complete
"""

import asyncio
from dataclasses import asdict
import json
import re
import uuid
from typing import Any, cast
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger
import base64

from src.services.cql_executor import CqlResult, run_cql_libraries
from src.services.errorhandler import make_operation_outcome
from src.services.fhir_context import fetch_patient_documents
from src.services.fhir_proxy import fhir_get
from src.services.job_state import (
    create_job,
    update_batch_job_result,
    update_batch_job_status,
    update_job_result,
)
from src.services.llm_executor import LlmResult, run_all_prompts
from src.services.prompt_loader import load_prompts
from src.util.settings import deploy_url, external_fhir_server_auth, external_fhir_server_url, use_llm

# Questionnaire extension URLs
_CQL_JOB_LIST_URL = "http://gtri.gatech.edu/fakeFormIg/structured-form-job-list"
_LLM_JOB_LIST_URL = "http://gtri.gatech.edu/fakeFormIg/unstructured-form-job-list"
_STRUCTURED_TASK_URL = "http://gtri.gatech.edu/fakeFormIg/structuredTask"
_UNSTRUCTURED_TASK_URL = "http://gtri.gatech.edu/fakeFormIg/unstructuredTask"
_UNSTRUCTURED_COMPONENT_SYSTEM = "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label"


# Questionnaire parsing
def _extract_job_lists(questionnaire: dict) -> tuple[list[str], list[str]]:
    """Extract CQL library names and LLM prompt paths from a Questionnaire resource."""
    cql_names: list[str] = []
    prompt_paths: list[str] = []
    for ext in questionnaire.get("extension", []):
        if ext.get("url") == _CQL_JOB_LIST_URL:
            for sub in ext.get("extension", []):
                val = sub.get("valueString")
                if val:
                    cql_names.append(val)
        elif ext.get("url") == _LLM_JOB_LIST_URL:
            for sub in ext.get("extension", []):
                val = sub.get("valueString")
                if val:
                    prompt_paths.append(val)
    return cql_names, prompt_paths


def _filter_requested_jobs(cql_names: list[str], prompt_paths: list[str], requested_jobs: list[str] | None) -> tuple[list[str], list[str]]:
    """Limit execution to the named CQL libraries and prompt paths requested by the client."""
    if not requested_jobs:
        return cql_names, prompt_paths

    requested = list(dict.fromkeys(job for job in requested_jobs if job))
    if not requested:
        return cql_names, prompt_paths

    available_cql = set(cql_names)
    available_prompts = set(prompt_paths)
    prompt_names: dict[str, list[str]] = {}
    for path in prompt_paths:
        prompt_names.setdefault(Path(path).name, []).append(path)

    selected_cql: set[str] = set()
    selected_prompts: set[str] = set()
    missing_jobs: list[str] = []
    ambiguous_jobs: dict[str, list[str]] = {}

    for job_name in requested:
        if job_name in available_cql:
            selected_cql.add(job_name)
            continue
        if job_name in available_prompts:
            selected_prompts.add(job_name)
            continue

        prompt_matches = prompt_names.get(job_name, [])
        if len(prompt_matches) == 1:
            selected_prompts.add(prompt_matches[0])
            continue
        if len(prompt_matches) > 1:
            ambiguous_jobs[job_name] = prompt_matches
            continue

        missing_jobs.append(job_name)

    if ambiguous_jobs:
        details = "; ".join(f"{job_name}: {matches}" for job_name, matches in sorted(ambiguous_jobs.items()))
        raise ValueError(f"Requested job name matches multiple prompts in job package '{details}'")
    if missing_jobs:
        raise ValueError(f"Requested job(s) not found in job package: {', '.join(missing_jobs)}")

    return [name for name in cql_names if name in selected_cql], [path for path in prompt_paths if path in selected_prompts]


def _get_item_task(item: dict, extension_url: str) -> str | None:
    for ext in item.get("extension", []):
        if ext.get("url") == extension_url:
            return ext.get("valueString")
    return None


def _format_component_display(component_key: str) -> str:
    normalized = component_key.replace("-", " ").replace("_", " ")
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    return normalized.title()


def _extract_json_payload(response_text: str) -> str:
    stripped = response_text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3:
        return stripped

    first_line = lines[0].strip().lower()
    last_line = lines[-1].strip()
    if first_line not in {"```", "```json"} or last_line != "```":
        return stripped

    return "\n".join(lines[1:-1]).strip()


def _build_llm_components(response_text: str) -> list[dict] | None:
    try:
        parsed = json.loads(_extract_json_payload(response_text))
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    components: list[dict] = []
    for key, value in parsed.items():
        if isinstance(value, str):
            value_string = value
        elif value is None:
            value_string = "null"
        elif isinstance(value, (int, float, bool)):
            value_string = str(value)
        else:
            value_string = json.dumps(value)

        components.append(
            {
                "code": {
                    "coding": [
                        {
                            "system": _UNSTRUCTURED_COMPONENT_SYSTEM,
                            "code": key,
                            "display": _format_component_display(key),
                        }
                    ]
                },
                "valueString": value_string,
            }
        )

    return components


# Observation builders
def _obs_base(link_id: str, question_text: str, patient_id: str, form_name: str) -> dict:
    obs_id = str(uuid.uuid4())
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "identifier": [{"system": deploy_url, "value": f"Observation/{obs_id}"}],
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "survey",
                        "display": "Survey",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": f"urn:gtri:heat:form:{form_name}",
                    "code": link_id,
                    "display": question_text,
                }
            ]
        },
        "subject": {"reference": f"Patient/{patient_id}"},
    }


def _build_cql_observations(
    cql_results: list[CqlResult],
    questionnaire: dict,
    form_name: str,
    patient_id: str,
) -> list[dict]:
    """Walk Questionnaire items, match structuredTask to CQL results, build Observations."""
    entries: list[dict] = []
    seen_urls: set[str] = set()

    # Index CQL results by library name for O(1) lookup
    cql_by_lib: dict[str, CqlResult] = {r.library_name: r for r in cql_results if not r.error}

    for group in questionnaire.get("item", []):
        for question in group.get("item", []):
            link_id = question.get("linkId", "")
            question_text = question.get("text", "")
            task_ext = _get_item_task(question, _STRUCTURED_TASK_URL)
            if not task_ext:
                continue

            try:
                library_name, task_name = task_ext.split(".", 1)
            except ValueError:
                logger.warning(f"structuredTask has unexpected format: {task_ext}")
                continue

            cql_result = cql_by_lib.get(library_name)
            if not cql_result:
                logger.debug(f"No CQL result for library '{library_name}', skipping {link_id}")
                continue

            result_entries = cql_result.results.get(task_name, [])
            if not result_entries:
                logger.debug(f"No CQL data for task '{task_name}' in {library_name}")
                continue

            for res_entry in result_entries:
                # Tuple result (pipe-delimited value string from CQL)
                raw_value = res_entry.get("value")
                if isinstance(raw_value, str) and "|" in raw_value:
                    obs, supporting = _build_tuple_observation(raw_value, link_id, question_text, patient_id, form_name, task_name)
                    obs_url = f"Observation/{obs['id']}"
                    if obs_url not in seen_urls:
                        entries.append({"fullUrl": obs_url, "resource": obs})
                        seen_urls.add(obs_url)
                    if supporting:
                        sup_url = f"{supporting['resourceType']}/{supporting['id']}"
                        if sup_url not in seen_urls:
                            entries.append({"fullUrl": sup_url, "resource": supporting})
                            seen_urls.add(sup_url)
                elif isinstance(raw_value, str):
                    # Simple scalar valueString observation
                    obs = _obs_base(link_id, question_text, patient_id, form_name)
                    obs["valueString"] = raw_value
                    obs_url = f"Observation/{obs['id']}"
                    if obs_url not in seen_urls:
                        entries.append({"fullUrl": obs_url, "resource": obs})
                        seen_urls.add(obs_url)
                elif isinstance(res_entry, dict) and res_entry.get("resourceType"):
                    # FHIR resource result — reference it via focus
                    res_type = res_entry["resourceType"]
                    res_id = res_entry.get("id", str(uuid.uuid4()))
                    obs = _obs_base(link_id, question_text, patient_id, form_name)
                    obs["focus"] = [{"reference": f"{res_type}/{res_id}"}]
                    obs_url = f"Observation/{obs['id']}"
                    if obs_url not in seen_urls:
                        entries.append({"fullUrl": obs_url, "resource": obs})
                        seen_urls.add(obs_url)
                    sup_url = f"{res_type}/{res_id}"
                    if sup_url not in seen_urls:
                        entries.append({"fullUrl": sup_url, "resource": res_entry})
                        seen_urls.add(sup_url)

    return entries


def _build_tuple_observation(
    pipe_value: str,
    link_id: str,
    question_text: str,
    patient_id: str,
    form_name: str,
    task_name: str,
) -> tuple[dict, dict | None]:
    """Parse a pipe-delimited CQL return value and build Observation + supporting resource.

    Expected format (mirrors v0 create_linked_results):
      "{date}|{system}|{code}|{display}|{value}[|{unit}]"

    The task_name prefix determines supporting resource type
    (e.g. "Condition_", "Observation_", "MedicationStatement_", etc.)
    """
    parts = pipe_value.split("|")
    effective_dt = parts[0] if parts else datetime.now(timezone.utc).isoformat()
    if len(effective_dt) == 19:
        effective_dt += "Z"

    res_id = str(uuid.uuid4())
    obs = _obs_base(link_id, question_text, patient_id, form_name)
    obs["effectiveDateTime"] = effective_dt
    obs["focus"] = [{"reference": f"Unknown/{res_id}"}]  # overridden below per type

    supporting: dict | None = None

    def _coding():
        return [{"system": parts[1] if len(parts) > 1 else "", "code": parts[2] if len(parts) > 2 else "", "display": parts[3] if len(parts) > 3 else ""}]

    task_upper = task_name.upper()
    if "CONDITION" in task_upper:
        supporting = {
            "resourceType": "Condition",
            "id": res_id,
            "identifier": [{"system": deploy_url, "value": f"Condition/{res_id}"}],
            "code": {"coding": _coding()},
            "onsetDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        obs["focus"] = [{"reference": f"Condition/{res_id}"}]
    elif "MEDICATION" in task_upper and "REQUEST" in task_upper:
        supporting = {
            "resourceType": "MedicationRequest",
            "id": res_id,
            "identifier": [{"system": deploy_url, "value": f"MedicationRequest/{res_id}"}],
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {"coding": _coding()},
            "authoredOn": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        obs["focus"] = [{"reference": f"MedicationRequest/{res_id}"}]
    elif "MEDICATION" in task_upper:
        supporting = {
            "resourceType": "MedicationStatement",
            "id": res_id,
            "identifier": [{"system": deploy_url, "value": f"MedicationStatement/{res_id}"}],
            "status": "active",
            "medicationCodeableConcept": {"coding": _coding()},
            "effectiveDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        obs["focus"] = [{"reference": f"MedicationStatement/{res_id}"}]
    elif "PROCEDURE" in task_upper:
        supporting = {
            "resourceType": "Procedure",
            "id": res_id,
            "identifier": [{"system": deploy_url, "value": f"Procedure/{res_id}"}],
            "code": {"coding": _coding()},
            "performedDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        obs["focus"] = [{"reference": f"Procedure/{res_id}"}]
    elif "OBSERVATION" in task_upper:
        supporting = {
            "resourceType": "Observation",
            "id": res_id,
            "identifier": [{"system": deploy_url, "value": f"Observation/{res_id}"}],
            "status": "final",
            "code": {"coding": _coding()},
            "effectiveDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        if len(parts) > 4:
            supporting["valueString"] = parts[4]
        obs["focus"] = [{"reference": f"Observation/{res_id}"}]
    else:
        # Unknown type — fall back to valueString on the answer Observation
        obs.pop("focus", None)
        obs["valueString"] = pipe_value

    return obs, supporting


def _build_llm_observations(
    llm_results: list[LlmResult],
    questionnaire: dict,
    form_name: str,
    patient_id: str,
) -> list[dict]:
    """Walk Questionnaire items, match unstructuredTask to LLM results, build Observations."""
    entries: list[dict] = []
    seen_urls: set[str] = set()

    llm_by_path: dict[str, LlmResult] = {r.prompt_path: r for r in llm_results}

    for group in questionnaire.get("item", []):
        for question in group.get("item", []):
            link_id = question.get("linkId", "")
            question_text = question.get("text", "")
            prompt_path = _get_item_task(question, _UNSTRUCTURED_TASK_URL)
            if not prompt_path:
                continue

            llm_result = llm_by_path.get(prompt_path)
            if not llm_result:
                logger.debug(f"No LLM result for prompt '{prompt_path}', skipping {link_id}")
                continue

            for doc_result in llm_result.document_results:
                if doc_result.error or not doc_result.response:
                    continue

                obs = _obs_base(link_id, question_text, patient_id, form_name)
                obs["effectiveDateTime"] = doc_result.doc_date
                obs["focus"] = [{"reference": f"DocumentReference/{doc_result.doc_id}"}]
                components = _build_llm_components(doc_result.response)
                if components is not None:
                    obs["component"] = components
                else:
                    obs["valueString"] = doc_result.response

                obs_url = f"Observation/{obs['id']}"
                # Deduplicate on focus + valueString (mirrors v0 NLPQL dedup)
                dedup_key = f"{doc_result.doc_id}::{doc_result.response}"
                if dedup_key not in seen_urls:
                    entries.append({"fullUrl": obs_url, "resource": obs})
                    seen_urls.add(dedup_key)

    return entries


def _summarize_llm_result(llm_result: LlmResult) -> tuple[str, dict]:
    has_success = any(doc_result.response for doc_result in llm_result.document_results if not doc_result.error)
    errors = [doc_result.error for doc_result in llm_result.document_results if doc_result.error]

    if has_success:
        return "complete", asdict(llm_result)
    if errors:
        return "error", {"message": "llm execution produced no usable response", "errors": errors, "document_results": [asdict(doc) for doc in llm_result.document_results]}
    return "skipped", {"message": "llm execution produced no response", "document_results": [asdict(doc) for doc in llm_result.document_results]}


def _create_status_observation(overall_status: str) -> dict:
    """Create a status Observation (mirrors v0 create_results_status_observation)."""
    status_code = "complete" if overall_status == "complete" else "in-progress"
    return {
        "resourceType": "Observation",
        "id": "status-observation",
        "status": overall_status,
        "code": {"coding": [{"code": "result-status"}]},
        "valueCodeableConcept": {
            "coding": [{"code": status_code}],
            "text": f"Batch job status: {overall_status}",
        },
    }


async def _fetch_patient_resource(patient_id: str) -> dict | None:
    """Fetch the Patient resource from the external FHIR server."""
    assert external_fhir_server_url
    url = f"{external_fhir_server_url.rstrip('/')}/Patient/{patient_id}"
    headers = {"Accept": "application/fhir+json"}
    if external_fhir_server_auth:
        headers["Authorization"] = external_fhir_server_auth
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.is_success:
            return resp.json()
    except Exception as exc:
        logger.warning(f"Could not fetch Patient/{patient_id}: {exc}")
    return None


def _append_unique_entries(entries: list[dict], new_entries: list[dict]) -> None:
    """Append Bundle entries while preserving stable resources across partial updates."""
    seen_urls = {entry.get("fullUrl") for entry in entries}
    for entry in new_entries:
        full_url = entry.get("fullUrl")
        if full_url not in seen_urls:
            entries.append(entry)
            seen_urls.add(full_url)


def _ordered_task_entries(task_names: list[str], entries_by_task: dict[str, list[dict]]) -> list[dict]:
    """Flatten completed task entries in configured order and deduplicate by fullUrl."""
    entries: list[dict] = []
    for task_name in task_names:
        _append_unique_entries(entries, entries_by_task.get(task_name, []))
    return entries


def _build_document_entries(documents: list[dict], patient_id: str, batch_id: str) -> list[dict]:
    """Build supporting DocumentReference entries for LLM result focus references."""
    entries: list[dict] = []
    for doc in documents:
        try:
            original = doc.get("resource")
            if original and original.get("resourceType") == "DocumentReference":
                doc_id = original.get("id", doc.get("id"))
                if not doc_id:
                    continue
                entries.append({"fullUrl": f"DocumentReference/{doc_id}", "resource": original})
                continue

            doc_id = doc.get("id")
            if not doc_id:
                continue
            supporting_doc: dict = {
                "resourceType": "DocumentReference",
                "id": doc_id,
                "status": "current",
                "type": {"text": doc.get("type", "Unknown")},
                "subject": {"reference": f"Patient/{patient_id}"},
                "date": doc.get("date", ""),
            }
            if doc.get("text"):
                try:
                    encoded = base64.b64encode(doc["text"].encode("utf-8")).decode("ascii")
                    supporting_doc["content"] = [{"attachment": {"contentType": "text/plain", "data": encoded}}]
                except (UnicodeEncodeError, TypeError) as exc:
                    logger.warning(f"[batch={batch_id}] Failed to base64-encode text for DocumentReference/{doc_id}: {exc}")

            entries.append({"fullUrl": f"DocumentReference/{doc_id}", "resource": supporting_doc})
        except Exception as exc:
            logger.warning(f"[batch={batch_id}] Skipping malformed document entry (id={doc.get('id', 'unknown')}): {exc}")

    return entries


def _build_result_bundle(
    patient_resource: dict | None,
    cql_entries: list[dict],
    llm_entries: list[dict],
    overall_status: str,
    bundle_id: str | None = None,
) -> dict:
    """Assemble a partial or final result FHIR Bundle."""
    status_obs = _create_status_observation(overall_status)
    all_entries: list[dict] = [{"fullUrl": "Observation/status-observation", "resource": status_obs}]
    if patient_resource:
        pid = patient_resource.get("id", "unknown")
        all_entries.append({"fullUrl": f"Patient/{pid}", "resource": patient_resource})

    all_entries.extend(cql_entries)
    all_entries.extend(llm_entries)

    return {
        "resourceType": "Bundle",
        "id": bundle_id or str(uuid.uuid4()),
        "type": "collection",
        "total": len(all_entries),
        "entry": all_entries,
    }


# Main orchestrator
async def run_batch_job(
    batch_id: str,
    patient_id: str,
    job_package: str,
    questionnaire_id: str,
    job_package_version: str | None = None,
    requested_jobs: list[str] | None = None,
) -> None:
    """Run the full CQL + LLM pipeline for a batch job.

    Called as a FastAPI BackgroundTask after POST /batchjob writes the DB record.
    Updates batch_jobs_v1 status to 'running' then 'complete' (or 'error').
    """
    logger.info(f"[batch={batch_id}] Starting batch job for Patient/{patient_id}, pkg={job_package}, jobs={requested_jobs or 'all'}")
    update_batch_job_status(batch_id, "running")

    task_results: list[dict] = []
    job_ids_by_task: dict[tuple[str, str], str] = {}
    partial_bundle_persisted = False

    try:
        # 1. Fetch Questionnaire from HAPI FHIR
        questionnaire_data = await fhir_get("Questionnaire", questionnaire_id)

        if questionnaire_data.get("resourceType") == "OperationOutcome":
            raise RuntimeError(f"Could not fetch Questionnaire/{questionnaire_id}: {questionnaire_data}")

        questionnaire = cast(dict[str, Any], questionnaire_data)

        form_name = questionnaire.get("name", job_package)
        logger.info(f"[batch={batch_id}] Loaded Questionnaire '{form_name}'")

        # 2. Extract task lists
        cql_names, prompt_paths = _extract_job_lists(questionnaire)
        cql_names, prompt_paths = _filter_requested_jobs(cql_names, prompt_paths, requested_jobs)
        logger.info(f"[batch={batch_id}] CQL libs={cql_names}, LLM prompts={prompt_paths}")

        for library_name in cql_names:
            task_job_id = str(uuid.uuid4())
            if create_job(task_job_id, batch_id, patient_id, job_package, library_name, "structured", status="running"):
                job_ids_by_task[("structured", library_name)] = task_job_id
        for prompt_path in prompt_paths:
            task_job_id = str(uuid.uuid4())
            if create_job(task_job_id, batch_id, patient_id, job_package, prompt_path, "unstructured", status="running"):
                job_ids_by_task[("unstructured", prompt_path)] = task_job_id

        # 3. Concurrently load prompts and fetch patient documents when LLM jobs were requested.
        prompts = documents = []
        if prompt_paths:
            prompts, documents = await asyncio.gather(
                load_prompts(prompt_paths),
                fetch_patient_documents(patient_id),
            )
            if not documents:
                logger.info(f"[batch={batch_id}] No DocumentReferences found — skipping LLM execution")
                for path in prompt_paths:
                    job_id = job_ids_by_task.get(("unstructured", path))
                    if job_id:
                        update_job_result(job_id, "skipped", {"message": "skipped — no supporting documents"})
                    task_results.append(
                        {
                            "task_name": path,
                            "task_type": "unstructured",
                            "status": "skipped",
                            "result": "skipped — no supporting documents",
                        }
                    )
            elif not use_llm:
                logger.info(f"[batch={batch_id}] LiteLLM is not configured — skipping LLM execution")
                for path in prompt_paths:
                    job_id = job_ids_by_task.get(("unstructured", path))
                    if job_id:
                        update_job_result(job_id, "skipped", {"message": "skipped — llm not configured"})
                    task_results.append(
                        {
                            "task_name": path,
                            "task_type": "unstructured",
                            "status": "skipped",
                            "result": "skipped — llm not configured",
                        }
                    )

        # 4. Persist an initial partial Bundle, then checkpoint it as each task finishes.
        bundle_id = str(uuid.uuid4())
        cql_entries_by_name: dict[str, list[dict]] = {}
        llm_entries_by_path: dict[str, list[dict]] = {}
        document_entries = _build_document_entries(documents, patient_id, batch_id)

        def _current_result_entries() -> tuple[list[dict], list[dict]]:
            return (
                _ordered_task_entries(cql_names, cql_entries_by_name),
                _ordered_task_entries(prompt_paths, llm_entries_by_path),
            )

        def _persist_partial_bundle() -> None:
            nonlocal partial_bundle_persisted
            current_cql_entries, current_llm_entries = _current_result_entries()
            partial_bundle = _build_result_bundle(
                None,
                current_cql_entries,
                current_llm_entries + document_entries,
                "preliminary",
                bundle_id,
            )
            update_batch_job_result(batch_id, partial_bundle)
            partial_bundle_persisted = True

        _persist_partial_bundle()

        snapshot_lock = asyncio.Lock()

        async def _record_cql_result(cql_result: CqlResult) -> None:
            async with snapshot_lock:
                task_name = cql_result.library_name
                status = "error" if cql_result.error else "complete"
                job_id = job_ids_by_task.get(("structured", task_name))
                if job_id:
                    update_job_result(job_id, status, {"error": cql_result.error, "results": cql_result.results})
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_type": "structured",
                        "status": status,
                        "error": cql_result.error,
                    }
                )
                cql_entries_by_name[task_name] = _build_cql_observations([cql_result], questionnaire, form_name, patient_id)
                _persist_partial_bundle()

        async def _record_llm_result(llm_result: LlmResult) -> None:
            async with snapshot_lock:
                task_name = llm_result.prompt_path
                status, result_payload = _summarize_llm_result(llm_result)
                job_id = job_ids_by_task.get(("unstructured", task_name))
                if job_id:
                    update_job_result(job_id, status, result_payload)
                task_results.append({"task_name": task_name, "task_type": "unstructured", "status": status})
                llm_entries_by_path[task_name] = _build_llm_observations([llm_result], questionnaire, form_name, patient_id)
                _persist_partial_bundle()

        async def _empty() -> list:
            return []

        cql_task = run_cql_libraries(cql_names, patient_id, on_result=_record_cql_result) if cql_names else _empty()
        llm_task = run_all_prompts(prompts, documents, on_result=_record_llm_result) if (prompt_paths and documents and use_llm) else _empty()
        cql_results, llm_results = await asyncio.gather(cql_task, llm_task)

        completed_cql_names = {result.library_name for result in cql_results}
        for library_name in cql_names:
            if library_name in completed_cql_names:
                continue
            logger.warning(f"[batch={batch_id}] No CQL result returned for library '{library_name}'")
            job_id = job_ids_by_task.get(("structured", library_name))
            if job_id:
                update_job_result(job_id, "error", {"message": "no cql result returned for library"})
            task_results.append({"task_name": library_name, "task_type": "structured", "status": "error"})
            _persist_partial_bundle()

        if prompt_paths and documents and use_llm:
            completed_llm_paths = {result.prompt_path for result in llm_results}
            for prompt_path in prompt_paths:
                if prompt_path in completed_llm_paths:
                    continue
                logger.warning(f"[batch={batch_id}] No LLM result returned for prompt '{prompt_path}'")
                job_id = job_ids_by_task.get(("unstructured", prompt_path))
                if job_id:
                    update_job_result(job_id, "error", {"message": "no llm result returned for prompt"})
                task_results.append({"task_name": prompt_path, "task_type": "unstructured", "status": "error"})
                _persist_partial_bundle()

        # 5. Fetch the Patient resource and assemble the final Bundle.
        patient_resource = await _fetch_patient_resource(patient_id)
        all_errors = [task for task in task_results if task.get("status") == "error"]
        overall_status = "complete" if not all_errors else "preliminary"
        final_cql_entries, final_llm_entries = _current_result_entries()
        result_bundle = _build_result_bundle(
            patient_resource,
            final_cql_entries,
            final_llm_entries + document_entries,
            overall_status,
            bundle_id,
        )

        # 6. Persist the final result.
        update_batch_job_status(batch_id, "complete", result_bundle)
        logger.info(f"[batch={batch_id}] Completed. Bundle entries: {result_bundle['total']}")

    except Exception as exc:
        logger.exception(f"[batch={batch_id}] Unhandled error in run_batch_job: {exc}")
        error_bundle = make_operation_outcome("exception", str(exc))
        for task_job_id in job_ids_by_task.values():
            update_job_result(task_job_id, "error", {"message": str(exc)})
        if partial_bundle_persisted:
            update_batch_job_status(batch_id, "error")
        else:
            update_batch_job_status(batch_id, "error", error_bundle)
