"""Build CQL- and LLM-derived FHIR Observations for batch results.

This module owns deterministic result IDs, Observation construction, JSON component extraction,
and restoration/summarization of persisted child results. It is pure representation logic and
does not fetch documents, execute jobs, assemble the enclosing Bundle, or persist state; those
responsibilities belong to ``fhir_context``, the executors, ``result_bundle``, and the ``job_state`` package.
It retains the existing ``settings.deploy_url`` reads used by generated resources.
"""

import json
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from loguru import logger

from src.services.cql_executor import CqlResult
from src.services.job_package import (
    _STRUCTURED_TASK_URL,
    _UNSTRUCTURED_COMPONENT_SYSTEM,
    _UNSTRUCTURED_TASK_URL,
    get_item_task,
    iter_questionnaire_items,
)
from src.services.llm_executor import LlmDocumentResult, LlmResult
from src.util.settings import settings

# Stable namespace for result resources; changing it would change every generated result ID.
_RESULT_RESOURCE_NAMESPACE = uuid.UUID("2f1c5e7a-1d84-5f36-8c0d-f1bdfb9412cb")


def stable_id(batch_id: str, *parts: object) -> str:
    """Return a deterministic UUID for a result resource within one batch."""
    name = "|".join(json.dumps(part, sort_keys=True, separators=(",", ":"), default=str) for part in (batch_id, *parts))
    return str(uuid.uuid5(_RESULT_RESOURCE_NAMESPACE, name))


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
def _obs_base(
    batch_id: str,
    id_parts: tuple[object, ...],
    link_id: str,
    question_text: str,
    patient_id: str,
    form_name: str,
) -> dict:
    obs_id = stable_id(batch_id, *id_parts)
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "identifier": [{"system": settings.deploy_url, "value": f"Observation/{obs_id}"}],
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


def _infer_cql_value_type(raw_value: object) -> str | None:
    """Infer the compatible FHIR value type for a legacy persisted CQL value."""
    if isinstance(raw_value, bool):
        return "Boolean"
    if isinstance(raw_value, int):
        return "Integer"
    if isinstance(raw_value, float):
        return "Decimal"
    if isinstance(raw_value, str):
        return "String"
    return None


def _set_cql_observation_value(obs: dict, raw_value: object, value_type: str | None, task_name: str) -> None:
    """Set an Observation value[x] for a typed CQL entry, using JSON for unsupported types."""
    direct_types = {"Boolean", "Integer", "DateTime", "Time", "Period", "Quantity", "CodeableConcept", "Range", "Ratio", "SampledData"}
    if value_type in direct_types:
        obs[f"value{value_type}"] = raw_value
    elif value_type == "Decimal":
        obs["valueQuantity"] = {"value": raw_value}
    elif value_type == "Coding":
        obs["valueCodeableConcept"] = {"coding": [raw_value]}
    elif value_type == "Code":
        obs["valueCodeableConcept"] = {"coding": [{"code": raw_value}]}
    elif value_type == "Date":
        obs["valueDateTime"] = raw_value
    elif value_type == "Reference":
        obs["focus"] = [raw_value if isinstance(raw_value, dict) else {"reference": str(raw_value)}]
    else:
        obs["valueString"] = json.dumps(raw_value, separators=(",", ":"), sort_keys=True)
        logger.debug(f"Unsupported CQL value type '{value_type}' for task '{task_name}'; using valueString")


def build_cql_observations(
    cql_results: list[CqlResult],
    questionnaire: dict,
    form_name: str,
    patient_id: str,
    batch_id: str = "",
) -> list[dict]:
    """Walk Questionnaire items, match structuredTask to CQL results, build Observations."""
    entries: list[dict] = []
    seen_urls: set[str] = set()

    # Index CQL results by library name for O(1) lookup
    cql_by_lib: dict[str, CqlResult] = {r.library_name: r for r in cql_results if not r.error}

    for question in iter_questionnaire_items(questionnaire.get("item")):
        link_id = question.get("linkId", "")
        question_text = question.get("text", "")
        task_ext = get_item_task(question, _STRUCTURED_TASK_URL)
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

        for entry_index, res_entry in enumerate(result_entries):
            if not isinstance(res_entry, dict):
                logger.debug(f"Skipping malformed CQL result entry for task '{task_name}'")
                continue

            # Tuple result (pipe-delimited value string from CQL)
            raw_value = res_entry.get("value")
            stored_type = res_entry.get("type")
            value_type = stored_type if isinstance(stored_type, str) else _infer_cql_value_type(raw_value)
            answer_key = (
                "cql",
                library_name,
                task_name,
                link_id,
                entry_index,
                value_type or "",
                res_entry if res_entry.get("resourceType") else raw_value,
            )
            if value_type == "String" and isinstance(raw_value, str) and "|" in raw_value:
                obs, supporting = build_tuple_observation(
                    raw_value,
                    link_id,
                    question_text,
                    patient_id,
                    form_name,
                    task_name,
                    batch_id,
                    answer_key=answer_key,
                )
                obs_url = f"Observation/{obs['id']}"
                if obs_url not in seen_urls:
                    entries.append({"fullUrl": obs_url, "resource": obs})
                    seen_urls.add(obs_url)
                if supporting:
                    sup_url = f"{supporting['resourceType']}/{supporting['id']}"
                    if sup_url not in seen_urls:
                        entries.append({"fullUrl": sup_url, "resource": supporting})
                        seen_urls.add(sup_url)
            elif value_type == "String" and isinstance(raw_value, str):
                # Simple scalar valueString observation
                obs = _obs_base(batch_id, answer_key + ("observation",), link_id, question_text, patient_id, form_name)
                obs["valueString"] = raw_value
                obs_url = f"Observation/{obs['id']}"
                if obs_url not in seen_urls:
                    entries.append({"fullUrl": obs_url, "resource": obs})
                    seen_urls.add(obs_url)
            elif res_entry.get("resourceType"):
                # FHIR resource result — reference it via focus
                res_type = str(res_entry["resourceType"])
                upstream_id = res_entry.get("id")
                if upstream_id:
                    res_id = str(upstream_id)
                    supporting_resource = res_entry
                else:
                    res_id = stable_id(batch_id, "cql", "supporting", res_type, res_entry)
                    supporting_resource = {**res_entry, "id": res_id}
                obs = _obs_base(batch_id, answer_key + ("observation",), link_id, question_text, patient_id, form_name)
                obs["focus"] = [{"reference": f"{res_type}/{res_id}"}]
                obs_url = f"Observation/{obs['id']}"
                if obs_url not in seen_urls:
                    entries.append({"fullUrl": obs_url, "resource": obs})
                    seen_urls.add(obs_url)
                sup_url = f"{res_type}/{res_id}"
                if sup_url not in seen_urls:
                    entries.append({"fullUrl": sup_url, "resource": supporting_resource})
                    seen_urls.add(sup_url)
            else:
                obs = _obs_base(batch_id, answer_key + ("observation",), link_id, question_text, patient_id, form_name)
                _set_cql_observation_value(obs, raw_value, value_type, task_name)
                obs_url = f"Observation/{obs['id']}"
                if obs_url not in seen_urls:
                    entries.append({"fullUrl": obs_url, "resource": obs})
                    seen_urls.add(obs_url)

    return entries


def build_tuple_observation(
    pipe_value: str,
    link_id: str,
    question_text: str,
    patient_id: str,
    form_name: str,
    task_name: str,
    batch_id: str = "",
    *,
    answer_key: tuple[object, ...] | None = None,
) -> tuple[dict, dict | None]:
    """Parse a pipe-delimited CQL return value and build Observation + supporting resource.

    Expected format (mirrors v0 create_linked_results):
      "{date}|{system}|{code}|{display}|{value}[|{unit}]"

    The task_name prefix determines supporting resource type
    (e.g. "Condition_", "Observation_", "MedicationStatement_", etc.).
    """
    parts = pipe_value.split("|")
    effective_dt = parts[0] if parts else datetime.now(UTC).isoformat()
    if len(effective_dt) == 19:
        effective_dt += "Z"

    id_parts = answer_key or ("cql", task_name, link_id, 0, "String", pipe_value)
    obs = _obs_base(batch_id, id_parts + ("observation",), link_id, question_text, patient_id, form_name)
    obs["effectiveDateTime"] = effective_dt

    def _coding():
        return [{"system": parts[1] if len(parts) > 1 else "", "code": parts[2] if len(parts) > 2 else "", "display": parts[3] if len(parts) > 3 else ""}]

    task_upper = task_name.upper()
    if "CONDITION" in task_upper:
        resource_type = "Condition"
    elif "MEDICATION" in task_upper and "REQUEST" in task_upper:
        resource_type = "MedicationRequest"
    elif "MEDICATION" in task_upper:
        resource_type = "MedicationStatement"
    elif "PROCEDURE" in task_upper:
        resource_type = "Procedure"
    elif "OBSERVATION" in task_upper:
        resource_type = "Observation"
    else:
        resource_type = None

    if resource_type is None:
        # Unknown type — fall back to valueString on the answer Observation
        obs["valueString"] = pipe_value
        return obs, None

    res_id = stable_id(batch_id, *id_parts, "supporting", resource_type)
    if resource_type == "Condition":
        supporting = {
            "resourceType": resource_type,
            "id": res_id,
            "identifier": [{"system": settings.deploy_url, "value": f"{resource_type}/{res_id}"}],
            "code": {"coding": _coding()},
            "onsetDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
    elif resource_type == "MedicationRequest":
        supporting = {
            "resourceType": resource_type,
            "id": res_id,
            "identifier": [{"system": settings.deploy_url, "value": f"{resource_type}/{res_id}"}],
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {"coding": _coding()},
            "authoredOn": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
    elif resource_type == "MedicationStatement":
        supporting = {
            "resourceType": resource_type,
            "id": res_id,
            "identifier": [{"system": settings.deploy_url, "value": f"{resource_type}/{res_id}"}],
            "status": "active",
            "medicationCodeableConcept": {"coding": _coding()},
            "effectiveDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
    elif resource_type == "Procedure":
        supporting = {
            "resourceType": resource_type,
            "id": res_id,
            "identifier": [{"system": settings.deploy_url, "value": f"{resource_type}/{res_id}"}],
            "code": {"coding": _coding()},
            "performedDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
    else:
        supporting = {
            "resourceType": resource_type,
            "id": res_id,
            "identifier": [{"system": settings.deploy_url, "value": f"{resource_type}/{res_id}"}],
            "status": "final",
            "code": {"coding": _coding()},
            "effectiveDateTime": effective_dt,
            "subject": {"reference": f"Patient/{patient_id}"},
        }
        if len(parts) > 4:
            supporting["valueString"] = parts[4]

    obs["focus"] = [{"reference": f"{resource_type}/{res_id}"}]
    return obs, supporting


def build_llm_observations(
    llm_results: list[LlmResult],
    questionnaire: dict,
    form_name: str,
    patient_id: str,
    batch_id: str = "",
) -> list[dict]:
    """Walk Questionnaire items, match unstructuredTask to LLM results, build Observations."""
    entries: list[dict] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    llm_by_path: dict[str, LlmResult] = {r.prompt_path: r for r in llm_results}

    for question in iter_questionnaire_items(questionnaire.get("item")):
        link_id = question.get("linkId", "")
        question_text = question.get("text", "")
        prompt_path = get_item_task(question, _UNSTRUCTURED_TASK_URL)
        if not prompt_path:
            continue

        llm_result = llm_by_path.get(prompt_path)
        if not llm_result:
            logger.debug(f"No LLM result for prompt '{prompt_path}', skipping {link_id}")
            continue

        for doc_result in llm_result.document_results:
            if doc_result.error or not doc_result.response:
                continue

            dedup_key = (link_id, prompt_path, doc_result.doc_id, doc_result.response)
            obs = _obs_base(batch_id, ("llm", *dedup_key, "observation"), link_id, question_text, patient_id, form_name)
            obs["effectiveDateTime"] = doc_result.doc_date
            obs["focus"] = [{"reference": f"DocumentReference/{doc_result.doc_id}"}]
            components = _build_llm_components(doc_result.response)
            if components is not None:
                obs["component"] = components
            else:
                obs["valueString"] = doc_result.response

            obs_url = f"Observation/{obs['id']}"
            # Deduplicate per question on focus + value (mirrors v0 NLPQL dedup); the same
            # finding under two different questions is two distinct answers.
            if dedup_key not in seen_keys:
                entries.append({"fullUrl": obs_url, "resource": obs})
                seen_keys.add(dedup_key)

    return entries


def summarize_llm_result(llm_result: LlmResult) -> tuple[str, dict]:
    has_success = any(doc_result.response for doc_result in llm_result.document_results if not doc_result.error)
    errors = [doc_result.error for doc_result in llm_result.document_results if doc_result.error]

    if has_success:
        return "complete", asdict(llm_result)
    if errors:
        return "error", {"message": "llm execution produced no usable response", "errors": errors, "document_results": [asdict(doc) for doc in llm_result.document_results]}
    return "skipped", {"message": "llm execution produced no response", "document_results": [asdict(doc) for doc in llm_result.document_results]}


def cql_result_from_payload(library_name: str, patient_id: str, payload: dict | None) -> CqlResult | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, dict):
        return None
    error = payload.get("error")
    return CqlResult(
        library_name=library_name,
        patient_id=patient_id,
        results=results,
        error=error if isinstance(error, str) else None,
    )


def llm_result_from_payload(prompt_path: str, payload: dict | None) -> LlmResult | None:
    if not isinstance(payload, dict):
        return None
    raw_documents = payload.get("document_results")
    if not isinstance(raw_documents, list):
        return None
    documents: list[LlmDocumentResult] = []
    for raw in raw_documents:
        if not isinstance(raw, dict):
            return None
        try:
            documents.append(
                LlmDocumentResult(
                    doc_id=str(raw["doc_id"]),
                    doc_type=str(raw.get("doc_type", "Unknown")),
                    doc_date=str(raw.get("doc_date", "")),
                    response=str(raw.get("response", "")),
                    error=raw.get("error") if isinstance(raw.get("error"), str) else None,
                )
            )
        except KeyError:
            return None
    return LlmResult(prompt_path=prompt_path, document_results=documents)
