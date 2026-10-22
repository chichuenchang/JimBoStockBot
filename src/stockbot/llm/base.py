"""Provider-agnostic LLM interface."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from stockbot.config import ModelCfg


class LLMError(Exception):
    """billable=True when the provider likely charged anyway: timeouts (the request ran) and
    any unusable reply that came back with success status. Adapters raise only LLMError."""

    def __init__(self, message: str, billable: bool = False):
        super().__init__(message)
        self.billable = billable
        self.cost = 0.0  # estimate Budget.complete billed for this failure


TIMEOUT_EST_OUTPUT_TOKENS = 4000
CHARS_PER_TOKEN = 3  # conservative: overestimates input tokens


@dataclass(frozen=True)
class Usage:
    input_tokens: int  # all prompt tokens, cached included
    cached_input_tokens: int
    output_tokens: int


def estimate_usage(prompt_chars: int) -> Usage:
    return Usage(prompt_chars // CHARS_PER_TOKEN, 0, TIMEOUT_EST_OUTPUT_TOKENS)


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: Usage
    latency_s: float


class LLMClient(Protocol):
    async def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResult: ...


def cost_usd(cfg: ModelCfg, usage: Usage) -> float:
    uncached = usage.input_tokens - usage.cached_input_tokens
    return (uncached * cfg.price_in + usage.cached_input_tokens * cfg.price_in_cached
            + usage.output_tokens * cfg.price_out) / 1e6


def make_client(cfg: ModelCfg, timeout_s: float,
                http: httpx.AsyncClient | None = None) -> LLMClient:
    if cfg.adapter == "openai_compat":
        from stockbot.llm.openai_compat import OpenAICompatClient
        return OpenAICompatClient(cfg, timeout_s, http)
    if cfg.adapter == "anthropic":
        from stockbot.llm.anthropic_client import AnthropicClient
        return AnthropicClient(cfg, timeout_s)
    raise LLMError(f"adapter {cfg.adapter!r} has no factory (fake is built directly)")
