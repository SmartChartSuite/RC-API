import threading
from pathlib import Path
from types import SimpleNamespace

from src.services import prompt_loader
from src.util.settings import settings


def _write_prompt(prompt_root: Path, path: str, content: str) -> Path:
    prompt_file = prompt_root / f"{path}.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(content, encoding="utf-8")
    return prompt_file


class _FakeLangfuse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = False
        self._lock = threading.Lock()

    def get_prompt(self, path: str, **kwargs):
        with self._lock:
            self.calls.append((path, kwargs))
            call_number = len(self.calls)
        if self.fail:
            raise RuntimeError("langfuse unavailable")
        return SimpleNamespace(version=call_number, commit_message=f"commit {call_number}", prompt=f"Langfuse body {call_number}")


def _configure_langfuse(monkeypatch, client: object, *, ttl_seconds: float = 60.0, concurrency: int = 4) -> None:
    monkeypatch.setattr(prompt_loader, "_langfuse_client", client)
    monkeypatch.setattr(settings, "langfuse_prompt_cache_ttl_seconds", ttl_seconds)
    monkeypatch.setattr(settings, "langfuse_prompt_fetch_concurrency", concurrency)
    monkeypatch.setattr(settings, "langfuse_prompt_max_retries", 1)
    monkeypatch.setattr(settings, "langfuse_prompt_fetch_timeout_seconds", 5)


def test_load_from_folder_parses_frontmatter(tmp_path, monkeypatch):
    prompt_root = tmp_path / "prompts"
    _write_prompt(prompt_root, "2025_08/example", "---\nname: Prompt One\nversion: '2'\ndescription: Test prompt\n---\nPrompt body")
    monkeypatch.setattr(settings, "prompts_dir", str(prompt_root))

    prompts = prompt_loader._load_from_folder(["2025_08/example"])

    assert len(prompts) == 1
    assert prompts[0].metadata.name == "Prompt One"
    assert prompts[0].metadata.version == "2"
    assert prompts[0].content == "Prompt body"


def test_local_prompt_cache_lifecycle(tmp_path, monkeypatch):
    prompt_root = tmp_path / "prompts"
    prompt_file = _write_prompt(prompt_root, "package/example", "---\nname: Cached prompt\n---\nShort")
    monkeypatch.setattr(settings, "prompts_dir", str(prompt_root))
    read_calls = 0
    original_read_text = Path.read_text

    def counted_read_text(self, *args, **kwargs):
        nonlocal read_calls
        read_calls += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    # Hits reuse the parsed file, and callers get isolated copies.
    first = prompt_loader._load_from_folder(["package/example"])
    first[0].metadata.name = "Caller mutation"
    second = prompt_loader._load_from_folder(["package/example"])
    assert read_calls == 1
    assert second[0].metadata.name == "Cached prompt"

    # A changed file signature reloads it.
    prompt_file.write_text("---\nname: After\n---\nA much longer replacement body", encoding="utf-8")
    assert prompt_loader._load_from_folder(["package/example"])[0].content == "A much longer replacement body"

    # A deleted file is evicted, and a recreated one loads fresh.
    prompt_file.unlink()
    assert prompt_loader._load_from_folder(["package/example"]) == []
    _write_prompt(prompt_root, "package/example", "---\nname: Recreated\n---\nNew body")
    assert prompt_loader._load_from_folder(["package/example"])[0].metadata.name == "Recreated"


def test_unsafe_prompt_path_is_rejected_before_cache_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "prompts_dir", str(tmp_path / "prompts"))

    assert prompt_loader._load_prompt_from_folder("../outside") is None
    assert not prompt_loader._local_prompt_cache


async def test_load_prompts_uses_folder_loader_when_langfuse_disabled(monkeypatch):
    monkeypatch.setattr(settings, "use_langfuse", False)
    monkeypatch.setattr(prompt_loader, "_load_from_folder", lambda prompt_paths: ["folder-result"])

    assert await prompt_loader.load_prompts(["any/path"]) == ["folder-result"]
    assert await prompt_loader.load_prompts([]) == []


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

    monkeypatch.setattr(settings, "use_langfuse", True)
    monkeypatch.setattr(settings, "langfuse_host", "https://langfuse.example")
    monkeypatch.setattr(settings, "prompts_dir", "./prompts")
    monkeypatch.setattr(prompt_loader.httpx, "AsyncClient", _FailingClient)
    monkeypatch.setattr(prompt_loader.litellm, "callbacks", ["langfuse_otel", "other-callback"])

    await prompt_loader.initialize_prompt_source()

    assert settings.use_langfuse is False
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

    monkeypatch.setattr(settings, "use_langfuse", True)
    monkeypatch.setattr(settings, "langfuse_host", "https://langfuse.example")
    monkeypatch.setattr(prompt_loader.httpx, "AsyncClient", _HealthyClient)
    monkeypatch.setattr(prompt_loader.litellm, "callbacks", [])
    monkeypatch.setattr(prompt_loader.litellm, "suppress_debug_info", False)

    await prompt_loader.initialize_prompt_source()

    assert settings.use_langfuse is True
    assert prompt_loader.litellm.callbacks == ["langfuse_otel"]
    assert prompt_loader.litellm.suppress_debug_info is True


async def test_initialize_prompt_source_disables_langfuse_tracing_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "use_langfuse", False)
    monkeypatch.setattr(settings, "prompts_dir", "./prompts")
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

    assert await prompt_loader._load_from_langfuse(["prompt/a"]) == ["prompt/a"]
    assert observed_threads and observed_threads[0] != event_loop_thread


def test_langfuse_cache_ttl_stale_if_error_and_clear(monkeypatch):
    client = _FakeLangfuse()
    _configure_langfuse(monkeypatch, client, ttl_seconds=10.0)
    clock = [100.0]
    warnings: list[str] = []
    monkeypatch.setattr(prompt_loader.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(prompt_loader.logger, "warning", lambda message: warnings.append(str(message)))

    # A hit within the TTL makes no call, and the SDK cache is disabled.
    assert prompt_loader._load_from_langfuse_sync(["package/prompt"])[0].content == "Langfuse body 1"
    clock[0] = 109.9
    assert prompt_loader._load_from_langfuse_sync(["package/prompt"])[0].content == "Langfuse body 1"
    assert len(client.calls) == 1
    assert client.calls[0][1] == {"label": "latest", "cache_ttl_seconds": 0, "max_retries": 1, "fetch_timeout_seconds": 5}

    # After the TTL it refetches.
    clock[0] = 110.0
    assert prompt_loader._load_from_langfuse_sync(["package/prompt"])[0].content == "Langfuse body 2"

    # A failed refresh serves the stale Langfuse entry and logs a warning.
    client.fail = True
    clock[0] = 121.0
    assert prompt_loader._load_from_langfuse_sync(["package/prompt"])[0].content == "Langfuse body 2"
    assert any("stale cached Langfuse prompt" in message for message in warnings)

    # Clearing the cache forces a refetch.
    client.fail = False
    clock[0] = 121.5
    prompt_loader.clear_prompt_cache()
    assert prompt_loader._load_from_langfuse_sync(["package/prompt"])[0].content == "Langfuse body 4"


def test_langfuse_cache_ttl_zero_always_fetches(monkeypatch):
    client = _FakeLangfuse()
    _configure_langfuse(monkeypatch, client, ttl_seconds=0.0)

    prompt_loader._load_from_langfuse_sync(["package/prompt"])
    prompt_loader._load_from_langfuse_sync(["package/prompt"])

    assert len(client.calls) == 2


def test_langfuse_failure_falls_back_locally_without_poisoning_cache(tmp_path, monkeypatch):
    prompt_root = tmp_path / "prompts"
    _write_prompt(prompt_root, "package/prompt", "---\nname: Local fallback\nversion: '3'\n---\nFallback body")
    monkeypatch.setattr(settings, "prompts_dir", str(prompt_root))
    client = _FakeLangfuse()
    client.fail = True
    _configure_langfuse(monkeypatch, client)

    fallback = prompt_loader._load_from_langfuse_sync(["package/prompt"])
    client.fail = False
    fetched = prompt_loader._load_from_langfuse_sync(["package/prompt"])

    assert (fallback[0].metadata.name, fallback[0].metadata.version, fallback[0].content) == ("Local fallback", "3", "Fallback body")
    assert fetched[0].content == "Langfuse body 2"


def test_langfuse_fetches_are_bounded_and_preserve_input_order(monkeypatch):
    bound = 3
    barrier = threading.Barrier(bound)
    active = 0
    max_active = 0
    lock = threading.Lock()

    class _ConcurrentLangfuse:
        def get_prompt(self, path, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                barrier.wait(timeout=5)
            finally:
                with lock:
                    active -= 1
            return SimpleNamespace(version=1, commit_message="", prompt=f"body for {path}")

    _configure_langfuse(monkeypatch, _ConcurrentLangfuse(), concurrency=bound)
    paths = [f"package/prompt-{index}" for index in range(6)]

    prompts = prompt_loader._load_from_langfuse_sync(paths)

    assert max_active == bound
    assert [prompt.metadata.path for prompt in prompts] == paths
