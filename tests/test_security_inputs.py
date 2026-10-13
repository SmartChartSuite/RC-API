"""Security tests for outbound URL helpers and prompt path validation."""

import asyncio
import socket

import pytest

from src.services import prompt_loader
from src.util.outbound_url import is_allowed_attachment_url, is_valid_fhir_id, resolves_to_internal_address
from src.util.settings import settings

FHIR_BASE = "https://fhir.example/fhir"


@pytest.mark.parametrize(("value", "expected"), [("patient-123", True), ("..", False), ("../Patient", False), ("a/b", False), ("a" * 65, False)])
def test_is_valid_fhir_id(value, expected):
    assert is_valid_fhir_id(value) is expected


@pytest.mark.parametrize(
    ("url", "allowed_hosts", "expected"),
    [
        ("https://fhir.example/fhir/Binary/1", [], True),  # trusted FHIR origin
        ("https://sub.docs.example/a.txt", [".docs.example"], True),  # allowlisted subdomain
        ("https://evildocs.example/a.txt", [".docs.example"], False),  # suffix trick
        ("https://attacker.example/a.txt", ["docs.example"], False),  # not allowlisted
        ("http://169.254.169.254/latest/meta-data/", ["169.254.169.254"], False),  # internal IP even if allowlisted
    ],
)
def test_is_allowed_attachment_url(url, allowed_hosts, expected):
    assert is_allowed_attachment_url(url, FHIR_BASE, allowed_hosts) is expected


@pytest.mark.parametrize(("answers", "expected"), [(["93.184.216.34"], False), (["93.184.216.34", "10.0.0.5"], True)])
async def test_resolves_to_internal_address_flags_any_private_answer(monkeypatch, answers, expected):
    async def _fake_getaddrinfo(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port)) for ip in answers]

    monkeypatch.setattr(type(asyncio.get_running_loop()), "getaddrinfo", _fake_getaddrinfo)

    assert await resolves_to_internal_address("https://docs.example/a.txt") is expected


@pytest.mark.parametrize(("path", "expected"), [("2025_08/syphilis/ig_hc", True), ("../secrets", False), ("/etc/passwd", False), ("a\\b", False)])
def test_is_safe_prompt_path(path, expected):
    assert prompt_loader.is_safe_prompt_path(path) is expected


def test_load_prompt_from_folder_rejects_symlink_escape(tmp_path, monkeypatch):
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("LEAKED", encoding="utf-8")
    (prompts_root / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(settings, "prompts_dir", str(prompts_root))

    assert prompt_loader._load_prompt_from_folder("linked/leak") is None
