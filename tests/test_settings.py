from src.util.settings import Settings


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
