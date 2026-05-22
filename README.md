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
  └── /response    ─── QuestionnaireResponse CRUD ────────── Local DB (patient data)
```

Batch jobs run **asynchronously** in a background task. Clients poll `GET /batchjob/{id}` for status and fetch results with `GET /batchjob/{id}/results` when complete.

---

## API Endpoints

### Batch Jobs

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/batchjob` | Submit a job package run for a patient |
| `GET` | `/batchjob` | List all batch jobs as FHIR Parameters resources (`?include_patient=true`) |
| `GET` | `/batchjob/{id}/status` | Poll status of a batch job as a FHIR Parameters resource |
| `GET` | `/batchjob/{id}` | Retrieve the full FHIR result Bundle |
| `DELETE` | `/batchjob/{id}` | Delete a batch job *(requires `admin` scope)* |

**`POST /batchjob` request body** (FHIR Parameters):
```json
{
  "resourceType": "Parameters",
  "parameter": [
    { "name": "patientId",        "valueString": "12345" },
    { "name": "jobPackage",       "valueString": "SyphilisRegistry" },
    { "name": "jobPackageVersion","valueString": "1.0" },
    { "name": "job",              "valueString": "SyphilisHistory" },
    { "name": "job",              "valueString": "prompts/2025_08/syphilis/ig_hc" }
  ]
}
```

Use one or more repeated `job` parameters to run only the named CQL library or prompt entries from the job package. Prompt matching accepts either the full prompt path or a unique prompt file name.

### Job Packages (Questionnaires)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jobpackage` | Search Questionnaires (`?name=`, `?version=`) |
| `GET` | `/jobpackage/{id}` | Get a Questionnaire |
| `POST` | `/jobpackage` | Create a Questionnaire |
| `PUT` | `/jobpackage/{id}` | Update a Questionnaire |
| `DELETE` | `/jobpackage/{id}` | Delete a Questionnaire *(requires `admin` scope)* |

### Groups, Libraries, Responses

Same CRUD pattern (`GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}`) for:
- `/group` — FHIR Groups with implicit Patient member inclusion on search
- `/library` — CQL Library resources
- `/response` — Patient-linked QuestionnaireResponses (stored in local DB only)

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `ok` or `degraded` with a list of missing config vars |

Interactive docs are available at `/docs` (when `API_DOCS=true`).

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
| `LANGFUSE_PUBLIC_KEY` | *(none)* | Langfuse public key. All three `LANGFUSE_*` vars required together |
| `LANGFUSE_SECRET_KEY` | *(none)* | Langfuse secret key |
| `LANGFUSE_HOST` | *(none)* | Langfuse host URL |
| `PROMPTS_DIR` | `./prompts` | Local prompt directory (fallback when Langfuse not configured) |
| `OAUTH2_JWKS_URL` | *(none)* | JWKS endpoint URL. Auth is **disabled** if not set |
| `OAUTH2_ISSUER` | *(none)* | Expected `iss` claim in JWT |
| `OAUTH2_AUDIENCE` | *(none)* | Expected `aud` claim in JWT |
| `DB_CONNECTION_STRING` | `sqlite+pysqlite:///rcapi_jobs.sqlite` | SQLAlchemy connection string |
| `DB_SCHEMA` | `rcapi` | DB schema name (ignored for SQLite) |
| `API_DOCS` | `true` | Set to `false` to disable `/docs` and `/redoc` |
| `DEPLOY_URL` | `http://example.org/` | Base URL used in Observation identifiers |
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
```

`pixi` is used for local task orchestration. Python dependencies live in `pyproject.toml`, and `uv` creates the project environment for both local development and Docker builds.

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
- `pre-commit` runs formatting and validation hooks before code is committed.

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

If Langfuse is not configured, LLM prompts are loaded from `./prompts/` using the path structure defined in Questionnaire `unstructuredTask` extensions:

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

```bash
# Development (auto-reload)
pixi run serve

# Production
pixi run serve-prod
```

The server binds to `:8080` by default (configured in `hypercorn_config.toml`).

Docker builds install dependencies with `uv` from the committed `uv.lock` file, while local development uses `pixi` tasks that wrap the same `uv`-managed environment. Local `.env` files are for developer machines only and are not copied into the image.

### Code Quality

```bash
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
├── src_v0/                        # Archived v0 source (read-only reference)
└── tests_v0/                      # Archived v0 tests (read-only reference)
```
