"""Scripted LLM for tests and $0 replay runs."""
from __future__ import annotations

from collections.abc import Callable

from stockbot.llm.base import LLMResult, Usage


class FakeLLM:
    def __init__(self, replies: list[str] | Callable[[str, str], str],
                 usage: Usage = Usage(1000, 0, 100)):
        self.replies = replies
        self.usage = usage
        self.calls: list[tuple[str, str]] = []
        self.images: list[tuple[bytes, ...]] = []  # per call, parallel to calls

    async def complete(self, system: str, user: str, json_mode: bool = True,
                       images: tuple[bytes, ...] = ()) -> LLMResult:
        self.calls.append((system, user))
        self.images.append(images)
        if callable(self.replies):
            text = self.replies(system, user)
        else:
            text = self.replies[(len(self.calls) - 1) % len(self.replies)]
        return LLMResult(text, self.usage, 0.0)
