"""LLM Provider — OpenAI 兼容接口（Stage 8）。

ponytail: stdlib urllib 直连，不引 SDK；base_url/model/api_key 可配置，
方便接网关（Anspire 等）。
"""

from __future__ import annotations

import json
import os
import urllib.request


class LLMError(Exception):
    pass


class LLMProvider:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")

    def complete(self, prompt: str, timeout: int = 30) -> str:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
        except Exception as e:
            raise LLMError(f"LLM_PROVIDER_ERROR: {e}") from e
        return body["choices"][0]["message"]["content"]
