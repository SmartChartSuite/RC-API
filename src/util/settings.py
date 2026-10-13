"""v1 settings — all environment variables with graceful degraded-mode handling.

Required vars (EXTERNAL_FHIR_SERVER_URL, HAPI_FHIR_CQL_EXECUTION_URL) are loaded via
_require() which records the error but does NOT raise, allowing the API to start inside
Docker even with missing config. Endpoints check config_errors and return a FHIR
OperationOutcome (HTTP 503) rather than crashing.
"""

import os
from urllib.parse import urlparse

from loguru import logger

from src.models.config import ConfigEndpointModel

config_errors: dict[str, str] = {}


class ConfigurationError(RuntimeError):
    """Raised when code needs a setting that was not configured (survives `python -O`, unlike assert)."""


def require_setting(value: str | None, name: str) -> str:
    """Return a configured setting or raise ConfigurationError; narrows the type for callers."""
    if not value:
        raise ConfigurationError(f"Required setting '{name}' is not configured.")
    return value


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


def _require_url(var: str) -> str | None:
    """Load a required http(s) URL env var. Records an error without raising if missing or malformed."""
    val = _require(var)
    if val:
        parsed = urlparse(val)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            msg = f"Environment variable '{var}' must be an absolute http(s) URL."
            logger.error(msg)
            config_errors[var] = msg
            return None
    return val


def _normalize_root_path(value: str | None) -> str:
    if not value or value == "/":
        return ""
    normalized = value.strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")


# FHIR servers
external_fhir_server_url: str | None = _require_url("EXTERNAL_FHIR_SERVER_URL")
external_fhir_server_auth: str = _get("EXTERNAL_FHIR_SERVER_AUTH") or ""

# HAPI FHIR CQL Execution Service
# Library IDs are the CamelCase resource name (e.g. "SyphilisRegistry")
# POST {hapi_fhir_cql_execution_url}/Library/{LibraryName}/$evaluate
hapi_fhir_cql_execution_url: str | None = _require_url("HAPI_FHIR_CQL_EXECUTION_URL")

# LiteLLM (all three required together for LLM operations)
litellm_model: str | None = _get("LITELLM_MODEL")
litellm_api_base: str | None = _get("LITELLM_API_BASE")
litellm_api_key: str | None = _get("LITELLM_API_KEY")
litellm_model_reasoning_effort: str | None = _get("LITELLM_MODEL_REASONING_EFFORT")
use_llm: bool = bool(litellm_model and litellm_api_base and litellm_api_key)
if any([litellm_model, litellm_api_base, litellm_api_key]) and not use_llm:
    logger.warning("Partial LiteLLM config — set LITELLM_MODEL, LITELLM_API_BASE, and LITELLM_API_KEY together.")


def _bool(var: str, default: bool) -> bool:
    """Read a boolean environment variable with a safe default."""
    raw = _get(var)
    if raw is None:
        return default
    normalized = raw.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning(f"{var}='{raw}' is not a valid boolean — using default {default}.")
    return default


# Enable provider-side prompt caching for models that support it.
litellm_prompt_cache_enabled: bool = _bool("LITELLM_PROMPT_CACHE_ENABLED", True)


def _positive_int(var: str, default: int) -> int:
    """Read a positive-integer env var, falling back to default on missing/invalid input."""
    raw = _get(var)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(f"{var}='{raw}' is not a valid integer — using default {default}.")
        return default
    if parsed < 1:
        logger.warning(f"{var}={parsed} must be >= 1 — using default {default}.")
        return default
    return parsed


# Max concurrent in-flight LLM completion calls across all prompts/documents in a job.
llm_max_concurrency: int = _positive_int("LLM_MAX_CONCURRENCY", 8)
# Max concurrent attachment fetches when loading DocumentReference content.
doc_fetch_max_concurrency: int = _positive_int("DOC_FETCH_MAX_CONCURRENCY", 8)


# Embedded durable batch worker
batch_worker_enabled: bool = _bool("BATCH_WORKER_ENABLED", True)
batch_worker_poll_interval_seconds: int = _positive_int("BATCH_WORKER_POLL_INTERVAL_SECONDS", 2)
batch_worker_lease_seconds: int = _positive_int("BATCH_WORKER_LEASE_SECONDS", 120)
batch_job_heartbeat_interval_seconds: int = _positive_int("BATCH_JOB_HEARTBEAT_INTERVAL_SECONDS", 30)
batch_job_max_attempts: int = _positive_int("BATCH_JOB_MAX_ATTEMPTS", 3)
batch_job_retry_delay_seconds: int = _positive_int("BATCH_JOB_RETRY_DELAY_SECONDS", 30)
if batch_worker_lease_seconds <= batch_job_heartbeat_interval_seconds * 2:
    _safe_lease = batch_job_heartbeat_interval_seconds * 3
    logger.warning(
        f"BATCH_WORKER_LEASE_SECONDS={batch_worker_lease_seconds} must exceed twice BATCH_JOB_HEARTBEAT_INTERVAL_SECONDS="
        f"{batch_job_heartbeat_interval_seconds} to avoid expired active leases — using {_safe_lease}."
    )
    batch_worker_lease_seconds = _safe_lease

# Langfuse (all three required together for prompt retrieval)
langfuse_public_key: str | None = _get("LANGFUSE_PUBLIC_KEY")
langfuse_secret_key: str | None = _get("LANGFUSE_SECRET_KEY")
langfuse_host: str | None = _get("LANGFUSE_HOST")
langfuse_prompt_fetch_timeout_seconds: int = _positive_int("LANGFUSE_PROMPT_FETCH_TIMEOUT_SECONDS", 5)
langfuse_prompt_max_retries: int = _positive_int("LANGFUSE_PROMPT_MAX_RETRIES", 1)
use_langfuse: bool = bool(langfuse_public_key and langfuse_secret_key and langfuse_host)
if any([langfuse_public_key, langfuse_secret_key, langfuse_host]) and not use_langfuse:
    logger.warning("Partial Langfuse config — set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST together.")

# Prompts folder (fallback when Langfuse not configured)
prompts_dir: str = _get("PROMPTS_DIR") or "./prompts"

# Database
db_connection_string: str = _get("DB_CONNECTION_STRING") or "sqlite+pysqlite:///rcapi_jobs.sqlite"
db_schema: str = _get("DB_SCHEMA") or "rcapi"

# OAuth 2 — fail closed. Authentication is enforced unless AUTH_DISABLED=true is set
# explicitly (intended for local development only). When enforced, OAUTH2_JWKS_URL,
# OAUTH2_ISSUER, and OAUTH2_AUDIENCE are all required; if any is missing, protected
# endpoints reject every request with 503 rather than silently allowing anonymous access.
auth_disabled: bool = _bool("AUTH_DISABLED", False)
oauth2_jwks_url: str | None = _get("OAUTH2_JWKS_URL")
oauth2_issuer: str | None = _get("OAUTH2_ISSUER")
oauth2_audience: str | None = _get("OAUTH2_AUDIENCE")
oauth2_jwks_cache_seconds: int = _positive_int("OAUTH2_JWKS_CACHE_SECONDS", 3600)
oauth2_enabled: bool = not auth_disabled
oauth2_missing_config: list[str] = []
if auth_disabled:
    logger.warning("AUTH_DISABLED=true — all endpoints are accessible without authentication. Never use this in production.")
else:
    oauth2_missing_config = [name for name, value in (("OAUTH2_JWKS_URL", oauth2_jwks_url), ("OAUTH2_ISSUER", oauth2_issuer), ("OAUTH2_AUDIENCE", oauth2_audience)) if not value]
    for name in oauth2_missing_config:
        msg = f"Authentication is enabled but '{name}' is not set. Protected endpoints will return 503 until configured (or set AUTH_DISABLED=true for local development)."
        logger.error(msg)
        config_errors[name] = msg


def _csv(var: str) -> list[str]:
    """Read a comma-separated env var into a list of trimmed, non-empty values."""
    raw = _get(var)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# CORS — explicit allowlist. "*" is permitted but disables credentialed requests.
cors_allow_origins: list[str] = _csv("CORS_ALLOW_ORIGINS")
cors_allow_credentials: bool = bool(cors_allow_origins) and "*" not in cors_allow_origins

# Outbound DocumentReference attachment fetching (SSRF protection and payload limits).
# Attachments are fetched only from the external FHIR server origin plus these extra hosts.
attachment_allowed_hosts: list[str] = [host.casefold() for host in _csv("ATTACHMENT_ALLOWED_HOSTS")]
doc_max_count: int = _positive_int("DOC_MAX_COUNT", 500)
doc_max_bytes: int = _positive_int("DOC_MAX_BYTES", 5_000_000)

# Misc
deploy_url: str = _get("DEPLOY_URL") or "http://example.org/"
if deploy_url[-1] != "/":
    deploy_url += "/"
root_path: str = _normalize_root_path(_get("ROOT_PATH") or urlparse(deploy_url).path)
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
