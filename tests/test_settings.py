import importlib

import src.util.settings as settings


def test_root_path_uses_env_var(monkeypatch):
    monkeypatch.setenv("ROOT_PATH", "rc-api/")

    reloaded = importlib.reload(settings)
    assert reloaded.root_path == "/rc-api"

    monkeypatch.delenv("ROOT_PATH", raising=False)
    importlib.reload(reloaded)


def test_root_path_falls_back_to_deploy_url_path(monkeypatch):
    monkeypatch.delenv("ROOT_PATH", raising=False)
    monkeypatch.setenv("DEPLOY_URL", "https://example.org/api/v1/")

    reloaded = importlib.reload(settings)
    assert reloaded.root_path == "/api/v1"

    importlib.reload(reloaded)


def test_batch_worker_settings_use_safe_positive_defaults():
    assert settings.batch_worker_enabled is True
    assert settings.batch_worker_poll_interval_seconds > 0
    assert settings.batch_worker_lease_seconds > settings.batch_job_heartbeat_interval_seconds
    assert settings.batch_job_retry_delay_seconds > 0
    assert settings.batch_job_max_attempts > 0
