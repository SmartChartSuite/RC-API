"""Load and validate typed application settings with graceful degraded-mode handling.

Required FHIR URLs and OAuth settings are checked without raising during startup;
missing or invalid values are recorded in `config_errors` so protected endpoints
can return a FHIR OperationOutcome (HTTP 503). This module also parses environment
values and derives runtime flags and client-facing configuration. It does not
implement authentication, transport, persistence, or domain workflows; those
belong to `src.util.auth`, `src.util.http_clients`, and `src.services`.
"""

import math
from typing import Annotated, Any
from urllib.parse import urlparse

from loguru import logger
from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from src.models.config import ConfigEndpointModel


class ConfigurationError(RuntimeError):
    """Raised when code needs a setting that was not configured (survives ``python -O``)."""


def require_setting(value: str | None, name: str) -> str:
    """Return a configured setting or raise ConfigurationError; narrows the type for callers."""
    if not value:
        raise ConfigurationError(f"Required setting '{name}' is not configured.")
    return value


def secret_value(secret: SecretStr | None) -> str | None:
    """Unwrap a secret setting at its point of use; empty secrets count as unset."""
    value = secret.get_secret_value() if secret is not None else None
    return value or None


def _normalize_env_value(value: Any) -> Any:
    """Normalize whitespace and docker ``--env-file`` quote artifacts."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _normalize_root_path(value: str | None) -> str:
    if not value or value == "/":
        return ""
    normalized = value.strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _parse_bool(raw: str, env_name: str, default: bool) -> bool:
    normalized = raw.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning(f"{env_name}='{raw}' is not a valid boolean — using default {default}.")
    return default


def _parse_positive_int(raw: str, env_name: str, default: int) -> int:
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(f"{env_name}='{raw}' is not a valid integer — using default {default}.")
        return default
    if parsed < 1:
        logger.warning(f"{env_name}={parsed} must be >= 1 — using default {default}.")
        return default
    return parsed


def _parse_nonnegative_float(raw: str, env_name: str, default: float) -> float:
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning(f"{env_name}='{raw}' is not a valid number — using default {default}.")
        return default
    if not math.isfinite(parsed) or parsed < 0:
        logger.warning(f"{env_name}={parsed} must be finite and >= 0 — using default {default}.")
        return default
    return parsed


class Settings(BaseSettings):
    """Application configuration loaded from environment variables (field name upper-cased, unless aliased)."""

    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore", populate_by_name=True)

    # FHIR servers (required; missing or invalid values degrade to None and are recorded in config_errors)
    external_fhir_server_url: str | None = None
    # Secrets are SecretStr so printing or dumping settings never reveals them; unwrap with secret_value().
    external_fhir_server_auth: SecretStr | None = None
    # HAPI FHIR CQL Execution Service: POST {hapi_fhir_cql_execution_url}/Library/{LibraryName}/$evaluate
    hapi_fhir_cql_execution_url: str | None = None
    # Max concurrent in-flight CQL library evaluations across batch jobs.
    cql_max_concurrency: int = 4

    # LiteLLM (all three required together for LLM operations)
    litellm_model: str | None = None
    litellm_api_base: str | None = None
    litellm_api_key: SecretStr | None = None
    litellm_model_reasoning_effort: str | None = None
    # Enable provider-side prompt caching for models that support it.
    litellm_prompt_cache_enabled: bool = True
    # Max concurrent in-flight LLM completion calls across all prompts/documents in a job.
    llm_max_concurrency: int = 8

    # Outbound DocumentReference attachment fetching (SSRF protection and payload limits).
    # Attachments are fetched only from the external FHIR server origin plus ATTACHMENT_ALLOWED_HOSTS.
    doc_fetch_max_concurrency: int = 8
    doc_max_count: int = 500
    doc_max_bytes: int = 5_000_000
    attachment_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Embedded durable batch worker
    batch_worker_enabled: bool = True
    batch_worker_poll_interval_seconds: int = 2
    batch_worker_lease_seconds: int = 120
    batch_job_heartbeat_interval_seconds: int = 30
    batch_job_checkpoint_interval_seconds: float = 5.0
    batch_job_checkpoint_every_tasks: int = 10
    batch_job_max_attempts: int = 3
    batch_job_retry_delay_seconds: int = 30
    batch_job_recovery_chunk_size: int = 100
    batch_job_stream_poll_interval_seconds: float = 5.0

    # Langfuse (all three required together for prompt retrieval)
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None
    langfuse_prompt_fetch_timeout_seconds: int = 5
    langfuse_prompt_max_retries: int = 1
    langfuse_prompt_cache_ttl_seconds: float = 60.0
    langfuse_prompt_fetch_concurrency: int = 4

    # Prompts folder (fallback when Langfuse is not configured) and database
    prompts_dir: str = "./prompts"
    # Process-local Patient summary cache used by batch-list consumers.
    patient_summary_cache_ttl_seconds: float = 900.0
    patient_summary_cache_max_entries: int = 1000
    db_connection_string: str = "sqlite+pysqlite:///rcapi_jobs.sqlite"
    db_schema: str = "rcapi"

    # OAuth 2 — fail closed. Enforced unless AUTH_DISABLED=true (local development only); when enforced,
    # OAUTH2_JWKS_URL, OAUTH2_ISSUER, and OAUTH2_AUDIENCE are required or protected endpoints return 503.
    auth_disabled: bool = False
    oauth2_jwks_url: str | None = None
    oauth2_issuer: str | None = None
    oauth2_audience: str | None = None
    oauth2_jwks_cache_seconds: int = 3600
    oauth2_userinfo_timeout_seconds: float = 3.0
    oauth2_userinfo_staleness_seconds: float = 86_400.0

    # CORS — explicit allowlist. "*" is permitted but disables credentialed requests.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Misc
    deploy_url: str = "http://example.org/"
    root_path: str = ""
    log_level: str = "INFO"
    primary_identifier_system: str | None = Field(default=None, validation_alias="PRIMARYIDENTIFIER_SYSTEM")
    primary_identifier_label: str | None = Field(default=None, validation_alias="PRIMARYIDENTIFIER_LABEL")

    # Derived in _derive_values; plain mutable fields so runtime code and tests can override them.
    config_errors: dict[str, str] = Field(default_factory=dict, exclude=True)
    use_llm: bool = False
    use_langfuse: bool = False
    oauth2_enabled: bool = True
    oauth2_missing_config: list[str] = Field(default_factory=list)
    cors_allow_credentials: bool = False
    config_endpoint: ConfigEndpointModel | dict = Field(default_factory=dict)

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_env_value(cls, value: Any, info: ValidationInfo) -> Any:
        """Normalize env strings; invalid booleans and positive integers warn and fall back to the default."""
        if not isinstance(value, str) or info.field_name is None:
            return value
        value = _normalize_env_value(value)
        field = cls.model_fields[info.field_name]
        env_name = field.validation_alias if isinstance(field.validation_alias, str) else info.field_name.upper()
        if field.annotation is bool:
            return _parse_bool(value, env_name, field.default)
        if field.annotation is int:
            return _parse_positive_int(value, env_name, field.default)
        if field.annotation is float:
            return _parse_nonnegative_float(value, env_name, field.default)
        return value

    @field_validator("cors_allow_origins", "attachment_allowed_hosts", mode="before")
    @classmethod
    def _parse_csv(cls, value: Any) -> Any:
        """Read the comma-separated env format instead of JSON."""
        if isinstance(value, str):
            return [item.strip() for item in _normalize_env_value(value).split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _derive_values(self) -> Settings:
        """Record config errors and derive dependent values without failing startup."""
        # Empty secrets count as unset so the "configured together" checks below stay correct.
        for field_name in ("external_fhir_server_auth", "litellm_api_key", "langfuse_secret_key"):
            if secret_value(getattr(self, field_name)) is None:
                setattr(self, field_name, None)
        self.config_errors = {}
        for field_name in ("external_fhir_server_url", "hapi_fhir_cql_execution_url"):
            env_name = field_name.upper()
            value = getattr(self, field_name)
            if not value:
                message = f"Required environment variable '{env_name}' is not set."
            elif not _is_http_url(value):
                message = f"Environment variable '{env_name}' must be an absolute http(s) URL."
                setattr(self, field_name, None)
            else:
                continue
            logger.error(message)
            self.config_errors[env_name] = message

        self.use_llm = bool(self.litellm_model and self.litellm_api_base and self.litellm_api_key)
        if any([self.litellm_model, self.litellm_api_base, self.litellm_api_key]) and not self.use_llm:
            logger.warning("Partial LiteLLM config — set LITELLM_MODEL, LITELLM_API_BASE, and LITELLM_API_KEY together.")

        if self.batch_worker_lease_seconds <= self.batch_job_heartbeat_interval_seconds * 2:
            safe_lease = self.batch_job_heartbeat_interval_seconds * 3
            logger.warning(
                f"BATCH_WORKER_LEASE_SECONDS={self.batch_worker_lease_seconds} must exceed twice BATCH_JOB_HEARTBEAT_INTERVAL_SECONDS="
                f"{self.batch_job_heartbeat_interval_seconds} to avoid expired active leases — using {safe_lease}."
            )
            self.batch_worker_lease_seconds = safe_lease

        self.use_langfuse = bool(self.langfuse_public_key and self.langfuse_secret_key and self.langfuse_host)
        if any([self.langfuse_public_key, self.langfuse_secret_key, self.langfuse_host]) and not self.use_langfuse:
            logger.warning("Partial Langfuse config — set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST together.")

        self.oauth2_enabled = not self.auth_disabled
        self.oauth2_missing_config = []
        if self.auth_disabled:
            logger.warning("AUTH_DISABLED=true — all endpoints are accessible without authentication. Never use this in production.")
        else:
            oauth2_required = (("OAUTH2_JWKS_URL", self.oauth2_jwks_url), ("OAUTH2_ISSUER", self.oauth2_issuer), ("OAUTH2_AUDIENCE", self.oauth2_audience))
            self.oauth2_missing_config = [name for name, value in oauth2_required if not value]
            for name in self.oauth2_missing_config:
                message = f"Authentication is enabled but '{name}' is not set. Protected endpoints will return 503 until configured (or set AUTH_DISABLED=true for local development)."
                logger.error(message)
                self.config_errors[name] = message

        self.cors_allow_credentials = bool(self.cors_allow_origins) and "*" not in self.cors_allow_origins
        self.attachment_allowed_hosts = [host.casefold() for host in self.attachment_allowed_hosts]

        # Empty env values fall back to defaults, as before.
        self.prompts_dir = self.prompts_dir or "./prompts"
        self.db_connection_string = self.db_connection_string or "sqlite+pysqlite:///rcapi_jobs.sqlite"
        self.db_schema = self.db_schema or "rcapi"
        self.deploy_url = self.deploy_url or "http://example.org/"
        if not self.deploy_url.endswith("/"):
            self.deploy_url += "/"
        self.root_path = _normalize_root_path(self.root_path or urlparse(self.deploy_url).path)
        self.log_level = (self.log_level or "INFO").upper()
        self.config_endpoint = (
            ConfigEndpointModel.model_validate({"primaryIdentifier": {"system": self.primary_identifier_system, "label": self.primary_identifier_label}}) if self.primary_identifier_system else {}
        )
        return self


# Process-wide settings. Read attributes at call time (settings.x) so overrides take effect everywhere.
settings = Settings()
