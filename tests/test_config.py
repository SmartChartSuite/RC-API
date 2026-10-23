"""Tests the `/config` endpoint's configured identifier response and authentication boundary.

The tests patch `settings` and use FastAPI `TestClient`; `/health` and docs stay public, while endpoint
implementation belongs in `src.routers.config`."""

from fastapi.testclient import TestClient

from main import app
from src.models.config import ConfigEndpointModel
from src.routers import config
from src.util.settings import settings


async def test_get_config_returns_empty_dict_when_primary_identifier_is_unset(monkeypatch):
    monkeypatch.setattr(settings, "config_endpoint", {})

    result = await config.get_config()

    assert result == {}


def test_config_endpoint_returns_primary_identifier(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_enabled", False)
    monkeypatch.setattr(
        settings,
        "config_endpoint",
        ConfigEndpointModel.model_validate(
            {
                "primaryIdentifier": {
                    "system": "http://example.org/fhir/identifier-system",
                    "label": "MRN",
                }
            }
        ),
    )
    client = TestClient(app)

    response = client.get("/config")

    assert response.status_code == 200
    assert response.json() == {
        "primaryIdentifier": {
            "system": "http://example.org/fhir/identifier-system",
            "label": "MRN",
        }
    }


def test_config_requires_auth_but_health_and_docs_remain_public(monkeypatch):
    monkeypatch.setattr(settings, "oauth2_enabled", True)
    client = TestClient(app)

    assert client.get("/config").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
