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
