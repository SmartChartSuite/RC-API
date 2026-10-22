# Results Combining API (RC-API)

> **v1.0** — SmartChart Suite Results Combining API

The RC-API is the SmartChart Suite middleware that orchestrates clinical data abstraction for a patient population. It combines structured evidence from CQL-based clinical decision support with unstructured evidence from LLM-based chart abstraction, returning results as a FHIR-conformant Bundle of Observations.

It sits within a FHIR-based stack and is designed to be deployed alongside:
- An **external FHIR server** (patient data source — DocumentReferences, Patients, etc.)
- A **HAPI FHIR server** for CQL execution and resource management (Questionnaires, Libraries, Groups)

For deployment instructions, see the [SmartChart-Core repository](https://github.com/SmartChartSuite/SmartChart-Core).
For full documentation, see [smartchartsuitedocs.readthedocs.io](https://smartchartsuitedocs.readthedocs.io/en/latest/).

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [API Endpoints](#api-endpoints)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Running the Server](#running-the-server)
- [Authentication](#authentication)
- [Project Structure](#project-structure)

---

## Architecture Overview

```
Client
  │
  ▼
RC-API (this service)
  ├── POST /batchjob ──────────────────────────────────── Submits a job package run
  │     │
  │     ├── Fetches Questionnaire (job package) ─────── HAPI FHIR
  │     ├── Runs CQL Libraries ──────────────────────── HAPI FHIR (Library/$evaluate)
  │     ├── Fetches DocumentReferences ──────────────── External FHIR Server
  │     ├── Runs LLM prompts (one per document) ─────── LiteLLM proxy
  │     └── Stores result FHIR Bundle ───────────────── Local DB
  │
  ├── GET /batchjob/{id}/status         Status polling (lightweight)
  ├── GET /batchjob/{id}                Full FHIR Bundle retrieval
  │
  ├── /jobpackage  ─── Questionnaire CRUD ──────────────── HAPI FHIR (proxied)
  ├── /group       ─── Group CRUD + Patient include ─────── HAPI FHIR (proxied)
  ├── /library     ─── CQL Library CRUD ─────────────────── HAPI FHIR (proxied)
  ├── /patient     ─── Patient search/read ──────────────── External FHIR (proxied)
  └── /response    ─── QuestionnaireResponse CRUD ────────── Local DB (patient data)
```

Batch jobs run **asynchronously** through a durable database-backed worker embedded in the API container. `POST /batchjob` commits the request as `pending`; the worker claims it under a renewable lease and resumes unfinished work after retry or restart without rerunning completed child tasks. Clients poll `GET /batchjob/{id}/status` for lightweight status and use `GET /batchjob/{id}` for the latest result snapshot. Batch statuses are `pending`, `running`, `complete`, `partial`, and `error`; `partial` means retries were exhausted after at least one child completed, while `error` means no child completed. Once individual tasks finish, running jobs return a preliminary Bundle containing the results completed so far; terminal jobs return their stored result Bundle when one is available. On each results request, the status Observation text reports live task progress as `Batch job status: x% (m/n)`.

---

## API Endpoints

### Batch Jobs

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/batchjob` | Submit a job package run for a patient |
| `GET` | `/batchjob` | List all batch jobs as FHIR Parameters resources (`?include_patient=true`) |
| `GET` | `/batchjob/stream` | Stream the form list as SSE using the same filters and Bundle shape as `/batchjob` |
| `GET` | `/batchjob/{id}/status` | Poll status of a batch job as a FHIR Parameters resource |
| `GET` | `/batchjob/{id}` | Retrieve the latest preliminary or final FHIR result Bundle |
| `DELETE` | `/batchjob/{id}` | Delete a batch job *(requires `admin` scope)* |

**`POST /batchjob` request body** (FHIR Parameters):
```json
{
  "resourceType": "Parameters",
  "parameter": [
    { "name": "patientId",        "valueString": "patient-123" },
    { "name": "jobPackage",       "valueString": "ExampleRegistry" },
    { "name": "jobPackageVersion","valueString": "1.0.0" },
    { "name": "job",              "valueString": "ExampleStructuredTask" },
    { "name": "job",              "valueString": "2026_01/example/example-unstructured-task" }
  ]
}
```

Use one or more repeated `job` parameters to run only the named CQL library or prompt entries from the job package. Prompt matching accepts either the full prompt path or a unique prompt file name.

On success, `POST /batchjob` returns a FHIR `Parameters` resource that now includes `batchJobQuestionnaireResponse` as a `valueReference`, for example:

```json
{
  "name": "batchJobQuestionnaireResponse",
  "valueReference": {
    "reference": "QuestionnaireResponse/550e8400-e29b-41d4-a716-446655440000"
  }
}
```

That reference points to the initial local `QuestionnaireResponse` record created when the batch job starts. The stored resource is created with `status = in-progress` and is tied to the exact Questionnaire resolved for the job package and version.
The batch job list and status endpoints include `startedBy`.

`GET /batchjob/stream` is a non-breaking SSE alternative to the form-list endpoint. It emits an initial `form-list` event immediately, then emits another event only when the filtered Bundle changes. Unchanged polls send SSE keepalive comments. The stream is summary-only and does not support `include_patient=true`; use the regular endpoint when the full Patient resource is required. Because authenticated browser `EventSource` clients cannot normally set an `Authorization` header, clients using Bearer authentication should consume this endpoint with an authenticated streaming `fetch()` or an equivalent client.

### Job Packages (Questionnaires)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jobpackage` | Search Questionnaires (`?name=`, `?version=`) within the `smartchartui` context and return a flat list of Questionnaire resources |
| `GET` | `/jobpackage/title` | List `[{"name", "title"}]` for every job package, sorted by title, for client type-ahead prefix matching. Each process keeps its own cache, loaded at startup; successful POST/PUT/DELETE writes through this API invalidate it, while edits made directly on HAPI FHIR still require `?refresh=true` |
| `GET` | `/jobpackage/{id}` | Get a Questionnaire |
| `POST` | `/jobpackage` | Create a Questionnaire |
| `PUT` | `/jobpackage/{id}` | Update a Questionnaire |
| `DELETE` | `/jobpackage/{id}` | Delete a Questionnaire *(requires `admin` scope)* |

### Groups and Libraries

Same CRUD pattern (`GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}`) for:
- `/group` — FHIR Groups with implicit Patient member inclusion on search
- `/library` — CQL Library resources

### Responses

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/response` | List stored QuestionnaireResponses as a FHIR `Bundle` (`?batch_job_id=`, `?job_package=`) |
| `GET` | `/response/{id}` | Get a stored `QuestionnaireResponse` by local response ID |
| `POST` | `/response?batch_job_id=<id>` | Create a stored `QuestionnaireResponse` from a raw FHIR `QuestionnaireResponse` body |
| `PUT` | `/response/{id}` | Update a stored `QuestionnaireResponse` using a raw FHIR `QuestionnaireResponse` body |
| `DELETE` | `/response/{id}` | Delete a stored `QuestionnaireResponse` *(requires `admin` scope)* |

`POST /response` expects a raw `QuestionnaireResponse` resource body. The related local batch job id is passed as the `batch_job_id` query parameter. The API derives `patient_id` from `subject.reference`, stores the `questionnaire` value as the local `job_package`, and normalizes the resource `id` to the local response id.
Successful POST and PUT responses return the normalized resource with the server-derived `author`; client-supplied author values are never retained.

### Write validation

`POST` and `PUT` bodies for `/jobpackage`, `/group`, `/library`, and `PUT /response/{id}` are validated against the matching resource model in `src/models/fhir.py`. `POST /response` already used its model. The checks are:

- `resourceType` is required and must match the endpoint. For example, a `Patient` sent to `/group` is rejected.
- Fields the model declares, such as `name`, `version`, `member`, and `subject`, must have the declared types.
- On `PUT`, a body `id` must be omitted or equal the path id. A mismatch, including an explicit `"id": null`, returns 400 with an `invariant` OperationOutcome.

Validation failures return a 400 FHIR `OperationOutcome` and nothing is forwarded or stored. Valid bodies are passed on exactly as sent: extensions, unknown or nested fields, and explicit `null` values are kept, and no defaults are added. `/response` still applies its normalization of `id`, `meta.lastUpdated`, `authored`, and the stored subject/questionnaire linkage.

### Patients

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/patient` | Search Patients on the external FHIR server using FHIR query params |
| `GET` | `/patient/{id}` | Get a single Patient from the external FHIR server |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `ok` or `degraded` with a list of missing config vars |

### Config

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/config` | Returns public client config such as the primary identifier metadata |

Interactive docs are available at `/docs`.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values. The API starts in **degraded mode** if required variables are missing — endpoints return a FHIR `OperationOutcome` (HTTP 503) rather than crashing.

### Required

| Variable | Description |
|----------|-------------|
| `EXTERNAL_FHIR_SERVER_URL` | Base URL of the external FHIR server (patient data source) |
| `HAPI_FHIR_CQL_EXECUTION_URL` | Base URL of the HAPI FHIR server for CQL execution and resource management |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `EXTERNAL_FHIR_SERVER_AUTH` | *(none)* | Full `Authorization` header value for the external FHIR server (e.g. `Bearer <token>`) |
| `LITELLM_MODEL` | *(none)* | LiteLLM model string (e.g. `openai/gpt-4o`). All three `LITELLM_*` vars required together |
| `LITELLM_API_BASE` | *(none)* | LiteLLM proxy base URL |
| `LITELLM_API_KEY` | *(none)* | LiteLLM API key |
| `LITELLM_MODEL_REASONING_EFFORT` | *(none)* | Optional reasoning effort forwarded to models that support it (e.g. `low`, `medium`, `high`) |
| `LITELLM_PROMPT_CACHE_ENABLED` | `true` | Forward a stable provider-side `prompt_cache_key` for supported models; one key is shared across documents for the same model/prompt template |
| `LANGFUSE_PUBLIC_KEY` | *(none)* | Langfuse public key. All three `LANGFUSE_*` vars required together |
| `LANGFUSE_SECRET_KEY` | *(none)* | Langfuse secret key |
| `LANGFUSE_HOST` | *(none)* | Langfuse host URL for prompt retrieval and, by default, OTEL ingestion |
| `OTEL_SEMCONV_STABILITY_OPT_IN` | *(none)* | Provider-level LiteLLM/OpenTelemetry setting; `gen_ai_latest_experimental` opts into the latest GenAI conventions |
| `LANGFUSE_PROMPT_FETCH_TIMEOUT_SECONDS` | `5` | Maximum duration of each synchronous Langfuse prompt fetch attempt, executed outside the API event loop |
| `LANGFUSE_PROMPT_MAX_RETRIES` | `1` | Retries after a failed Langfuse prompt fetch before using the matching local prompt |
| `LANGFUSE_PROMPT_CACHE_TTL_SECONDS` | `60.0` | Application Langfuse prompt cache lifetime in seconds; `0` disables caching |
| `LANGFUSE_PROMPT_FETCH_CONCURRENCY` | `4` | Maximum concurrent Langfuse prompt fetches across batch jobs |
| `PROMPTS_DIR` | `./prompts` | Local prompt directory used when Langfuse is disabled, unavailable, or fails to return an individual prompt |
| `OAUTH2_JWKS_URL` | *(none)* | JWKS endpoint URL. **Required** unless `AUTH_DISABLED=true`; protected endpoints return 503 if missing |
| `OAUTH2_ISSUER` | *(none)* | Expected `iss` claim in JWT. **Required** unless `AUTH_DISABLED=true` |
| `OAUTH2_AUDIENCE` | *(none)* | Expected `aud` claim in JWT. **Required** unless `AUTH_DISABLED=true` |
| `OAUTH2_JWKS_CACHE_SECONDS` | `3600` | JWKS cache lifetime; unknown key ids trigger an early (rate-limited) refresh |
| `OAUTH2_USERINFO_TIMEOUT_SECONDS` | `3.0` | Timeout for the best-effort OAuth provider `/userinfo` lookup |
| `OAUTH2_USERINFO_STALENESS_SECONDS` | `86400` | Cached profile lifetime before the next write refreshes it (24 hours) |
| `AUTH_DISABLED` | `false` | Local development only: disables authentication entirely |
| `CORS_ALLOW_ORIGINS` | *(none)* | Comma-separated browser origins permitted by CORS; unset sends no CORS headers |
| `ATTACHMENT_ALLOWED_HOSTS` | *(none)* | Extra hosts (beyond the external FHIR origin) from which attachment URLs may be fetched; `.example.org` includes subdomains |
| `CQL_MAX_CONCURRENCY` | `4` | Maximum concurrent in-flight CQL library evaluations across batch jobs |
| `DOC_MAX_COUNT` | `500` | Maximum DocumentReferences processed per patient |
| `DOC_MAX_BYTES` | `5000000` | Maximum bytes per document attachment (inline or fetched) |
| `PATIENT_SUMMARY_CACHE_TTL_SECONDS` | `900.0` | Seconds projected Patient summaries remain fresh in the process-local cache; `0` disables caching |
| `PATIENT_SUMMARY_CACHE_MAX_ENTRIES` | `1000` | Maximum projected Patient summaries retained per process |
| `DB_CONNECTION_STRING` | `sqlite+pysqlite:///rcapi_jobs.sqlite` | SQLAlchemy connection string |
| `DB_SCHEMA` | `rcapi` | DB schema name (ignored for SQLite) |
| `BATCH_WORKER_ENABLED` | `true` | Run the durable batch consumer inside the API process |
| `BATCH_WORKER_POLL_INTERVAL_SECONDS` | `2` | Seconds between checks for runnable and expired jobs |
| `BATCH_WORKER_LEASE_SECONDS` | `120` | Seconds a worker owns a claimed batch without renewal; keep this greater than the heartbeat interval |
| `BATCH_JOB_HEARTBEAT_INTERVAL_SECONDS` | `30` | Seconds between lease renewals while a batch is executing |
| `BATCH_JOB_CHECKPOINT_INTERVAL_SECONDS` | `5.0` | Minimum seconds between partial Bundle checkpoint writes; `0` writes after every task |
| `BATCH_JOB_CHECKPOINT_EVERY_TASKS` | `10` | Minimum task completions between partial Bundle checkpoint writes |
| `BATCH_JOB_MAX_ATTEMPTS` | `3` | Maximum claimed execution attempts before terminal error |
| `BATCH_JOB_RETRY_DELAY_SECONDS` | `30` | Seconds before a failed or expired attempt is runnable again |
| `BATCH_JOB_RECOVERY_CHUNK_SIZE` | `100` | Maximum expired batches processed per recovery transaction |
| `BATCH_JOB_STREAM_POLL_INTERVAL_SECONDS` | `5.0` | Seconds between form-list SSE refresh checks |
| `DEPLOY_URL` | `http://example.org/` | Base URL used in Observation identifiers as well as determining root_path |
| `ROOT_PATH` | *(derived from `DEPLOY_URL` path, or empty)* | FastAPI `root_path` for deployments behind a URL prefix, e.g. `/rc-api` |
| `PRIMARYIDENTIFIER_SYSTEM` | *(none)* | If set, enables `/config.primaryIdentifier.system` in the public config response |
| `PRIMARYIDENTIFIER_LABEL` | *(none)* | Optional label returned as `/config.primaryIdentifier.label` when `PRIMARYIDENTIFIER_SYSTEM` is set |
| `LOG_LEVEL` | `INFO` | Loguru log level |

---

## Local Development

### Prerequisites

- [pixi](https://pixi.sh/latest/)
- Git

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/SmartChartSuite/RC-API.git
cd RC-API

# 2. Create the uv-managed project environment
pixi run install

# 3. Install pre-commit hooks
pixi run hooks

# 4. Copy and configure environment variables
cp .env.example .env
# Edit .env with your FHIR server URLs and credentials

# 5. Apply database migrations
pixi run migrate
```

`pixi` is used for local task orchestration. Python dependencies live in `pyproject.toml`, and `uv` creates the project environment for both local development and Docker builds. The project targets Python 3.14 and uv 0.13 everywhere: `pyproject.toml` (`requires-python` and `[tool.uv] required-version`) is the source of truth, Pixi pins matching versions, and CI reads the uv version from `pyproject.toml`. `uv.lock` is the only application lockfile; `pixi.lock` only pins the Python and uv toolchain.

### Database Migrations

Alembic is the schema authority. Run migrations once as a deployment step before starting or replacing API workers:

```bash
pixi run migrate
```

For a fresh database, this creates the baseline tables and applies all later revisions. For an existing database created before Alembic was introduced, first back up the database, stamp it at the baseline revision, and then apply later migrations:

```bash
uv run alembic stamp 0001
uv run alembic upgrade head
```

Do not stamp a fresh empty database because stamping records a revision without creating its tables. The API verifies that the database is at Alembic head during startup and exits with a migration instruction when it is not. Useful inspection commands are `pixi run migration-current` and `pixi run migration-history`.

Revision `0003` adds the embedded-worker queue, lease, retry, and logical-child uniqueness fields. Revision `0004` adds the persisted human-readable Questionnaire title used in batch-job metadata. Running batches renew their leases periodically. Workers continuously requeue expired attempts while retry capacity remains and classify an exhausted batch as `partial` when a stored Bundle includes completed children, otherwise `error`. Partial result Bundles and already-completed or skipped child tasks are preserved across claims, retries, graceful shutdowns, and container restarts. The new `partial` status is a free string value and requires no database migration. The API and workers run in the same container. Each Hypercorn process starts one lease-coordinated consumer, so increasing the process count increases both HTTP capacity and maximum concurrent batch execution. Deploy only one RC-API container unless you intentionally scale replicas against the same database.
Offline SQL generation with `uv run alembic upgrade head --sql` is partially supported: it emits SQL through revisions `0001` and `0002`, but revision `0003` includes a data-dependent logical-job deduplication step that requires an online database connection. Use `uv run alembic upgrade head` for the actual upgrade.

### Tooling Flow

The local developer workflow is split across a few tools, each with a narrow role:

```text
pyproject.toml
  -> uv manages Python packages and the project environment
  -> pixi runs shared local commands like install, serve, test, lint, and format
  -> hypercorn serves the FastAPI app during development and production runs
  -> pre-commit runs repository checks before changes are committed
```

- `pyproject.toml` is the source of truth for Python dependencies and tool config.
- `uv` handles package add/remove operations and syncing the environment.
- `pixi` provides a stable command surface for developers so common workflows do not depend on local shell setup.
- `hypercorn` is the ASGI server behind `pixi run serve` and `pixi run serve-prod`.
- `pre-commit` runs formatting, linting, and `pyright` type checks before code is committed.

### Managing Dependencies

Use `uv` directly for Python package changes. `pixi` is only used here to bootstrap the environment and run common project tasks.

```bash
# Add a runtime dependency
uv add <package>

# Add a development-only dependency
uv add --dev <package>

# Remove a runtime dependency
uv remove <package>

# Remove a development-only dependency
uv remove --dev <package>
```

After changing dependencies, refresh the environment:

```bash
uv sync --dev
```

Or use the existing task wrapper:

```bash
pixi run install
```

Dependency changes should be committed with the updated project metadata files generated by `uv`.

### Prompts Directory

If Langfuse is not configured or is unreachable at startup, LLM prompts are loaded from `./prompts/`. During batch execution, each synchronous Langfuse SDK read runs outside the API event loop with bounded timeout and retries. If an individual read fails, RC-API loads the matching local file instead, using the path structure defined in Questionnaire `unstructuredTask` extensions:

```
prompts/
└── 2025_08/
    └── syphilis/
        └── ig_bstfed.md     # Prompt content with optional YAML frontmatter
```

Each `.md` file supports YAML frontmatter:
```markdown
---
name: ig_bstfed
version: "1.0"
description: "Breastfeeding abstraction criteria for syphilis case investigation"
---

# Prompt content here...
```

---

## Running the Server
When running locally, load `.env` into the shell before starting Hypercorn so LiteLLM receives the tracing settings:

```bash
set -a; source .env; set +a
```

```bash
# Development (auto-reload)
pixi run serve

# Production
pixi run serve-prod
```

The server binds to `:8080` and starts one Hypercorn worker process by default (configured in `hypercorn_config.toml`). Each process can serve requests independently and runs one embedded durable batch consumer coordinated through database leases.

Docker builds install dependencies with `uv` from the committed `uv.lock` file, while local development uses `pixi` tasks that wrap the same `uv`-managed environment. Local `.env` files are for developer machines only and are not copied into the image.

### Docker Image

```bash
# Multi-platform build that reuses the layer cache
pixi run build-image

# Full rebuild: no cache, and pull fresh base images
pixi run build-image-clean
```

The container runs as the unprivileged `rcapi` user (UID/GID 10001). The application code and virtualenv are read-only to that user. Only `/app` itself is writable, so the default SQLite database can be created there. If you mount a host volume for SQLite, make it writable by UID 10001. A `HEALTHCHECK` polls `/health` every 30 seconds.

The image does not apply migrations on startup. Run them from the same image with the env vars the service uses:

```bash
docker run --rm --no-healthcheck \
  -e DB_CONNECTION_STRING=... -e DB_SCHEMA=... \
  ghcr.io/smartchartsuite/rc-api:latest \
  alembic upgrade head
```

`--no-healthcheck` keeps the one-off job from showing as `unhealthy`, because no server is listening while it runs.

### Code Quality

```bash
# Type check
uv run pyright

# Lint
pixi run lint

# Format
pixi run format

# Full non-mutating verification (lint, format check, type check, tests)
pixi run check

# Run all pre-commit hooks
uv run pre-commit run --all-files
```

Ruff runs its default rules plus `B` (bugbear), `I` (import sorting), `UP` (pyupgrade), `ASYNC` and `S` (bandit security checks), with a 200-character line length. Tests ignore only `S101`, so pytest `assert` is allowed there. Pre-commit, `pixi run check` and CI all use the configuration in `pyproject.toml`.

---

## Authentication

Authentication uses an OAuth 2 Bearer/JWT dependency with JWKS-backed RS256 validation and **fails closed**. `OAUTH2_JWKS_URL`, `OAUTH2_ISSUER`, and `OAUTH2_AUDIENCE` must all be set; if any is missing, the server logs an error at startup and protected endpoints return `503` rather than opening up. For local development only, set `AUTH_DISABLED=true` to run without authentication (a warning is logged). `/config` is protected by the same dependency, while `/health` and FastAPI’s `/docs`, `/redoc`, and `/openapi.json` routes remain public for operations and discovery. The JWKS is cached for `OAUTH2_JWKS_CACHE_SECONDS`, refreshed early when a token presents an unknown key id, and served stale if the identity provider is briefly unreachable; if no keys can be obtained the API returns `503`. Authentication failures return a generic `401` without token-parsing details.

### Token Requirements

- Tokens must be signed with **RS256** and verifiable against the JWKS endpoint
- The `iss` claim must match `OAUTH2_ISSUER`
- The `aud` claim must match `OAUTH2_AUDIENCE`
- The token must carry `exp`, `iss`, and `aud` claims
- **`DELETE` endpoints require `admin` in the token's `scope` claim**

### Request Header

```
Authorization: Bearer <token>
```

### User Profile and QuestionnaireResponse Author

On each write that can create or edit a local `QuestionnaireResponse` (`POST /batchjob`, `POST /response`, and `PUT /response/{id}`), RC-API resolves the JWT `sub` through the issuer's `/userinfo` endpoint using the incoming bearer token. The profile is stored in the `users` table and refreshed after `OAUTH2_USERINFO_STALENESS_SECONDS`; a provider timeout, non-2xx response, or malformed payload is logged and never prevents the write. Previously synchronized profile data is retained when a refresh fails.

The server overwrites any client-supplied `QuestionnaireResponse.author` on every write. It stores the last editor as:

```json
{
  "display": "User Name",
  "identifier": {
    "system": "https://your-auth-server.example.com/",
    "value": "auth0|subject"
  }
}
```

`display` falls back to the subject when the provider has no name. Email is retained only in `users.email` and is not copied into the FHIR resource. Existing `started_by` and `last_updated_by` database audit fields continue to store the subject.

---

## Project Structure

```
RC-API/
├── alembic.ini                     # Alembic configuration
├── alembic/                        # Versioned database migrations
├── main.py                         # FastAPI app entry point
├── hypercorn_config.toml           # ASGI server configuration (binds :8080)
├── pyproject.toml                  # Python dependencies and tool configuration
├── pixi.toml                       # Local developer tasks and bootstrap commands
├── .env.example                    # Environment variable template
├── prompts/                        # Local LLM prompt files (.md)
│
├── src/
│   ├── models/
│   │   ├── config.py               # Public /config response models
│   │   ├── fhir.py                 # Canonical FHIR resource models + write validation
│   │   ├── job_request.py          # FHIR Parameters request model
│   │   ├── job_response.py         # Batch job status/result models
│   │   └── prompt.py               # Prompt metadata model
│   │
│   ├── routers/
│   │   ├── batchjob.py             # POST/GET/DELETE /batchjob
│   │   ├── config.py               # GET /config
│   │   ├── group.py                # CRUD /group → HAPI FHIR Group
│   │   ├── jobpackage.py           # CRUD /jobpackage → HAPI FHIR Questionnaire
│   │   ├── library.py              # CRUD /library → HAPI FHIR Library
│   │   ├── patient.py              # Patient search/read via external FHIR
│   │   └── response.py             # CRUD /response → local DB
│   │
│   ├── services/
│   │   ├── batch_job_service.py    # Batch job use cases behind the /batchjob router
│   │   ├── batch_job_filters.py    # Pure batch-list filter parsing and matching
│   │   ├── batch_worker.py         # Embedded durable database-backed worker
│   │   ├── cql_executor.py         # HAPI FHIR Library/$evaluate wrapper
│   │   ├── errorhandler.py         # FHIR OperationOutcome helpers
│   │   ├── external_fhir.py        # Best-effort external FHIR resource fetches
│   │   ├── fhir_context.py         # Patient DocumentReference fetcher
│   │   ├── fhir_proxy.py           # HAPI FHIR CRUD proxy
│   │   ├── job_orchestrator.py     # Runs one batch attempt (lease, tasks, checkpoints)
│   │   ├── job_package.py          # Reads CQL/prompt task lists from a Questionnaire
│   │   ├── result_observations.py  # CQL/LLM result → Observation builders
│   │   ├── result_bundle.py        # Result Bundle and DocumentReference assembly
│   │   ├── llm_executor.py         # LiteLLM per-document execution
│   │   ├── prompt_loader.py        # Langfuse + local ./prompts/ loader
│   │   └── job_state/              # The only database layer
│   │       ├── models.py           # SQLAlchemy ORM tables + claim/job snapshots
│   │       ├── database.py         # Engine, Alembic revision check, initialize_db
│   │       ├── batch_queue.py      # Claiming, leases, fencing, retry, recovery
│   │       ├── batch_records.py    # Batch CRUD, result writes, document store
│   │       ├── child_jobs.py       # Per-task child job rows
│   │       ├── responses.py        # QuestionnaireResponse CRUD
│   │       └── users.py            # Authenticated user profile persistence
│   │
│   └── util/
│       ├── auth.py                 # OAuth 2 JWT validation dependencies
│       ├── current_user.py         # Userinfo sync and trusted QR author mapping
│       ├── documents.py            # DocumentReference text encoding
│       ├── fhir_bundle.py          # Tolerant Bundle parsing helpers
│       ├── http_clients.py         # Shared outbound HTTP client profiles + retries
│       ├── outbound_url.py         # Safe outbound URL helpers
│       ├── settings.py             # Typed environment configuration
│       └── threading.py            # run_db: database calls off the event loop
│
└── tests/                          # Pytest coverage for routers, services, and models
```
