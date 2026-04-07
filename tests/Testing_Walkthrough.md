# RC-API Test Suite Walkthrough

## Overview

The test suite lives entirely under `tests/`. It is structured as **7 test files** (one per router/topic), a shared **`conftest.py`**, a **`fixtures/`** folder of JSON data, and a **`test_integration.py`** file that hits real external services.

```
tests/
├── conftest.py                     # Shared fixtures & helpers
├── fixtures/
│   ├── fhir_bundle_empty.json
│   ├── fhir_integration_questionnaire.json
│   ├── fhir_library_cql.json
│   ├── fhir_library_nlpql.json
│   ├── fhir_patient.json
│   ├── fhir_questionnaire.json
│   └── user_data.json
├── test_main_router.py
├── test_webhook.py
├── test_cql_router.py
├── test_nlpql_router.py
├── test_forms_router.py
├── test_smartchartui_router.py
└── test_integration.py             # marked @integration — requires real services
```

---

## `conftest.py` — Shared Infrastructure

Everything in this file is available to all test files automatically.

### Environment Bootstrap (lines 18–32)
Sets all required env vars **before any `src.*` import**. This is critical because `src/util/settings.py` reads `os.environ` at module scope (not lazily). The defaults point to localhost and use a SQLite test DB so no real services are required.

### `app` fixture (session-scoped)
Imports the FastAPI app with three things patched so the lifespan startup doesn't run real I/O:
- `startup_connect` — no DB connection attempted
- `clone_repo_to_temp_folder` — no git clone
- `init_jobs_array` — no CQF Ruler library preload

### `client` fixture (session-scoped)
A `TestClient` wrapping the app. Session-scoped means it's created once and shared across the entire test run. `raise_server_exceptions=False` means 500 errors come back as responses rather than pytest exceptions.

### `mock_httpx` fixture (function-scoped)
Patches `src.util.settings.httpx_client` and re-exports it to every module that imported it at module scope (9 total targets). Tests that need to control outbound HTTP use this — setting `.get.return_value`, `.post.return_value`, etc.

### `mock_fhir_clients` fixture (function-scoped)
Patches `external_fhir_client` and `internal_fhir_client` in `smartchartui.py` with `MagicMock`s. Tests destructure the return value as `ext_client, internal_client = mock_fhir_clients`.

### `mock_jobstate` fixture (function-scoped)
Patches all 8 jobstate functions in `smartchartui.py` (e.g., `get_job`, `add_to_batch_jobs`, `delete_batch_job`) with individual `MagicMock`s. Returns a dict keyed by function name so tests can configure each independently.

### `make_response` / `make_fhir_searchset` helpers
- `make_response(status, json_body, text)` — builds a real `httpx.Response` (not a mock) for use as `mock_httpx.xxx.return_value`.
- `make_fhir_searchset(resources)` — wraps a list of FHIR resources into a FHIR Bundle searchset.

---

## `tests/fixtures/`

| File | Purpose |
|---|---|
| `fhir_bundle_empty.json` | A FHIR Bundle with `total: 0` and no entries — used to simulate "not found" from CQF Ruler |
| `fhir_library_cql.json` | A minimal FHIR `Library` resource with base64-encoded CQL content |
| `fhir_library_nlpql.json` | Same structure for an NLPQL library |
| `fhir_patient.json` | A minimal FHIR `Patient` resource |
| `fhir_questionnaire.json` | A minimal FHIR `Questionnaire` with name `TestQuestionnaire` and a few items |
| `fhir_integration_questionnaire.json` | The full `SETNETInfantFollowUpIntegrationTesting` Questionnaire — uploaded to CQF Ruler before integration tests run |
| `user_data.json` | User-provided real patient IDs, job inputs, and expected outputs for data-driven + integration tests |

### `user_data.json` structure
```json
{
  "patients":              [...],   // for TestPatientSearchUserData parametrize
  "start_jobs_requests":   [...],   // for TestStartJobsUserData + integration start_jobs tests
  "batch_jobs_requests":   [...],   // for TestBatchJobUserData + integration batch poll test
  "jobpackage_csv_samples": [...]   // currently unused by tests
}
```
Fields containing `"REPLACE_ME"` cause tests to auto-skip (`pytest.skip`).

---

## `test_main_router.py` — Health & Config Endpoints

Covers `GET /`, `GET /health`, `GET /config`.

| Class | What it tests |
|---|---|
| `TestRootEndpoint` | `/` returns a FHIR `OperationOutcome` with `code: processing` and a "base URL" diagnostic message |
| `TestHealthEndpoint` | `/health` with a monkeypatched `get_health_of_stack` — checks 200 and response shape |
| `TestConfigEndpoint` | `/config` returns `{}` when not configured, or a `ConfigEndpointModel` with `primaryIdentifier` when patched |

---

## `test_webhook.py` — GitHub Webhook

Covers `POST /webhook`.

| Test | What it asserts |
|---|---|
| `test_webhook_returns_acknowledged` | Returns `"Acknowledged"` string on success |
| `test_webhook_calls_clone_with_ssh_url` | `clone_repo_to_temp_folder` is called exactly once with the `ssh_url` |
| `test_webhook_uses_ssh_not_clone_url` | SSH URL not HTTPS clone URL is passed |
| `test_webhook_missing_repository_key_raises` | Malformed payload (no `repository` key) → 500 |

---

## `test_cql_router.py` — CQL Library CRUD

Covers `GET /forms/cql`, `GET /forms/cql/{name}`, `POST /forms/cql`, `PUT /forms/cql/{name}`.

### `TestGetCqlLibraries`
- Mocks CQF Ruler returning a searchset Bundle → verifies 200 + `resourceType: Bundle`
- CQF Ruler returning 500 → 200 with `OperationOutcome` (code: `transient`)
- Empty bundle → `total: 0`

### `TestGetCqlByName`
- Library found → response is the base64-decoded plain-text CQL
- Not found (empty bundle) → `OperationOutcome not-found`
- Server error → `OperationOutcome transient`

### `TestSaveCql`
A real CQL string `VALID_CQL = "library TestLibrary version '1.0.0'..."` is used as the POST body.
- New library → POST to CQF Ruler → 201 `OperationOutcome` with the new ID in diagnostics
- Existing library → GET finds it, triggers PUT instead → still 201
- CQL validation fails → 500 `OperationOutcome`
- Empty body → 400 (custom FastAPI exception handler converts 422 → 400)
- CQF Ruler POST fails → 500

### `TestUpdateCql`
- Successful PUT → 201 `OperationOutcome` with `severity: information`
- Empty body → 400

---

## `test_nlpql_router.py` — NLPQL Library CRUD

Covers `GET /forms/nlpql`, `GET /forms/nlpql/{name}`, `POST /forms/nlpql`, `PUT /forms/nlpql/{name}`.

Structurally mirrors `test_cql_router.py` but with NLPQL-specific concerns.

> **Route Ordering**: The file documents that `nlpql_router` must be registered **before** `forms_router` in `main.py`, otherwise `GET /forms/nlpql` is incorrectly captured by the forms router.

> **NLPAAS_URL quirk**: `settings.py` appends `/` to `NLPAAS_URL`, so the value `"False"` becomes `"False/"` (truthy). Tests that want to simulate "no NLPaaS" directly patch `src.services.libraryhandler.nlpaas_url` to `""` instead of relying on the env var.

| Class | Key scenarios |
|---|---|
| `TestGetNlpqlLibraries` | Bundle returned, empty bundle, 503 → OperationOutcome |
| `TestGetNlpqlByName` | Found → plain-text NLPQL, not found → OO not-found, 503 → OO transient |
| `TestSaveNlpql` | No NLPaaS → 400, success → 201, empty body → 400, existing lib → PUT, validation fails → 400 |
| `TestUpdateNlpql` | Success → 201, empty body → 400 |

---

## `test_forms_router.py` — Questionnaire & Job Orchestration

Covers `GET /forms`, `GET /forms/{name}`, `POST /forms`, `PUT /forms/{name}`, `POST /forms/start`, `GET /forms/status/all`, `GET /forms/status/{uid}`, `POST /forms/jobPackageToQuestionnaire`.

### `TestGetForms` / `TestGetFormByName`
- Standard CQF Ruler mocking pattern: success → Bundle, 503 → OperationOutcome
- `GET /forms/{name}` — found → `Questionnaire` resource; empty bundle → `OperationOutcome not-found`

### `TestSaveForm` / `TestUpdateForm`
- **Save**: GET (check exists) → empty bundle → POST to CQF Ruler → 201 → returns informational `OperationOutcome`
- **Update**: GET finds existing → PUT → 200 informational OO; empty bundle → OO (known production edge case noted in comment); CQF Ruler GET fails → `transient` OO

### `TestStartJobs`
| Test | Scenario |
|---|---|
| `test_start_jobs_sync_calls_start_jobs` | Sync path — patches `start_jobs` and checks the Bundle is returned |
| `test_start_jobs_async_creates_job_entry` | `?asyncFlag=true` path — returns `Parameters` with `jobId` and a `Location` header |
| `test_start_jobs_missing_required_fields` | Incomplete body → 400 or 422 |

### `TestJobStatus`
- `GET /forms/status/all` with empty `jobs` dict → `{}`
- `GET /forms/status/{uid}` not found → 404 OO with `code: code-invalid`
- `GET /forms/status/{uid}` found → 200 `Parameters` resource

### `TestJobPackageToQuestionnaire`
- Invalid CSV → error OO (via monkeypatched converter)
- Valid CSV → Questionnaire resource returned

### `TestStartJobsUserData` _(data-driven)_
Parametrized over `start_jobs_requests` from `user_data.json`. Mocks `start_jobs` to return the `expected_output` and asserts the resourceType matches. Auto-skips if `REPLACE_ME` is present.

---

## `test_smartchartui_router.py` — SmartChart UI Endpoints

The largest unit test file. Covers all 10 `smartchartui.py` endpoints.

### Helper functions
- `_make_job_dict(job_id, status)` — builds a ParametersJob dict as stored in DB
- `_make_batch_job_dict(batch_id, child_ids)` — builds a BatchParametersJob dict with a child job List resource

### `TestReadPatient` / `TestSearchPatient`
- `readResource` called with patient ID → response is the patient fixture JSON.
- Search variants tested: no params (all), `?_id=`, `?name=`, `?identifier=`, `?name=&birthdate=` — each asserts the correct parameters dict was forwarded to `ext_client.searchResource`.

### `TestSearchGroup`
- Group with one member reference → list includes `Group` + resolved `Patient`
- Empty group list → `[]`

### `TestSearchQuestionnaire`
- Returns list of Questionnaires from `internal_fhir_client`
- Empty → `[]`

### `TestGetJob`
- `get_job` returns `None` → 404 OO not-found
- `get_job` returns job dict → 200 `Parameters`

### `TestGetAllBatchJobs`
- Empty DB → `[]`
- One batch job with one child → response includes `batchJobStatus` parameter (computed from child statuses)

### `TestGetBatchJobById`
- Not found → 404 OO
- Found → 200 `Parameters`

### `TestDeleteBatchJob`
- Success → 200 OO with "deleted" code
- Not found → 404 (mock returns a 404 JSONResponse)

### `TestPostBatchJob`
- `get_form` returns a Questionnaire, `add_to_batch_jobs` returns `True` → 200 `Parameters` with `batchId` + `Location` header
- `add_to_batch_jobs` returns `False` → 500 OO

### `TestGetBatchJobResults`
- No batch job in DB → 404
- Batch job with one complete child job → 200 Bundle (status observation assembled from child results)

### Data-driven classes
- `TestBatchJobUserData` — parametrized POST /smartchartui/batchjob from `user_data.json`
- `TestPatientSearchUserData` — parametrized GET /smartchartui/Patient/{id} from `user_data.json`

---

## `test_integration.py` — End-to-End Integration Tests

> Requires a `.env` file at the repo root with real service URLs. Marked `@pytest.mark.integration` — only runs with `-m integration`.

### The `integration_client` fixture
This is the centerpiece of the file. It:
1. Reads URLs from `.env` (`CQF_RULER_R4`, `EXTERNAL_FHIR_SERVER_URL`, `NLPAAS_URL`, `DB_CONNECTION_STRING`)
2. **Setup**: POSTs `fhir_integration_questionnaire.json` to CQF Ruler (using raw `httpx`) → captures the server-assigned ID
3. Patches all module-level URL variables so every outbound call from the app hits the real servers
4. Patches DB engine if a real `DB_CONNECTION_STRING` is provided
5. `yield c` — all tests run here using the live-patched `TestClient`
6. **Teardown** (while `c` and patches are still active): DELETEs the Questionnaire from CQF Ruler, then iterates `_batch_ids_to_cleanup` and calls `DELETE /smartchartui/batchjob/{id}` through `c` directly

> The teardown runs **inside** `with TestClient(app) as c:` — this is intentional. An earlier version tried creating a second `TestClient` in teardown, which re-triggered the lifespan/`startup_connect` and crashed with a DB schema error.

### Helper functions
- `_load_env_file(path)` — parses a `.env` file without python-dotenv dependency
- `_assert_not_error_operation_outcome(body, context)` — fails with a descriptive message if the response is an error OO
- `_extract_batch_id(response_body)` — finds `parameter[name=="batchId"].valueString`
- `_get_status_observation(results_body)` — finds `Observation/status-observation` in a results Bundle

### `TestStartJobsFullForm` (`scenarios[0]`)
- `POST /forms/start` with the first entry from `user_data.json`
- Asserts 200, `resourceType: Bundle`, and at least 1 entry
- Logs resource type breakdown

### `TestStartJobsSingleJob` (finds scenario with `job` param)
- Same endpoint, but picks a scenario that has a specific `job` parameter (single CQL library run)
- Same shape assertions

### `TestBatchJobPolling`
Full async batch job lifecycle:
1. `POST /smartchartui/batchjob` with `jobPackage: SETNETInfantFollowUpIntegrationTesting`
2. Extracts `batchId`, appends it to `_batch_ids_to_cleanup`
3. Polls `GET /smartchartui/results/{batchId}` every 15s for up to 5 minutes
4. On each poll, checks for `Observation/status-observation` with `status == "complete"`
5. Final assertion: at least 10 entries in the completed bundle

---

## Running the Tests

```bash
# All unit tests (no real services needed)
conda run -n rcapi python -m pytest tests/ -v

# Integration tests only (requires .env)
conda run -n rcapi python -m pytest tests/test_integration.py -v -s -m integration

# Single class
conda run -n rcapi python -m pytest tests/test_smartchartui_router.py::TestPostBatchJob -v
```
