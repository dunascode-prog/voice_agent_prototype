"""Versioned playbook prompts. Each call will record the prompt hash (docs/ARCHITECTURE.md 6.8)."""

import hashlib
from importlib import resources


def load_prompt(playbook: str) -> str:
    name = f"{playbook}.md"
    files = resources.files(__package__)
    if not files.joinpath(name).is_file():
        raise ValueError(f"unknown playbook '{playbook}'")
    return files.joinpath(name).read_text(encoding="utf-8")


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
