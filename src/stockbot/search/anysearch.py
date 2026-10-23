"""AnySearch REST client (api.anysearch.com). Measured 2026-10-23: no freshness filter (dates only
inside result text), extract fails on wsj/reuters/apnews/nytimes (HTTP 422). Spec:
docs/superpowers/specs/2026-10-23-search-5min-design.md"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

BASE_URL = "https://api.anysearch.com"


class SearchError(Exception):
    pass


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    text: str  # result content (carries "date: ..."), else snippet


class SearchClient(Protocol):
    async def search(self, query: str, max_results: int) -> list[Hit]: ...
    async def extract(self, url: str) -> str: ...


class AnySearch:
    def __init__(self, api_key: str | None, http: httpx.AsyncClient | None = None, timeout_s: float = 20):
        self.api_key = api_key
        self.http = http or httpx.AsyncClient()
        self.timeout = timeout_s

    async def _post(self, path: str, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = await self.http.post(BASE_URL + path, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise SearchError(f"anysearch {path}: {type(e).__name__}") from e
        try:
            body = resp.json()
        except ValueError:
            raise SearchError(f"anysearch {path}: HTTP {resp.status_code}, not JSON") from None
        if not isinstance(body, dict) or resp.status_code >= 400 or body.get("code", 0) != 0:
            msg = body.get("message") if isinstance(body, dict) else None
            raise SearchError(f"anysearch {path}: HTTP {resp.status_code} {msg or ''}".rstrip())
        return body.get("data") or {}

    async def search(self, query: str, max_results: int) -> list[Hit]:
        data = await self._post("/v1/search", {"query": query, "max_results": max_results})
        return [Hit(r.get("title") or "(untitled)", r.get("url") or "", r.get("content") or r.get("snippet") or "")
                for r in data.get("results") or []]

    async def extract(self, url: str) -> str:
        return (await self._post("/v1/extract", {"url": url})).get("content") or ""


@dataclass
class FakeSearch:
    """Canned results for tests. A page value that is an Exception is raised."""
    hits: list[Hit]
    pages: dict[str, str | Exception] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    extracted: list[str] = field(default_factory=list)

    async def search(self, query: str, max_results: int) -> list[Hit]:
        self.queries.append(query)
        return self.hits[:max_results]

    async def extract(self, url: str) -> str:
        self.extracted.append(url)
        page = self.pages.get(url, SearchError("not found"))
        if isinstance(page, Exception):
            raise page
        return page


class NoSearch:
    """Replay: no point-in-time web search exists, so a replayed day must not see today's web."""

    async def search(self, query: str, max_results: int) -> list[Hit]:
        raise SearchError("search unavailable in replay")

    async def extract(self, url: str) -> str:
        raise SearchError("search unavailable in replay")
