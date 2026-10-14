"""Provider-agnostic client for an OpenAI-compatible chat endpoint (Task P1).

Works with Ollama (http://localhost:11434/v1), llama.cpp's server, vLLM, or
any hosted OpenAI-compatible API. No third-party packages needed.

Environment:
    LLM_BASE_URL   default http://localhost:11434/v1
    LLM_MODEL      e.g. qwen2.5-coder:1.5b  (required for completions)
    LLM_API_KEY    optional (Ollama and llama.cpp ignore it)

    python llm/client.py            # ping the server and run one tiny completion
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: float = 600.0):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.timeout = timeout

    def _post(self, path: str, payload: dict | None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code} from {req.full_url}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LLMError(f"cannot reach {req.full_url}: {e}") from e

    def models(self) -> list[str]:
        d = self._post("/models", None)
        return [m.get("id", "") for m in d.get("data", [])]

    def complete(self, prompt: str, system: str | None = None, temperature: float = 0.8,
                 max_tokens: int = 1500) -> str:
        if not self.model:
            raise LLMError("no model set: export LLM_MODEL=<name> (see `ollama list`)")
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        d = self._post("/chat/completions", {"model": self.model, "messages": msgs,
                                             "temperature": temperature, "max_tokens": max_tokens,
                                             "stream": False})
        try:
            return d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response: {str(d)[:300]}") from e


if __name__ == "__main__":
    c = LLM()
    print(f"server: {c.base_url}  model: {c.model or '(unset)'}")
    try:
        print("models:", c.models())
        print("reply :", c.complete("Reply with the single word: ready", max_tokens=8).strip())
    except LLMError as e:
        print("ERROR:", e)
        sys.exit(1)
