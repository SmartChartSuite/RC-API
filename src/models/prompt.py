"""Prompt model — used by both prompt_loader and llm_executor."""

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
