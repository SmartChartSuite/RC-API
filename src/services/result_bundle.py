"""Assemble document entries and the result Bundle for a batch attempt.

This module owns status Observations, deterministic task/document ordering, DocumentReference
entry sanitization, and partial/final Bundle assembly. It performs no fetching, execution, or
persistence; those responsibilities belong to ``job_orchestrator``, ``fhir_context``, the
executors, and ``job_state``. Document text encoding remains delegated to ``src.util.documents``.
"""

from loguru import logger

from src.services.result_observations import stable_id
from src.util.documents import add_document_text_content


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


def append_unique_entries(entries: list[dict], new_entries: list[dict]) -> None:
    """Append Bundle entries while preserving stable resources across partial updates."""
    seen_urls = {entry.get("fullUrl") for entry in entries}
    for entry in new_entries:
        full_url = entry.get("fullUrl")
        if full_url not in seen_urls:
            entries.append(entry)
            seen_urls.add(full_url)


def ordered_task_entries(task_names: list[str], entries_by_task: dict[str, list[dict]]) -> list[dict]:
    """Flatten completed task entries in configured order and deduplicate by fullUrl."""
    entries: list[dict] = []
    for task_name in task_names:
        append_unique_entries(entries, entries_by_task.get(task_name, []))
    return entries


def build_document_entries(documents: list[dict], patient_id: str, batch_id: str) -> list[dict]:
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
                    add_document_text_content(supporting_doc, doc["text"])
                except (UnicodeEncodeError, TypeError) as exc:
                    logger.warning(f"[batch={batch_id}] Failed to base64-encode text for DocumentReference/{doc_id}: {exc}")

            entries.append({"fullUrl": f"DocumentReference/{doc_id}", "resource": supporting_doc})
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[batch={batch_id}] Skipping malformed document entry (id={doc.get('id', 'unknown')}): {exc}")

    return entries


def without_document_content(entry: dict) -> dict:
    """Return a stored Bundle entry with DocumentReference content removed."""
    resource = entry.get("resource")
    if not isinstance(resource, dict) or resource.get("resourceType") != "DocumentReference" or "content" not in resource:
        return entry
    return {**entry, "resource": {key: value for key, value in resource.items() if key != "content"}}


def ordered_document_set(fetched_documents: list[dict], stored_documents: list[dict]) -> list[dict]:
    """Order current fetched documents first, then stored-only documents by date and ID."""
    ordered: list[dict] = []
    seen_ids: set[str] = set()
    for document in fetched_documents:
        doc_id = document.get("id")
        if isinstance(doc_id, str) and doc_id and doc_id not in seen_ids:
            ordered.append(document)
            seen_ids.add(doc_id)

    stored_only = [document for document in stored_documents if isinstance(document.get("id"), str) and document["id"] not in seen_ids]
    stored_only.sort(key=lambda document: (str(document.get("date") or ""), str(document.get("id") or "")))
    ordered.extend(stored_only)
    return ordered


def build_result_bundle(
    patient_resource: dict | None,
    cql_entries: list[dict],
    llm_entries: list[dict],
    overall_status: str,
    bundle_id: str | None = None,
    *,
    batch_id: str = "",
) -> dict:
    """Assemble a partial or final result FHIR Bundle."""
    status_obs = _create_status_observation(overall_status)
    all_entries: list[dict] = [{"fullUrl": "Observation/status-observation", "resource": status_obs}]
    if patient_resource:
        pid = patient_resource.get("id", "unknown")
        all_entries.append({"fullUrl": f"Patient/{pid}", "resource": patient_resource})

    all_entries.extend(without_document_content(entry) for entry in cql_entries)
    all_entries.extend(without_document_content(entry) for entry in llm_entries)

    return {
        "resourceType": "Bundle",
        "id": bundle_id or stable_id(batch_id, "bundle"),
        "type": "collection",
        "total": len(all_entries),
        "entry": all_entries,
    }
