from src.util.settings import Settings, secret_value


def test_root_path_uses_env_var(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "rc-api/")

    configured = Settings()
    assert configured.root_path == "/rc-api"


def test_root_path_falls_back_to_deploy_url_path(monkeypatch):
    monkeypatch.delenv("ROOT_PATH", raising=False)
    monkeypatch.setenv("DEPLOY_URL", "https://example.org/api/v1/")

    configured = Settings()
    assert configured.root_path == "/api/v1"


def test_worker_lease_outlasts_heartbeat_interval():
    configured = Settings()
    assert configured.batch_worker_lease_seconds > configured.batch_job_heartbeat_interval_seconds


def test_secrets_are_masked_and_unwrapped_at_use(monkeypatch):
    monkeypatch.setenv("EXTERNAL_FHIR_SERVER_AUTH", "Bearer top-secret")
    monkeypatch.setenv("LITELLM_API_KEY", "llm-secret")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")

    configured = Settings()
    rendered = repr(configured) + configured.model_dump_json()

    assert "top-secret" not in rendered and "llm-secret" not in rendered
    assert secret_value(configured.external_fhir_server_auth) == "Bearer top-secret"
    assert configured.langfuse_secret_key is None


def test_cql_concurrency_uses_default_for_zero_env(monkeypatch):
    monkeypatch.setenv("CQL_MAX_CONCURRENCY", "0")

    configured = Settings()

    assert configured.cql_max_concurrency == 4


def test_batch_checkpoint_settings_use_defaults():
    configured = Settings()

    assert configured.batch_job_checkpoint_interval_seconds == 5.0
    assert configured.batch_job_checkpoint_every_tasks == 10


def test_batch_checkpoint_interval_allows_zero(monkeypatch):
    monkeypatch.setenv("BATCH_JOB_CHECKPOINT_INTERVAL_SECONDS", "0")

    configured = Settings()

    assert configured.batch_job_checkpoint_interval_seconds == 0.0


def test_batch_checkpoint_settings_invalid_values_use_defaults(monkeypatch):
    monkeypatch.setenv("BATCH_JOB_CHECKPOINT_INTERVAL_SECONDS", "not-a-number")
    monkeypatch.setenv("BATCH_JOB_CHECKPOINT_EVERY_TASKS", "0")

    configured = Settings()

    assert configured.batch_job_checkpoint_interval_seconds == 5.0
    assert configured.batch_job_checkpoint_every_tasks == 10


def test_langfuse_prompt_cache_ttl_allows_zero(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PROMPT_CACHE_TTL_SECONDS", "0")

    configured = Settings()

    assert configured.langfuse_prompt_cache_ttl_seconds == 0.0


def test_langfuse_prompt_settings_invalid_values_use_defaults(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PROMPT_CACHE_TTL_SECONDS", "not-a-number")
    monkeypatch.setenv("LANGFUSE_PROMPT_FETCH_CONCURRENCY", "0")

    configured = Settings()

    assert configured.langfuse_prompt_cache_ttl_seconds == 60.0
    assert configured.langfuse_prompt_fetch_concurrency == 4
