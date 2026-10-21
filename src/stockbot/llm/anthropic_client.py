"""Claude via the official anthropic SDK (beta messages for server-side refusal fallbacks)."""
from __future__ import annotations

import time

import anthropic

from stockbot.config import ModelCfg, api_key
from stockbot.llm.base import LLMError, LLMResult, Usage

JSON_HINT = "\n\nReply with a single JSON object only."


class AnthropicClient:
    def __init__(self, cfg: ModelCfg, timeout_s: float, sdk=None, effort: str = "medium"):
        self.cfg = cfg
        self.effort = effort
        self.sdk = sdk or anthropic.AsyncAnthropic(
            api_key=api_key(cfg), timeout=timeout_s, max_retries=0,
            **({"base_url": cfg.base_url} if cfg.base_url else {}))

    async def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResult:
        t0 = time.monotonic()
        try:
            resp = await self.sdk.beta.messages.create(
                model=self.cfg.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user + (JSON_HINT if json_mode else "")}],
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APITimeoutError as e:
            raise LLMError(f"{self.cfg.name}: timeout") from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"{self.cfg.name}: HTTP 429") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"{self.cfg.name}: HTTP {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"{self.cfg.name}: connection error") from e
        if resp.stop_reason == "refusal":
            raise LLMError(f"{self.cfg.name}: refusal")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text.strip():
            raise LLMError(f"{self.cfg.name}: empty content")
        u = resp.usage
        cached = u.cache_read_input_tokens or 0
        total_in = u.input_tokens + cached + (u.cache_creation_input_tokens or 0)
        return LLMResult(text, Usage(total_in, cached, u.output_tokens), time.monotonic() - t0)
