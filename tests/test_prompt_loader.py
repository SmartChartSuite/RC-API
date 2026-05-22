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
