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

Batch jobs run **asynchronously** through a durable database-backed worker embedded in the API container. `POST /batchjob` commits the request as `pending`; the worker claims it under a renewable lease and resumes unfinished work after retry or restart without rerunning completed child tasks. Clients poll `GET /batchjob/{id}/status` for lightweight status and use `GET /batchjob/{id}` for the latest result snapshot. Once individual tasks finish, running jobs return a preliminary Bundle containing the results completed so far; the same endpoint returns the final Bundle when processing completes. On each results request, the status Observation text reports live task progress as `Batch job status: x% (m/n)`.

---

## API Endpoints

### Batch Jobs

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/batchjob` | Submit a job package run for a patient |
| `GET` | `/batchjob` | List all batch jobs as FHIR Parameters resources (`?include_patient=true`) |
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

### Job Packages (Questionnaires)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jobpackage` | Search Questionnaires (`?name=`, `?version=`) within the `smartchartui` context and return a flat list of Questionnaire resources |
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
| `LANGFUSE_OTEL_HOST` | *(none)* | Optional separate Langfuse OTEL ingestion host; defaults to `LANGFUSE_HOST` |
| `LANGFUSE_TRACING_ENVIRONMENT` | *(none)* | Optional environment label attached to Langfuse traces, such as `production` or `staging` |
| `OTEL_SEMCONV_STABILITY_OPT_IN` | *(none)* | Set to `gen_ai_latest_experimental` to remove LiteLLM's non-standard raw child span while retaining generation telemetry |
| `LANGFUSE_PROMPT_FETCH_TIMEOUT_SECONDS` | `5` | Maximum duration of each synchronous Langfuse prompt fetch attempt, executed outside the API event loop |
| `LANGFUSE_PROMPT_MAX_RETRIES` | `1` | Retries after a failed Langfuse prompt fetch before using the matching local prompt |
| `PROMPTS_DIR` | `./prompts` | Local prompt directory used when Langfuse is disabled, unavailable, or fails to return an individual prompt |
| `OAUTH2_JWKS_URL` | *(none)* | JWKS endpoint URL. Auth is **disabled** if not set |
| `OAUTH2_ISSUER` | *(none)* | Expected `iss` claim in JWT |
| `OAUTH2_AUDIENCE` | *(none)* | Expected `aud` claim in JWT |
| `DB_CONNECTION_STRING` | `sqlite+pysqlite:///rcapi_jobs.sqlite` | SQLAlchemy connection string |
| `DB_SCHEMA` | `rcapi` | DB schema name (ignored for SQLite) |
| `BATCH_WORKER_ENABLED` | `true` | Run the durable batch consumer inside the API process |
| `BATCH_WORKER_POLL_INTERVAL_SECONDS` | `2` | Seconds between checks for runnable and expired jobs |
| `BATCH_WORKER_LEASE_SECONDS` | `120` | Seconds a worker owns a claimed batch without renewal; keep this greater than the heartbeat interval |
| `BATCH_JOB_HEARTBEAT_INTERVAL_SECONDS` | `30` | Seconds between lease renewals while a batch is executing |
| `BATCH_JOB_MAX_ATTEMPTS` | `3` | Maximum claimed execution attempts before terminal error |
| `BATCH_JOB_RETRY_DELAY_SECONDS` | `30` | Seconds before a failed or expired attempt is runnable again |
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

`pixi` is used for local task orchestration. Python dependencies live in `pyproject.toml`, and `uv` creates the project environment for both local development and Docker builds.

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

Revision `0003` adds the embedded-worker queue, lease, retry, and logical-child uniqueness fields. Running batches renew their leases periodically. Workers continuously requeue expired attempts while retry capacity remains and mark a batch `error` only after `BATCH_JOB_MAX_ATTEMPTS` is exhausted. Partial result Bundles and already-completed or skipped child tasks are preserved across claims, retries, graceful shutdowns, and container restarts. The API and workers run in the same container. Each Hypercorn process starts one lease-coordinated consumer, so increasing the process count increases both HTTP capacity and maximum concurrent batch execution. Deploy only one RC-API container unless you intentionally scale replicas against the same database.

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

The server binds to `:8080` and starts two Hypercorn worker processes by default (configured in `hypercorn_config.toml`). Each process can serve requests independently and runs one embedded durable batch consumer coordinated through database leases.

Docker builds install dependencies with `uv` from the committed `uv.lock` file, while local development uses `pixi` tasks that wrap the same `uv`-managed environment. Local `.env` files are for developer machines only and are not copied into the image.

### Code Quality

```bash
# Type check
uv run pyright

# Lint
pixi run lint

# Format
pixi run format

# Run all pre-commit hooks
uv run pre-commit run --all-files
```

---

## Authentication

NOTE: generalized auth flow, should be able to support Auth0 and Keycloak using handlers

OAuth 2 Bearer token authentication is **optional**. When `OAUTH2_JWKS_URL` is set, all endpoints require a valid JWT. When it is not set, all endpoints are fully open (useful for local development).

### Token Requirements

- Tokens must be signed with **RS256** and verifiable against the JWKS endpoint
- The `iss` claim must match `OAUTH2_ISSUER` (if set)
- The `aud` claim must match `OAUTH2_AUDIENCE` (if set)
- **`DELETE` endpoints require `admin` in the token's `scope` claim**

### Request Header

```
Authorization: Bearer <token>
```

---

## Project Structure

```
RC-API/
├── alembic.ini                     # Alembic configuration
├── alembic/                        # Versioned database migrations
├── main.py                        # FastAPI app entry points
├── hypercorn_config.toml          # ASGI server configuration (binds :8080)
├── pyproject.toml                 # Python dependencies and tool configuration
├── pixi.toml                      # Local developer tasks and bootstrap commands
├── .env.example                   # Environment variable template
├── prompts/                       # Local LLM prompt files (.md)
│
├── src/
│   ├── routers/
│   │   ├── batchjob.py            # POST/GET/DELETE /batchjob
│   │   ├── jobpackage.py          # CRUD /jobpackage → HAPI FHIR Questionnaire
│   │   ├── group.py               # CRUD /group → HAPI FHIR Group
│   │   ├── library.py             # CRUD /library → HAPI FHIR Library
│   │   └── response.py            # CRUD /response → local DB
│   │
│   ├── services/
│   │   ├── batch_worker.py         # Embedded durable database-backed worker
│   │   ├── job_orchestrator.py    # Async batch job pipeline
│   │   ├── cql_executor.py        # HAPI FHIR Library/$evaluate wrapper
│   │   ├── llm_executor.py        # LiteLLM per-document execution
│   │   ├── fhir_proxy.py          # HAPI FHIR CRUD proxy
│   │   ├── fhir_context.py        # Patient DocumentReference fetcher
│   │   ├── prompt_loader.py       # Langfuse + local ./prompts/ loader
│   │   ├── job_state.py           # SQLAlchemy ORM + DB helpers
│   │   └── errorhandler.py        # FHIR OperationOutcome helpers
│   │
│   ├── models/
│   │   ├── job_request.py         # FHIR Parameters request model
│   │   ├── job_response.py        # Batch job status/result models
│   │   ├── response.py            # QuestionnaireResponse models
│   │   └── prompt.py              # Prompt metadata model
│   │
│   └── util/
│       ├── settings.py            # Environment variable config
│       └── auth.py                # OAuth 2 JWT validation dependencies
│
└── tests/                         # Pytest coverage for routers, services, and models
```
