"""
test_integration.py — End-to-end tests requiring real external services.

These tests hit REAL instances of:
  - CQF Ruler (CQF_RULER_R4 env var)
  - External FHIR server (EXTERNAL_FHIR_SERVER_URL env var)
  - DB (DB_CONNECTION_STRING env var)

They are decorated with @pytest.mark.integration and are SKIPPED by default.

How to run:
  conda activate rcapi
  python -m pytest tests/test_integration.py -v -s -m integration

Input data comes from tests/fixtures/user_data.json:
  - "start_jobs_requests"   → list of Parameters POST bodies for POST /forms/start
  - "batch_jobs_requests"   → list of Parameters POST bodies for POST /smartchartui/batchjob

Logging strategy:
  Each test prints explicit step-by-step output (pytest -s) so that when an external service is broken you can see exactly which call failed, what the status code was, and what body was returned
  without having to re-run in debug mode.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from loguru import logger

from tests.conftest import load_user_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BATCH_JOB_POLL_INTERVAL_SECONDS = 10
BATCH_JOB_POLL_TIMEOUT_SECONDS = 300  # 5 minutes
BATCH_JOB_MIN_RESULT_ENTRIES = 10  # at least 10 entries (Patient + observations)

STATUS_OBS_FULL_URL = "Observation/status-observation"
STATUS_OBS_COMPLETE_CODE = "complete"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# Modules that bind cqfr4_fhir / external_fhir_server_url at import time.
# ALL of them must be patched so the app's outbound httpx calls reach real servers.
_CQFR4_TARGETS = [
    "src.util.settings.cqfr4_fhir",
    "src.routers.cql_router.cqfr4_fhir",
    "src.routers.nlpql_router.cqfr4_fhir",
    "src.routers.forms_router.cqfr4_fhir",
    "src.services.libraryhandler.cqfr4_fhir",
    "src.models.forms.cqfr4_fhir",
    "src.models.functions.cqfr4_fhir",
    "src.routers.smartchartui.internal_fhir_client.server_base",
]
_FHIR_SERVER_TARGETS = [
    "src.util.settings.external_fhir_server_url",
    "src.models.functions.external_fhir_server_url",
    "src.routers.smartchartui.external_fhir_client.server_base",
]
_FHIR_AUTH_TARGETS = [
    "src.util.settings.external_fhir_server_auth",
    "src.models.functions.external_fhir_server_auth",
]
_NLPAAS_TARGETS = [
    "src.util.settings.nlpaas_url",
    "src.services.libraryhandler.nlpaas_url",
    "src.util.git.nlpaas_url",
    "src.models.functions.nlpaas_url",
]

# ---------------------------------------------------------------------------
# Integration Questionnaire fixture path
# ---------------------------------------------------------------------------
_INTEGRATION_QUESTIONNAIRE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "fhir_integration_questionnaire.json")
_INTEGRATION_QUESTIONNAIRE_NAME = "SETNETInfantFollowUpIntegrationTesting"


# Shared state written by the setup fixture so tests can record batch job IDs
# that need to be cleaned up on teardown.
_batch_ids_to_cleanup: list[str] = []


def _load_env_file(path: str) -> dict[str, str]:
    """
    Parse a .env file and return a dict of key=value pairs.
    Ignores comment lines (#) and blank lines. No dependency on python-dotenv.
    """
    env: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


@pytest.fixture(scope="module")
def integration_client():
    """
    TestClient wired to REAL external services.

    Service URLs are read from .env in the repo root (gitignored).
    Every module-scope import of cqfr4_fhir / external_fhir_server_url is patched so outbound
    httpx calls reach the real servers instead of the localhost test defaults.

    Setup (before yield):
      - Upload fhir_integration_questionnaire.json to CQF Ruler so the batch job tests can
        reference it by name (SETNETInfantFollowUpIntegrationTesting).

    Teardown (after yield, while TestClient + patches are still active):
      - Delete the uploaded Questionnaire from CQF Ruler.
      - Delete any batch jobs recorded in _batch_ids_to_cleanup via DELETE /smartchartui/batchjob/{id}.

    Auto-skips if .env doesn't exist or URLs are still placeholders.
    """
    import contextlib
    import httpx as _httpx

    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    svc = _load_env_file(os.path.normpath(env_file))

    cqf_url = svc.get("CQF_RULER_R4", "")
    fhir_url = svc.get("EXTERNAL_FHIR_SERVER_URL", "")
    fhir_auth = svc.get("EXTERNAL_FHIR_SERVER_AUTH", "")
    nlpaas_url = svc.get("NLPAAS_URL", "")
    db_conn = svc.get("DB_CONNECTION_STRING", "")

    if not cqf_url or "REPLACE_ME" in cqf_url:
        pytest.skip(".env not found or CQF_RULER_R4 not set. Copy .env.example → .env and fill in real service URLs.")
    if not fhir_url or "REPLACE_ME" in fhir_url:
        pytest.skip(".env not found or EXTERNAL_FHIR_SERVER_URL not set. Copy .env.example → .env and fill in real service URLs.")

    if not cqf_url.endswith("/"):
        cqf_url += "/"
    if not fhir_url.endswith("/"):
        fhir_url += "/"
    if nlpaas_url and nlpaas_url.lower() != "false" and not nlpaas_url.endswith("/"):
        nlpaas_url += "/"
    if nlpaas_url.lower() == "false":
        nlpaas_url = ""

    logger.info(f"[INTEGRATION] CQF Ruler:      {cqf_url}")
    logger.info(f"[INTEGRATION] External FHIR:  {fhir_url}")
    if nlpaas_url:
        logger.info(f"[INTEGRATION] NLPaaS:         {nlpaas_url}")

    # ── Setup: upload the integration questionnaire to CQF Ruler ────────────
    with open(_INTEGRATION_QUESTIONNAIRE_PATH) as f:
        questionnaire_body = json.load(f)

    logger.info(f"\n[INTEGRATION SETUP] Uploading Questionnaire '{_INTEGRATION_QUESTIONNAIRE_NAME}' to CQF Ruler...")
    upload_resp = _httpx.post(
        f"{cqf_url}Questionnaire",
        json=questionnaire_body,
        headers={"Content-Type": "application/fhir+json"},
        timeout=30,
    )
    if upload_resp.status_code not in (200, 201):
        pytest.fail(f"[INTEGRATION SETUP] Failed to upload integration Questionnaire to CQF Ruler — status={upload_resp.status_code}, body={upload_resp.text[:500]}")

    questionnaire_server_id = upload_resp.json().get("id")
    logger.info(f"\n[INTEGRATION SETUP] Questionnaire uploaded — server ID: {questionnaire_server_id}")

    # Build URL patches — all modules that imported cqfr4_fhir / external_fhir_server_url at module scope
    url_patches = (
        [patch(t, cqf_url) for t in _CQFR4_TARGETS]
        + [patch(t, fhir_url) for t in _FHIR_SERVER_TARGETS]
        + [patch(t, fhir_auth) for t in _FHIR_AUTH_TARGETS]
        + [patch(t, nlpaas_url) for t in _NLPAAS_TARGETS]
        + [patch("src.util.git.clone_repo_to_temp_folder")]
        + [patch("src.routers.forms_router.init_jobs_array")]
    )

    if db_conn and "REPLACE_ME" not in db_conn:
        from sqlalchemy import create_engine as _ce

        real_engine = _ce(db_conn)
        logger.info(f"[INTEGRATION] DB: {db_conn.split('@')[-1]}")
        url_patches += [
            patch("src.util.databaseclient.db_engine", real_engine),
            patch("src.services.jobstate.db_engine", real_engine),
        ]
    else:
        logger.info("[INTEGRATION] DB: SQLite fallback (rcapi_test.sqlite)")

    with contextlib.ExitStack() as stack:
        for p in url_patches:
            stack.enter_context(p)

        from fastapi.testclient import TestClient

        from main import app

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

            # ── Teardown (TestClient + patches still active here) ────────────
            # Delete the uploaded Questionnaire from CQF Ruler
            if questionnaire_server_id:
                logger.info(f"\n[INTEGRATION TEARDOWN] Deleting Questionnaire {questionnaire_server_id} from CQF Ruler...")
                del_resp = _httpx.delete(f"{cqf_url}Questionnaire/{questionnaire_server_id}", timeout=15)
                logger.info(f"\n[INTEGRATION TEARDOWN] Questionnaire delete response: {del_resp.status_code}")

            # Delete batch jobs created during this test run via the (still-patched) TestClient
            for batch_id in _batch_ids_to_cleanup:
                logger.info(f"\n[INTEGRATION TEARDOWN] Deleting batch job {batch_id}...")
                del_resp = c.delete(f"/smartchartui/batchjob/{batch_id}")
                logger.info(f"\n[INTEGRATION TEARDOWN] Batch job {batch_id} delete response: {del_resp.status_code}")
            _batch_ids_to_cleanup.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _assert_not_error_operation_outcome(body: dict, context: str) -> None:
    """
    Fail with a human-readable message if body is an error OperationOutcome. Helps pinpoint which external service returned an error.
    """
    if body.get("resourceType") == "OperationOutcome":
        issues = body.get("issue", [])
        severities = [i.get("severity", "unknown") for i in issues]
        diagnostics = [i.get("diagnostics", i.get("details", {}).get("text", "no details")) for i in issues]
        if any(s in ("error", "fatal") for s in severities):
            pytest.fail(f"{context}: Received error OperationOutcome — severities={severities}, diagnostics={diagnostics}\nFull body: {body}")


def _extract_batch_id(response_body: dict) -> str:
    """
    Extract the batchId from the Parameters response body returned by POST /smartchartui/batchjob.

    The batchId lives at: parameter[?name=="batchId"].valueString
    """
    params = response_body.get("parameter", [])
    for p in params:
        if p.get("name") == "batchId":
            batch_id = p.get("valueString")
            if batch_id:
                return batch_id
    pytest.fail(f"Could not find batchId in POST /smartchartui/batchjob response.\nparameter list: {params}")


def _get_status_observation(results_body: dict) -> dict | None:
    """
    Return the status observation entry from a results bundle, or None if not yet present.
    """
    for entry in results_body.get("entry", []):
        if entry.get("fullUrl") == STATUS_OBS_FULL_URL:
            return entry.get("resource", {})
    return None


# ---------------------------------------------------------------------------
# Tests: POST /forms/start — synchronous job runner
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestStartJobsFullForm:
    """
    POST /forms/start (all jobs in a form, synchronous).

    Input: start_jobs_requests[0] from user_data.json.
    Asserts:
      - HTTP 200
      - Response is a Bundle (not an error OperationOutcome)
      - Bundle has at least 1 entry (the Patient at minimum)
    """

    def test_start_jobs_full_form_returns_bundle(self, integration_client):
        user_data = load_user_data()
        scenarios = user_data.get("start_jobs_requests", [])
        if not scenarios:
            pytest.skip("No start_jobs_requests entries in user_data.json")

        scenario = scenarios[0]
        post_body = {k: v for k, v in scenario.items() if not k.startswith("_") and k != "expected_output"}

        logger.info(
            f"\n[INTEGRATION] POST /forms/start — jobPackage={post_body.get('parameter', [{}])[1].get('valueString', '?')}, patientId={post_body.get('parameter', [{}])[0].get('valueString', '?')}"
        )
        logger.info("\n[INTEGRATION] Calling CQF Ruler + external FHIR server (synchronous — may take 30-90s)...")

        response = integration_client.post("/forms/start", json=post_body)

        logger.info(f"\n[INTEGRATION] Response status: {response.status_code}")

        assert response.status_code == 200, f"POST /forms/start returned {response.status_code}. Check that CQF Ruler ({post_body}) is accessible. Body: {response.text[:500]}"

        body = response.json()
        _assert_not_error_operation_outcome(body, "POST /forms/start (full form)")

        assert body.get("resourceType") == "Bundle", f"Expected Bundle resourceType, got: {body.get('resourceType')}. Full body: {body}"

        entries = body.get("entry", [])
        logger.info(f"\n[INTEGRATION] Bundle returned {len(entries)} entries")
        assert len(entries) >= 1, f"Expected at least 1 entry in results Bundle (the Patient resource at minimum), got {len(entries)}. This may indicate no CQL jobs ran or the patient was not found."

        # Log a summary of resource types returned
        resource_types = [e.get("resource", {}).get("resourceType", "unknown") for e in entries]
        logger.info(f"\n[INTEGRATION] Resource types in bundle: {resource_types}")

        # If expected_output was provided, validate resourceType matches
        expected = scenario.get("expected_output", {})
        if expected.get("resourceType"):
            assert body["resourceType"] == expected["resourceType"], f"Expected resourceType {expected['resourceType']}, got {body['resourceType']}"


@pytest.mark.integration
class TestStartJobsSingleJob:
    """
    POST /forms/start with a specific job parameter (runs one CQL library, synchronous).

    Input: start_jobs_requests[0] from user_data.json — the 'job' parameter already specifies a single CQL file (e.g. "ifu_information.cql").
    Asserts:
      - HTTP 200
      - Response is a Bundle with at least 1 entry
    """

    def test_start_jobs_single_returns_bundle(self, integration_client):
        user_data = load_user_data()
        scenarios = user_data.get("start_jobs_requests", [])
        if not scenarios:
            pytest.skip("No start_jobs_requests entries in user_data.json")

        # Find the scenario that has a specific 'job' parameter
        scenario = next(
            (s for s in scenarios if any(p.get("name") == "job" for p in s.get("parameter", []))),
            scenarios[0],  # fall back to the first one
        )
        post_body = {k: v for k, v in scenario.items() if not k.startswith("_") and k != "expected_output"}

        job_param = next((p for p in post_body.get("parameter", []) if p.get("name") == "job"), None)
        logger.info(f"\n[INTEGRATION] POST /forms/start — single job: {job_param.get('valueString') if job_param else 'all (no job param)'}")
        logger.info("\n[INTEGRATION] Calling CQF Ruler + external FHIR server (synchronous)...")

        response = integration_client.post("/forms/start", json=post_body)

        logger.info(f"\n[INTEGRATION] Response status: {response.status_code}")

        assert response.status_code == 200, f"POST /forms/start (single job) returned {response.status_code}. Check CQF Ruler connectivity and that the library exists. Body: {response.text[:500]}"

        body = response.json()
        _assert_not_error_operation_outcome(body, "POST /forms/start (single job)")

        assert body.get("resourceType") == "Bundle", f"Expected Bundle, got {body.get('resourceType')}. Full: {body}"

        entries = body.get("entry", [])
        logger.info(f"\n[INTEGRATION] Bundle returned {len(entries)} entries")
        assert len(entries) >= 1, f"Expected at least 1 entry (Patient + result), got {len(entries)}. Check that the CQL library runs correctly against the patient."

        resource_types = [e.get("resource", {}).get("resourceType", "?") for e in entries]
        logger.info(f"\n[INTEGRATION] Resource types: {resource_types}")


# ---------------------------------------------------------------------------
# Tests: POST /smartchartui/batchjob → poll GET /smartchartui/results/{id}
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBatchJobPolling:
    """
    POST /smartchartui/batchjob (async), then poll GET /smartchartui/results/{batchId} until the status-observation shows "complete".

    Input: batch_jobs_requests[0] from user_data.json.
    Asserts:
      - POST returns 200 with a Parameters body containing a batchId
      - Within 5 minutes, GET /smartchartui/results/{batchId} returns a Bundle where Observation/status-observation has status == "complete"
      - The final results bundle has at least BATCH_JOB_MIN_RESULT_ENTRIES entries
    """

    def test_batch_job_completes_within_timeout(self, integration_client):
        user_data = load_user_data()
        scenarios = user_data.get("batch_jobs_requests", [])
        if not scenarios:
            pytest.skip("No batch_jobs_requests entries in user_data.json")

        post_body = {k: v for k, v in scenarios[0].items() if not k.startswith("_")}

        # ── Step 1: POST the batch job ──────────────────────────────────────
        logger.info(
            f"\n[INTEGRATION] POST /smartchartui/batchjob — jobPackage={next((p.get('valueString') for p in post_body.get('parameter', []) if p.get('name') == 'jobPackage'), '?')}, "
            f"patientId={next((p.get('valueString') for p in post_body.get('parameter', []) if p.get('name') == 'patientId'), '?')}"
        )

        post_response = integration_client.post("/smartchartui/batchjob", json=post_body)
        logger.info(f"\n[INTEGRATION] POST response status: {post_response.status_code}")

        assert post_response.status_code == 200, (
            f"POST /smartchartui/batchjob returned {post_response.status_code}. Check that the Questionnaire exists on CQF Ruler and the DB is accessible. Body: {post_response.text[:500]}"
        )

        post_body_response = post_response.json()
        _assert_not_error_operation_outcome(post_body_response, "POST /smartchartui/batchjob")

        batch_id = _extract_batch_id(post_body_response)
        # Register for teardown cleanup (see _upload_integration_questionnaire fixture)
        _batch_ids_to_cleanup.append(batch_id)
        logger.info("\n[INTEGRATION] " + f"Batch job started — batchId: {batch_id}")
        logger.info(
            f"\n[INTEGRATION] Will poll GET /smartchartui/results/{batch_id} every {BATCH_JOB_POLL_INTERVAL_SECONDS}s for up to {BATCH_JOB_POLL_TIMEOUT_SECONDS}s ({BATCH_JOB_POLL_TIMEOUT_SECONDS // 60} min)..."
        )

        # ── Step 2: Poll until complete ─────────────────────────────────────
        max_attempts = BATCH_JOB_POLL_TIMEOUT_SECONDS // BATCH_JOB_POLL_INTERVAL_SECONDS
        results_url = f"/smartchartui/results/{batch_id}"
        final_body = None

        for attempt in range(1, max_attempts + 1):
            time.sleep(BATCH_JOB_POLL_INTERVAL_SECONDS)

            poll_response = integration_client.get(results_url)

            if poll_response.status_code != 200:
                logger.info(f"\n[INTEGRATION]    Attempt {attempt}/{max_attempts}: GET {results_url} → {poll_response.status_code} (retrying...)")
                if poll_response.status_code >= 500:
                    logger.error(f"500 Error Body: {poll_response.text}")
                continue

            poll_body = poll_response.json()
            status_obs = _get_status_observation(poll_body)

            if status_obs is None:
                logger.info(f"\n[INTEGRATION]   Attempt {attempt}/{max_attempts}: no status-observation yet in bundle ({len(poll_body.get('entry', []))} entries total)")
                continue

            obs_status = status_obs.get("status", "unknown")
            status_text = status_obs.get("valueCodeableConcept", {}).get("text", "no text")
            entry_count = len(poll_body.get("entry", []))

            logger.info(f"\n[INTEGRATION]   Attempt {attempt}/{max_attempts}: status={obs_status}, progress='{status_text}', entries={entry_count}")

            if obs_status == STATUS_OBS_COMPLETE_CODE:
                logger.info(f"\n[INTEGRATION] Batch job COMPLETE after {attempt * BATCH_JOB_POLL_INTERVAL_SECONDS}s — {entry_count} entries in results bundle")
                final_body = poll_body
                break

        # ── Step 3: Assert completion ───────────────────────────────────────
        assert final_body is not None, (
            f"Batch job {batch_id} did not complete within {BATCH_JOB_POLL_TIMEOUT_SECONDS}s ({BATCH_JOB_POLL_TIMEOUT_SECONDS // 60} min). This may indicate a hung CQL execution, an unreachable external "
            f"FHIR server, or an NLPaaS timeout. Check the API server logs for job {batch_id}."
        )

        final_entries = final_body.get("entry", [])
        logger.info("\n[INTEGRATION] " + f"Final results bundle: {len(final_entries)} entries total")

        assert len(final_entries) >= BATCH_JOB_MIN_RESULT_ENTRIES, (
            f"Expected at least {BATCH_JOB_MIN_RESULT_ENTRIES} entries in the completed results bundle, got {len(final_entries)}. The jobs may have run but produced fewer results than expected. "
            "Check CQL library outputs and patient data for jobPackage referenced in user_data.json."
        )

        # Log a breakdown of what came back
        resource_types: dict[str, int] = {}
        for entry in final_entries:
            rt = entry.get("resource", {}).get("resourceType", "unknown")
            resource_types[rt] = resource_types.get(rt, 0) + 1
        logger.info(f"\n[INTEGRATION] Resource type breakdown: {resource_types}")
