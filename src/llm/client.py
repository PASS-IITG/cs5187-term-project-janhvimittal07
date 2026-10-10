"""Provider-agnostic client for a local OpenAI-compatible endpoint.  Task: P1.

Env: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY (optional)
"""
from __future__ import annotations

import os


class LLM:
    def __init__(self) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
        self.model = os.environ.get("LLM_MODEL", "")

    def complete(self, prompt: str, temperature: float = 0.8, max_tokens: int = 2048) -> str:
        raise NotImplementedError("P1: POST {base_url}/chat/completions, return the text")
