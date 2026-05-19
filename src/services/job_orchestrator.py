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
import uuid
from datetime import datetime, timezone

import httpx
from loguru import logger

from src.services.cql_executor import CqlResult, run_cql_libraries
from src.services.errorhandler import make_operation_outcome
from src.services.fhir_context import fetch_patient_documents
from src.services.fhir_proxy import fhir_get
from src.services.job_state import (
    create_job,
    update_batch_job_status,
    update_job_complete,
)
from src.services.llm_executor import LlmResult, run_all_prompts
from src.services.prompt_loader import load_prompts
from src.util.settings import deploy_url, external_fhir_server_auth, external_fhir_server_url, use_llm

# ── Questionnaire extension URLs ──────────────────────────────────────────────
_CQL_JOB_LIST_URL = "http://gtri.gatech.edu/fakeFormIg/structured-form-job-list"
_LLM_JOB_LIST_URL = "http://gtri.gatech.edu/fakeFormIg/unstructured-form-job-list"
_STRUCTURED_TASK_URL = "http://gtri.gatech.edu/fakeFormIg/structuredTask"
_UNSTRUCTURED_TASK_URL = "http://gtri.gatech.edu/fakeFormIg/unstructuredTask"


# ── Questionnaire parsing ─────────────────────────────────────────────────────


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


def _get_item_task(item: dict, extension_url: str) -> str | None:
    for ext in item.get("extension", []):
        if ext.get("url") == extension_url:
            return ext.get("valueString")
    return None


# ── Observation builders ──────────────────────────────────────────────────────


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
                obs["valueString"] = doc_result.response

                obs_url = f"Observation/{obs['id']}"
                # Deduplicate on focus + valueString (mirrors v0 NLPQL dedup)
                dedup_key = f"{doc_result.doc_id}::{doc_result.response}"
                if dedup_key not in seen_urls:
                    entries.append({"fullUrl": obs_url, "resource": obs})
                    seen_urls.add(dedup_key)

    return entries


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


def _build_result_bundle(
    patient_resource: dict | None,
    cql_entries: list[dict],
    llm_entries: list[dict],
    overall_status: str,
) -> dict:
    """Assemble the final result FHIR Bundle."""
    status_obs = _create_status_observation(overall_status)
    all_entries: list[dict] = [{"fullUrl": "Observation/status-observation", "resource": status_obs}]
    if patient_resource:
        pid = patient_resource.get("id", "unknown")
        all_entries.append({"fullUrl": f"Patient/{pid}", "resource": patient_resource})

    all_entries.extend(cql_entries)
    all_entries.extend(llm_entries)

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection",
        "total": len(all_entries),
        "entry": all_entries,
    }


# ── Main orchestrator ─────────────────────────────────────────────────────────


async def run_batch_job(
    batch_id: str,
    job_id: str,
    patient_id: str,
    job_package: str,
    job_package_version: str | None = None,
) -> None:
    """Run the full CQL + LLM pipeline for a batch job.

    Called as a FastAPI BackgroundTask after POST /batchjob writes the DB record.
    Updates batch_jobs_v1 status to 'running' then 'complete' (or 'error').
    """
    logger.info(f"[batch={batch_id}] Starting batch job for Patient/{patient_id}, pkg={job_package}")
    update_batch_job_status(batch_id, "running")
    create_job(job_id, batch_id, patient_id, job_package)

    task_results: list[dict] = []

    try:
        # 1. Fetch Questionnaire from HAPI FHIR
        params: dict = {"name": job_package}
        if job_package_version:
            params["version"] = job_package_version
        questionnaire_bundle = await fhir_get("Questionnaire", params=params)

        if questionnaire_bundle.get("resourceType") == "OperationOutcome":
            raise RuntimeError(f"Could not fetch Questionnaire: {questionnaire_bundle}")

        entries = questionnaire_bundle.get("entry", [])
        if not entries:
            raise RuntimeError(f"No Questionnaire found with name='{job_package}'")
        questionnaire = entries[0]["resource"]
        form_name = questionnaire.get("name", job_package)
        logger.info(f"[batch={batch_id}] Loaded Questionnaire '{form_name}'")

        # 2. Extract task lists
        cql_names, prompt_paths = _extract_job_lists(questionnaire)
        logger.info(f"[batch={batch_id}] CQL libs={cql_names}, LLM prompts={prompt_paths}")

        # 3. Concurrently: load prompts + fetch patient documents
        prompts, documents = await asyncio.gather(
            load_prompts(prompt_paths),
            fetch_patient_documents(patient_id),
        )

        if not documents and prompt_paths:
            logger.info(f"[batch={batch_id}] No DocumentReferences found — skipping LLM execution")
            for path in prompt_paths:
                task_results.append(
                    {
                        "task_name": path,
                        "task_type": "unstructured",
                        "status": "skipped",
                        "result": "skipped — no supporting documents",
                    }
                )

        # 4. Concurrently: CQL + LLM
        async def _empty() -> list:
            return []

        cql_task = run_cql_libraries(cql_names, patient_id) if cql_names else _empty()
        llm_task = run_all_prompts(prompts, documents) if (documents and use_llm and prompt_paths) else _empty()

        cql_results, llm_results = await asyncio.gather(cql_task, llm_task)

        # Record CQL task results
        for cql_res in cql_results:
            task_results.append(
                {
                    "task_name": cql_res.library_name,
                    "task_type": "structured",
                    "status": "error" if cql_res.error else "complete",
                    "error": cql_res.error,
                }
            )

        # Record LLM task results
        for llm_res in llm_results:
            task_results.append(
                {
                    "task_name": llm_res.prompt_path,
                    "task_type": "unstructured",
                    "status": "complete",
                }
            )

        # 5. Fetch Patient resource for Bundle
        patient_resource = await _fetch_patient_resource(patient_id)

        # 6. Build Observations for structured + unstructured results
        cql_entries = _build_cql_observations(cql_results, questionnaire, form_name, patient_id)
        llm_entries = _build_llm_observations(llm_results, questionnaire, form_name, patient_id)

        # 7. Assemble result Bundle
        all_errors = [t for t in task_results if t.get("status") == "error"]
        overall_status = "complete" if not all_errors else "preliminary"
        result_bundle = _build_result_bundle(patient_resource, cql_entries, llm_entries, overall_status)

        # 8. Persist
        update_job_complete(job_id, task_results, result_bundle)
        update_batch_job_status(batch_id, "complete", result_bundle)
        logger.info(f"[batch={batch_id}] Completed. Bundle entries: {result_bundle['total']}")

    except Exception as exc:
        logger.exception(f"[batch={batch_id}] Unhandled error in run_batch_job: {exc}")
        error_bundle = make_operation_outcome("exception", str(exc))
        update_job_complete(job_id, task_results)
        update_batch_job_status(batch_id, "error", error_bundle)
