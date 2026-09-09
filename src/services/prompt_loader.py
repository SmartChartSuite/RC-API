"""Prompt loader — loads prompts from Langfuse or local ./prompts/ directory.

Startup initialization validates Langfuse connectivity and selects whether
prompt loading and tracing should use Langfuse or the local prompts folder.
All callers use load_prompts(prompt_paths) regardless of strategy.
"""

import asyncio
from pathlib import Path

import httpx
import litellm
import yaml
from loguru import logger

from src.models.prompt import Prompt, PromptMetadata
from src.util.settings import (
    langfuse_host,
    langfuse_prompt_fetch_timeout_seconds,
    langfuse_prompt_max_retries,
    langfuse_public_key,
    langfuse_secret_key,
    prompts_dir,
    use_langfuse,
)


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


def _load_prompt_from_folder(path: str) -> Prompt | None:
    """Load one prompt from the local prompt tree."""
    file_path = Path(prompts_dir) / f"{path}.md"
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
    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError:
        logger.error("langfuse package not installed. Falling back to local prompts.")
        return _load_from_folder(prompt_paths)

    client = Langfuse(
        public_key=langfuse_public_key,
        secret_key=langfuse_secret_key,
        host=langfuse_host,
    )
    prompts: list[Prompt] = []
    for path in prompt_paths:
        try:
            lf_prompt = client.get_prompt(
                path,
                label="latest",
                max_retries=langfuse_prompt_max_retries,
                fetch_timeout_seconds=langfuse_prompt_fetch_timeout_seconds,
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
    global use_langfuse

    if not use_langfuse:
        _disable_langfuse_tracing()
        logger.info(f"Langfuse is not fully configured. Falling back to local prompts folder: {prompts_dir}")
        return

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(str(langfuse_host))
        response.raise_for_status()
        _enable_langfuse_tracing()
        logger.info(f"Langfuse is configured and reachable at {langfuse_host}")
    except Exception as exc:  # noqa: BLE001
        # Any initialization failure disables Langfuse and preserves local fallback.
        use_langfuse = False
        _disable_langfuse_tracing()
        logger.warning(f"Langfuse is configured but not reachable at {langfuse_host}: {exc}. Falling back to local prompts folder: {prompts_dir}")


async def load_prompts(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts from Langfuse (if configured) or local ./prompts/ fallback."""
    if not prompt_paths:
        return []
    if use_langfuse:
        return await _load_from_langfuse(prompt_paths)
    return await asyncio.to_thread(_load_from_folder, prompt_paths)
