"""Prompt loader — loads prompts from Langfuse or local ./prompts/ directory.

Startup initialization validates Langfuse connectivity and selects whether
prompt loading and tracing should use Langfuse or the local prompts folder.
All callers use load_prompts(prompt_paths) regardless of strategy.
"""

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
import litellm
import yaml
from loguru import logger

from src.models.prompt import Prompt, PromptMetadata
from src.util.settings import secret_value, settings

_langfuse_client: Any | None = None
_PROMPT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def get_langfuse_client() -> Any | None:
    """Return the process-wide Langfuse client used for prompts and tracing."""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError:
        logger.error("langfuse package not installed. Langfuse features are unavailable.")
        return None

    _langfuse_client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=secret_value(settings.langfuse_secret_key),
        host=settings.langfuse_host,
    )
    return _langfuse_client


def _disable_langfuse_tracing() -> None:
    callbacks = getattr(litellm, "callbacks", None)
    if isinstance(callbacks, list) and "langfuse_otel" in callbacks:
        litellm.callbacks = [callback for callback in callbacks if callback != "langfuse_otel"]


def _enable_langfuse_tracing() -> None:
    callbacks = getattr(litellm, "callbacks", None)
    if isinstance(callbacks, list):
        if "langfuse_otel" not in callbacks:
            litellm.callbacks = [*callbacks, "langfuse_otel"]
    else:
        litellm.callbacks = ["langfuse_otel"]
    litellm.suppress_debug_info = True


def _parse_md_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown string.

    Returns (metadata_dict, body_content).
    """
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return meta, body
            except yaml.YAMLError:
                pass
    return {}, text.strip()


def is_safe_prompt_path(path: str) -> bool:
    """Return whether a Questionnaire-supplied prompt path is a safe relative identifier.

    Allowed: '/'-separated segments of letters, digits, '_', '-', and '.', with no
    empty, '.', or '..' segments and no leading '/'. This blocks directory traversal
    and absolute paths for both local files and Langfuse prompt names.
    """
    if not isinstance(path, str) or not path or len(path) > 256:
        return False
    if path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    segments = path.split("/")
    return all(segment and segment not in {".", ".."} and _PROMPT_SEGMENT_RE.fullmatch(segment) for segment in segments)


def _resolve_prompt_file(path: str) -> Path | None:
    """Map a prompt path to a file inside settings.prompts_dir, or None if it would escape it."""
    if not is_safe_prompt_path(path):
        return None
    root = Path(settings.prompts_dir).resolve()
    candidate = (root / f"{path}.md").resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _load_prompt_from_folder(path: str) -> Prompt | None:
    """Load one prompt from the local prompt tree."""
    file_path = _resolve_prompt_file(path)
    if file_path is None:
        logger.warning(f"Rejected unsafe prompt path: {path!r}")
        return None
    if not file_path.exists():
        logger.warning(f"Prompt file not found: {file_path}")
        return None
    try:
        raw = file_path.read_text(encoding="utf-8")
        meta_dict, body = _parse_md_frontmatter(raw)
        prompt = Prompt(
            metadata=PromptMetadata(
                name=meta_dict.get("name") or Path(path).name,
                path=path,
                version=meta_dict.get("version"),
                last_updated=meta_dict.get("last_updated"),
                description=meta_dict.get("description"),
            ),
            content=body,
        )
        logger.debug(f"Loaded prompt from file: {file_path}")
        return prompt
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        logger.error(f"Failed to load prompt {file_path}: {exc}")
        return None


def _load_from_folder(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts from the local ./prompts/ directory tree."""
    return [prompt for path in prompt_paths if (prompt := _load_prompt_from_folder(path)) is not None]


def _load_from_langfuse_sync(prompt_paths: list[str]) -> list[Prompt]:
    """Synchronously load Langfuse prompts with per-prompt local fallback."""
    client = get_langfuse_client()
    if client is None:
        return _load_from_folder(prompt_paths)
    prompts: list[Prompt] = []
    for path in prompt_paths:
        try:
            lf_prompt = client.get_prompt(
                path,
                label="latest",
                max_retries=settings.langfuse_prompt_max_retries,
                fetch_timeout_seconds=settings.langfuse_prompt_fetch_timeout_seconds,
            )
            prompts.append(
                Prompt(
                    metadata=PromptMetadata(
                        name=Path(path).name,
                        path=path,
                        version=str(lf_prompt.version) if hasattr(lf_prompt, "version") else None,
                        description=lf_prompt.commit_message or "",
                    ),
                    content=lf_prompt.prompt,
                )
            )
            logger.debug(f"Loaded prompt from Langfuse: {path}")
        except Exception as exc:  # noqa: BLE001
            # Langfuse SDK failures must fall back to the matching local prompt.
            logger.warning(f"Failed to load prompt '{path}' from Langfuse: {exc}. Trying local fallback.")
            fallback = _load_prompt_from_folder(path)
            if fallback is not None:
                prompts.append(fallback)
            else:
                logger.error(f"Prompt '{path}' is unavailable from both Langfuse and the local prompt folder.")
    return prompts


async def _load_from_langfuse(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts without running the synchronous Langfuse SDK on the API event loop."""
    return await asyncio.to_thread(_load_from_langfuse_sync, prompt_paths)


async def initialize_prompt_source() -> None:
    """Validate Langfuse connectivity at startup and fall back to the prompts folder if unavailable."""
    if not settings.use_langfuse:
        _disable_langfuse_tracing()
        logger.info(f"Langfuse is not fully configured. Falling back to local prompts folder: {settings.prompts_dir}")
        return

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(str(settings.langfuse_host))
        response.raise_for_status()
        _enable_langfuse_tracing()
        logger.info(f"Langfuse is configured and reachable at {settings.langfuse_host}")
    except Exception as exc:  # noqa: BLE001
        # Any initialization failure disables Langfuse and preserves local fallback.
        settings.use_langfuse = False
        _disable_langfuse_tracing()
        logger.warning(f"Langfuse is configured but not reachable at {settings.langfuse_host}: {exc}. Falling back to local prompts folder: {settings.prompts_dir}")


async def load_prompts(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts from Langfuse (if configured) or local ./prompts/ fallback.

    Unsafe paths are dropped before reaching either source, and duplicates are
    removed while preserving order.
    """
    safe_paths: list[str] = []
    for path in dict.fromkeys(prompt_paths):
        if is_safe_prompt_path(path):
            safe_paths.append(path)
        else:
            logger.warning(f"Rejected unsafe prompt path: {path!r}")
    if not safe_paths:
        return []
    if settings.use_langfuse:
        return await _load_from_langfuse(safe_paths)
    return await asyncio.to_thread(_load_from_folder, safe_paths)
