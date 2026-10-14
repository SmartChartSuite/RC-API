"""Pydantic models for prompt metadata and prompt content.

This module defines the data-only shapes exchanged by `src.services.prompt_loader`
and `src.services.llm_executor`, including source metadata and rendered prompt
text. It performs no prompt loading, caching, Langfuse access, or LLM execution;
those operations belong to the collaborating service modules.
"""

from pydantic import BaseModel


class PromptMetadata(BaseModel):
    name: str
    path: str  # full path e.g. "2025_08/syphilis/ig_bstfed"
    version: str | None = None
    last_updated: str | None = None
    description: str | None = None


class Prompt(BaseModel):
    metadata: PromptMetadata
    content: str  # raw markdown body of the prompt (frontmatter stripped)
