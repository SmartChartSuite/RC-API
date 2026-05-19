"""Prompt loader — loads prompts from Langfuse or local ./prompts/ directory.

Strategy is selected at module load time based on use_langfuse in settings.
All callers use load_prompts(prompt_paths) regardless of strategy.
"""

from pathlib import Path

import yaml
from loguru import logger

from src.models.prompt import Prompt, PromptMetadata
from src.util.settings import langfuse_host, langfuse_public_key, langfuse_secret_key
from src.util.settings import prompts_dir, use_langfuse


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


def _load_from_folder(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts from the local ./prompts/ directory tree."""
    base = Path(prompts_dir)
    prompts: list[Prompt] = []
    for path in prompt_paths:
        file_path = base / f"{path}.md"
        if not file_path.exists():
            logger.warning(f"Prompt file not found: {file_path}")
            continue
        try:
            raw = file_path.read_text(encoding="utf-8")
            meta_dict, body = _parse_md_frontmatter(raw)
            name = meta_dict.get("name") or Path(path).name
            prompts.append(
                Prompt(
                    metadata=PromptMetadata(
                        name=name,
                        path=path,
                        version=meta_dict.get("version"),
                        last_updated=meta_dict.get("last_updated"),
                        description=meta_dict.get("description"),
                    ),
                    content=body,
                )
            )
            logger.info(f"Loaded prompt from file: {file_path}")
        except Exception as exc:
            logger.error(f"Failed to load prompt {file_path}: {exc}")
    return prompts


async def _load_from_langfuse(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts from Langfuse using the Python SDK."""
    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError:
        logger.error("langfuse package not installed. Run: pip install langfuse")
        return []

    client = Langfuse(
        public_key=langfuse_public_key,
        secret_key=langfuse_secret_key,
        host=langfuse_host,
    )
    prompts: list[Prompt] = []
    for path in prompt_paths:
        try:
            lf_prompt = client.get_prompt(path, label="latest")
            body = lf_prompt.prompt  # Langfuse returns the compiled prompt string
            # meta_dict, body = _parse_md_frontmatter(body)
            name = Path(path).name
            prompts.append(
                Prompt(
                    metadata=PromptMetadata(
                        name=name,
                        path=path,
                        version=str(lf_prompt.version) if hasattr(lf_prompt, "version") else None,
                        description=lf_prompt.commit_message or "",
                    ),
                    content=body,
                )
            )
            logger.info(f"Loaded prompt from Langfuse: {path}")
        except Exception as exc:
            logger.error(f"Failed to load prompt '{path}' from Langfuse: {exc}")
    return prompts


async def load_prompts(prompt_paths: list[str]) -> list[Prompt]:
    """Load prompts from Langfuse (if configured) or local ./prompts/ fallback."""
    if not prompt_paths:
        return []
    if use_langfuse:
        return await _load_from_langfuse(prompt_paths)
    return _load_from_folder(prompt_paths)
