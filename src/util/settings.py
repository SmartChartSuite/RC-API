"""v1 settings — all environment variables with graceful degraded-mode handling.

Required vars (EXTERNAL_FHIR_SERVER_URL, HAPI_FHIR_CQL_EXECUTION_URL) are loaded via
_require() which records the error but does NOT raise, allowing the API to start inside
Docker even with missing config. Endpoints check config_errors and return a FHIR
OperationOutcome (HTTP 503) rather than crashing.
"""

import os

from loguru import logger
import litellm

from src.models.config import ConfigEndpointModel

config_errors: dict[str, str] = {}


def _get(var: str, default: str | None = None) -> str | None:
    """Read an env var and normalize common docker --env-file formatting artifacts."""
    value = os.environ.get(var)
    if value is None:
        return default
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _require(var: str) -> str | None:
    """Load a required env var. Records error without raising if missing."""
    val = _get(var)
    if not val:
        msg = f"Required environment variable '{var}' is not set."
        logger.error(msg)
        config_errors[var] = msg
    return val


# FHIR servers
external_fhir_server_url: str | None = _require("EXTERNAL_FHIR_SERVER_URL")
external_fhir_server_auth: str = _get("EXTERNAL_FHIR_SERVER_AUTH") or ""

# HAPI FHIR CQL Execution Service
# Library IDs are the CamelCase resource name (e.g. "SyphilisRegistry")
# POST {hapi_fhir_cql_execution_url}/Library/{LibraryName}/$evaluate
hapi_fhir_cql_execution_url: str | None = _require("HAPI_FHIR_CQL_EXECUTION_URL")

# LiteLLM (all three required together for LLM operations)
litellm_model: str | None = _get("LITELLM_MODEL")
litellm_api_base: str | None = _get("LITELLM_API_BASE")
litellm_api_key: str | None = _get("LITELLM_API_KEY")
use_llm: bool = bool(litellm_model and litellm_api_base and litellm_api_key)
if any([litellm_model, litellm_api_base, litellm_api_key]) and not use_llm:
    logger.warning("Partial LiteLLM config — set LITELLM_MODEL, LITELLM_API_BASE, and LITELLM_API_KEY together.")

# Langfuse (all three required together for prompt retrieval)
langfuse_public_key: str | None = _get("LANGFUSE_PUBLIC_KEY")
langfuse_secret_key: str | None = _get("LANGFUSE_SECRET_KEY")
langfuse_host: str | None = _get("LANGFUSE_HOST")
use_langfuse: bool = bool(langfuse_public_key and langfuse_secret_key and langfuse_host)
if any([langfuse_public_key, langfuse_secret_key, langfuse_host]) and not use_langfuse:
    logger.warning("Partial Langfuse config — set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST together.")

# Prompts folder (fallback when Langfuse not configured)
prompts_dir: str = _get("PROMPTS_DIR") or "./prompts"

# Database
db_connection_string: str = _get("DB_CONNECTION_STRING") or "sqlite+pysqlite:///rcapi_jobs.sqlite"
db_schema: str = _get("DB_SCHEMA") or "rcapi"

# OAuth 2 (optional; auth disabled if OAUTH2_JWKS_URL is not set)
oauth2_jwks_url: str | None = _get("OAUTH2_JWKS_URL")
oauth2_issuer: str | None = _get("OAUTH2_ISSUER")
oauth2_audience: str | None = _get("OAUTH2_AUDIENCE")
oauth2_enabled: bool = bool(oauth2_jwks_url)
if oauth2_jwks_url and not oauth2_issuer:
    logger.warning("OAUTH2_JWKS_URL is set but OAUTH2_ISSUER is missing — token issuer will not be validated.")

# Misc
api_docs: str = _get("API_DOCS") or "true"
deploy_url: str = _get("DEPLOY_URL") or "http://example.org/"
if deploy_url[-1] != "/":
    deploy_url += "/"
root_path: str = deploy_url.split("/")[-1]
log_level: str = (_get("LOG_LEVEL") or "INFO").upper()
primary_identifier_system: str | None = _get("PRIMARYIDENTIFIER_SYSTEM")
primary_identifier_label: str | None = _get("PRIMARYIDENTIFIER_LABEL")
config_endpoint: ConfigEndpointModel | dict = (
    ConfigEndpointModel.model_validate(
        {
            "primaryIdentifier": {
                "system": primary_identifier_system,
                "label": primary_identifier_label,
            }
        }
    )
    if primary_identifier_system
    else {}
)

if use_llm and use_langfuse:
    litellm.callbacks = ["langfuse_otel"]
    litellm.suppress_debug_info = True
