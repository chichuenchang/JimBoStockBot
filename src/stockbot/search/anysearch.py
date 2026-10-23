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
        data = body.get("data") or {}
        if not isinstance(data, dict):  # callers catch only SearchError: a shape surprise must not kill a decision
            raise SearchError(f"anysearch {path}: unexpected data {type(data).__name__}")
        return data

    async def search(self, query: str, max_results: int) -> list[Hit]:
        rows = (await self._post("/v1/search", {"query": query, "max_results": max_results})).get("results") or []
        if not isinstance(rows, list):
            raise SearchError(f"anysearch /v1/search: unexpected results {type(rows).__name__}")
        return [Hit(r.get("title") or "(untitled)", r.get("url") or "", r.get("content") or r.get("snippet") or "")
                for r in rows if isinstance(r, dict)]

    async def extract(self, url: str) -> str:
        text = (await self._post("/v1/extract", {"url": url})).get("content") or ""
        if not isinstance(text, str):
            raise SearchError(f"anysearch /v1/extract: unexpected content {type(text).__name__}")
        return text


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
