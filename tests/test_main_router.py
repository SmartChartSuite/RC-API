"""
Tests for main_router.py
Endpoints covered:
  GET /
  GET /health
  GET /config
"""


class TestRootEndpoint:
    def test_root_returns_operation_outcome(self, client):
        """GET / should return a FHIR OperationOutcome with 'processing' code."""
        response = client.get("/")
        assert response.status_code == 200
        body = response.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "processing"

    def test_root_message_content(self, client):
        """GET / diagnostic message should reference the root URL."""
        response = client.get("/")
        body = response.json()
        assert "base URL" in body["issue"][0]["diagnostics"].lower() or "root" in body["issue"][0]["diagnostics"].lower()


class TestHealthEndpoint:
    def test_health_returns_200(self, client, monkeypatch):
        """GET /health should return 200 with a dict response."""
        monkeypatch.setattr(
            "src.routers.main_router.get_health_of_stack",
            lambda: {"status": "ok", "services": {}},
        )
        response = client.get("/health")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    def test_health_response_has_expected_shape(self, client, monkeypatch):
        """GET /health response dict should represent service health."""
        mock_health = {"status": "ok", "CQF_RULER_R4": "reachable"}
        monkeypatch.setattr("src.routers.main_router.get_health_of_stack", lambda: mock_health)
        response = client.get("/health")
        body = response.json()
        assert "status" in body or len(body) > 0  # Some health shape returned


class TestConfigEndpoint:
    def test_config_empty_when_no_primary_identifier(self, client, monkeypatch):
        """GET /config returns empty dict when PRIMARYIDENTIFIER_SYSTEM is not set."""
        monkeypatch.setattr("src.routers.main_router.config_endpoint", {})
        response = client.get("/config")
        assert response.status_code == 200
        assert response.json() == {}

    def test_config_populated_with_primary_identifier(self, client, monkeypatch):
        """GET /config returns ConfigEndpointModel when primary identifier is configured."""
        from src.util.settings import ConfigEndpointModel, ConfigEndpointPrimaryIdentifier

        expected = ConfigEndpointModel(
            primaryIdentifier=ConfigEndpointPrimaryIdentifier(
                system="http://example.org/pid",
                label="MRN",
            )
        )
        monkeypatch.setattr("src.routers.main_router.config_endpoint", expected)
        response = client.get("/config")
        assert response.status_code == 200
        body = response.json()
        assert "primaryIdentifier" in body
        assert body["primaryIdentifier"]["system"] == "http://example.org/pid"
        assert body["primaryIdentifier"]["label"] == "MRN"
