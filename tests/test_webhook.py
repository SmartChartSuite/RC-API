"""
Tests for webhook.py
Endpoint covered:
  POST /webhook
"""

GITHUB_WEBHOOK_PAYLOAD = {
    "ref": "refs/heads/main",
    "repository": {
        "id": 123456,
        "name": "knowledgebase",
        "full_name": "org/knowledgebase",
        "clone_url": "https://github.com/org/knowledgebase.git",
        "ssh_url": "git@github.com:org/knowledgebase.git",
    },
}


class TestWebhook:
    def test_webhook_returns_acknowledged(self, client, monkeypatch):
        """POST /webhook → returns 'Acknowledged' string on success."""
        monkeypatch.setattr(
            "src.routers.webhook.clone_repo_to_temp_folder",
            lambda url: None,  # No-op: don't actually clone
        )
        response = client.post("/webhook", json=GITHUB_WEBHOOK_PAYLOAD)
        assert response.status_code == 200
        assert response.json() == "Acknowledged"

    def test_webhook_calls_clone_with_ssh_url(self, client, monkeypatch):
        """POST /webhook → clone_repo_to_temp_folder is called with the ssh_url."""
        captured_url = []

        def mock_clone(url):
            captured_url.append(url)

        monkeypatch.setattr("src.routers.webhook.clone_repo_to_temp_folder", mock_clone)

        client.post("/webhook", json=GITHUB_WEBHOOK_PAYLOAD)
        assert len(captured_url) == 1
        assert captured_url[0] == GITHUB_WEBHOOK_PAYLOAD["repository"]["ssh_url"]

    def test_webhook_uses_ssh_not_clone_url(self, client, monkeypatch):
        """POST /webhook → SSH URL (not HTTPS clone_url) is passed to clone function."""
        captured_url = []

        def mock_clone(url):
            captured_url.append(url)

        monkeypatch.setattr("src.routers.webhook.clone_repo_to_temp_folder", mock_clone)
        client.post("/webhook", json=GITHUB_WEBHOOK_PAYLOAD)

        assert captured_url[0] == "git@github.com:org/knowledgebase.git"
        assert "https://" not in captured_url[0]

    def test_webhook_missing_repository_key_raises(self, client, monkeypatch):
        """POST /webhook with malformed payload → error (KeyError on missing key)."""
        monkeypatch.setattr(
            "src.routers.webhook.clone_repo_to_temp_folder",
            lambda url: None,
        )
        # Payload is missing "repository" key entirely
        response = client.post("/webhook", json={"ref": "refs/heads/main"})
        # Should raise a server error (500) since the router accesses keys directly
        assert response.status_code == 500
