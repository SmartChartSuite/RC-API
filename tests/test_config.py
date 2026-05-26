from fastapi.testclient import TestClient

from main import app
from src.models.config import ConfigEndpointModel
from src.routers import config


async def test_get_config_returns_empty_dict_when_primary_identifier_is_unset(monkeypatch):
    monkeypatch.setattr(config, "config_endpoint", {})

    result = await config.get_config()

    assert result == {}


async def test_get_config_returns_primary_identifier_payload(monkeypatch):
    payload = ConfigEndpointModel.model_validate(
        {
            "primaryIdentifier": {
                "system": "http://example.org/fhir/identifier-system",
                "label": "MRN",
            }
        }
    )
    monkeypatch.setattr(config, "config_endpoint", payload)

    result = await config.get_config()

    assert isinstance(result, ConfigEndpointModel)
    assert result.primaryIdentifier is not None
    assert result.primaryIdentifier.system == "http://example.org/fhir/identifier-system"
    assert result.primaryIdentifier.label == "MRN"


def test_config_endpoint_is_registered(monkeypatch):
    monkeypatch.setattr(
        config,
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
