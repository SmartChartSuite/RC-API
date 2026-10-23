"""Read Questionnaire job-package extensions and select requested jobs.

This module owns Questionnaire extension constants, job-list extraction, requested-job filtering,
and recursive item traversal for the batch execution pipeline. It is pure representation and
selection logic; it does not execute CQL or LLM work, build result resources, or persist state.
Those responsibilities belong to ``job_orchestrator``, ``result_bundle``, the executors, and
the ``job_state`` package.
"""

from collections.abc import Iterator
from pathlib import Path

# Questionnaire extension URLs
_CQL_JOB_LIST_URL = "http://gtri.gatech.edu/fakeFormIg/structured-form-job-list"
_LLM_JOB_LIST_URL = "http://gtri.gatech.edu/fakeFormIg/unstructured-form-job-list"
_STRUCTURED_TASK_URL = "http://gtri.gatech.edu/fakeFormIg/structuredTask"
_UNSTRUCTURED_TASK_URL = "http://gtri.gatech.edu/fakeFormIg/unstructuredTask"
_UNSTRUCTURED_COMPONENT_SYSTEM = "http://gtri.gatech.edu/fakeFormIg/unstructured-answer-type-label"


# Questionnaire parsing
def extract_job_lists(questionnaire: dict) -> tuple[list[str], list[str]]:
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


def filter_requested_jobs(cql_names: list[str], prompt_paths: list[str], requested_jobs: list[str] | None) -> tuple[list[str], list[str]]:
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


def get_item_task(item: dict, extension_url: str) -> str | None:
    for ext in item.get("extension", []):
        if ext.get("url") == extension_url:
            return ext.get("valueString")
    return None


def iter_questionnaire_items(items: list | None) -> Iterator[dict]:
    """Yield every Questionnaire item depth-first, regardless of nesting level."""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        yield item
        yield from iter_questionnaire_items(item.get("item"))
