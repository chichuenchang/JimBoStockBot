"""OpenAI-compatible chat completions: Moonshot, DeepSeek, OpenRouter, Ollama, OpenAI."""
from __future__ import annotations

import base64
import time

import httpx

from stockbot.config import ModelCfg, api_key
from stockbot.llm.base import LLMError, LLMResult, Usage


class OpenAICompatClient:
    def __init__(self, cfg: ModelCfg, timeout_s: float, http: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.timeout = timeout_s
        self.http = http or httpx.AsyncClient()

    async def complete(self, system: str, user: str, json_mode: bool = True,
                       images: tuple[bytes, ...] = ()) -> LLMResult:
        content = [{"type": "text", "text": user}, *(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}
            for png in images)] if images else user
        body = {"model": self.cfg.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": content}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(self.cfg.extra_body or {})
        headers = {"Authorization": f"Bearer {api_key(self.cfg)}"}
        url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
        t0 = time.monotonic()
        try:
            resp = await self.http.post(url, json=body, headers=headers, timeout=self.timeout)
        except httpx.TimeoutException as e:
            raise LLMError(f"{self.cfg.name}: timeout after {self.timeout}s", billable=True) from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.cfg.name}: transport error {type(e).__name__}") from e
        if resp.status_code != 200:
            raise LLMError(f"{self.cfg.name}: HTTP {resp.status_code} {resp.text[:200]}")
        # HTTP 200: the provider ran the request, so every failure from here on is billable.
        try:
            data = resp.json()
            text = data["choices"][0]["message"].get("content") or ""
            u = data.get("usage") or {}
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") \
                or u.get("prompt_cache_hit_tokens") or 0
            usage = Usage(int(u.get("prompt_tokens", 0)), int(cached), int(u.get("completion_tokens", 0)))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"{self.cfg.name}: malformed response", billable=True) from e
        if not text.strip():
            raise LLMError(f"{self.cfg.name}: empty content", billable=True)
        return LLMResult(text, usage, time.monotonic() - t0)
