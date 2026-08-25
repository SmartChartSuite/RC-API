import sys
import threading
from types import SimpleNamespace
from src.services import prompt_loader


def test_parse_md_frontmatter_extracts_metadata_and_body():
    metadata, body = prompt_loader._parse_md_frontmatter("---\nname: Prompt One\nversion: '1'\n---\nPrompt body")

    assert metadata == {"name": "Prompt One", "version": "1"}
    assert body == "Prompt body"


def test_load_from_folder_reads_prompt_files(tmp_path, monkeypatch):
    prompt_root = tmp_path / "prompts"
    prompt_root.mkdir()
    nested = prompt_root / "2025_08"
    nested.mkdir()
    prompt_file = nested / "example.md"
    prompt_file.write_text("---\nname: Prompt One\nversion: '2'\ndescription: Test prompt\n---\nPrompt body", encoding="utf-8")

    monkeypatch.setattr(prompt_loader, "prompts_dir", str(prompt_root))

    prompts = prompt_loader._load_from_folder(["2025_08/example"])

    assert len(prompts) == 1
    assert prompts[0].metadata.name == "Prompt One"
    assert prompts[0].metadata.version == "2"
    assert prompts[0].content == "Prompt body"


async def test_load_prompts_uses_folder_loader_when_langfuse_disabled(monkeypatch):
    monkeypatch.setattr(prompt_loader, "use_langfuse", False)
    monkeypatch.setattr(prompt_loader, "_load_from_folder", lambda prompt_paths: ["folder-result"])

    result = await prompt_loader.load_prompts(["any/path"])

    assert result == ["folder-result"]


async def test_load_prompts_returns_empty_for_no_paths():
    result = await prompt_loader.load_prompts([])

    assert result == []


async def test_initialize_prompt_source_disables_langfuse_when_unreachable(monkeypatch):
    class _FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(prompt_loader, "use_langfuse", True)
    monkeypatch.setattr(prompt_loader, "langfuse_host", "https://langfuse.example")
    monkeypatch.setattr(prompt_loader, "prompts_dir", "./prompts")
    monkeypatch.setattr(prompt_loader.httpx, "AsyncClient", _FailingClient)
    monkeypatch.setattr(prompt_loader.litellm, "callbacks", ["langfuse_otel", "other-callback"])

    await prompt_loader.initialize_prompt_source()

    assert prompt_loader.use_langfuse is False
    assert prompt_loader.litellm.callbacks == ["other-callback"]


async def test_initialize_prompt_source_keeps_langfuse_when_reachable(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

    class _HealthyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return _Response()

    monkeypatch.setattr(prompt_loader, "use_langfuse", True)
    monkeypatch.setattr(prompt_loader, "langfuse_host", "https://langfuse.example")
    monkeypatch.setattr(prompt_loader.httpx, "AsyncClient", _HealthyClient)
    monkeypatch.setattr(prompt_loader.litellm, "callbacks", [])
    monkeypatch.setattr(prompt_loader.litellm, "suppress_debug_info", False)

    await prompt_loader.initialize_prompt_source()

    assert prompt_loader.use_langfuse is True
    assert prompt_loader.litellm.callbacks == ["langfuse_otel"]
    assert prompt_loader.litellm.suppress_debug_info is True


async def test_initialize_prompt_source_disables_langfuse_tracing_when_not_configured(monkeypatch):
    monkeypatch.setattr(prompt_loader, "use_langfuse", False)
    monkeypatch.setattr(prompt_loader, "prompts_dir", "./prompts")
    monkeypatch.setattr(prompt_loader.litellm, "callbacks", ["langfuse_otel"])

    await prompt_loader.initialize_prompt_source()

    assert prompt_loader.litellm.callbacks == []


async def test_load_from_langfuse_runs_synchronous_sdk_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    observed_threads: list[int] = []

    def _fake_sync_loader(prompt_paths):
        observed_threads.append(threading.get_ident())
        return prompt_paths

    monkeypatch.setattr(prompt_loader, "_load_from_langfuse_sync", _fake_sync_loader)

    result = await prompt_loader._load_from_langfuse(["prompt/a"])

    assert result == ["prompt/a"]
    assert observed_threads
    assert observed_threads[0] != event_loop_thread


def test_langfuse_failure_uses_bounded_call_and_local_fallback(tmp_path, monkeypatch):
    prompt_root = tmp_path / "prompts"
    prompt_path = prompt_root / "package" / "fallback.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("---\nname: Local fallback\nversion: '3'\n---\nFallback body", encoding="utf-8")
    calls: list[dict] = []

    class _FailingLangfuse:
        def __init__(self, **kwargs):
            pass

        def get_prompt(self, path, **kwargs):
            calls.append({"path": path, **kwargs})
            raise RuntimeError("langfuse unavailable")

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=_FailingLangfuse))
    monkeypatch.setattr(prompt_loader, "prompts_dir", str(prompt_root))
    monkeypatch.setattr(prompt_loader, "langfuse_prompt_fetch_timeout_seconds", 4)
    monkeypatch.setattr(prompt_loader, "langfuse_prompt_max_retries", 1)

    prompts = prompt_loader._load_from_langfuse_sync(["package/fallback"])

    assert calls == [
        {
            "path": "package/fallback",
            "label": "latest",
            "max_retries": 1,
            "fetch_timeout_seconds": 4,
        }
    ]
    assert len(prompts) == 1
    assert prompts[0].metadata.name == "Local fallback"
    assert prompts[0].metadata.path == "package/fallback"
    assert prompts[0].metadata.version == "3"
    assert prompts[0].content == "Fallback body"
