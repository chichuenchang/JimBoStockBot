"""Alpaca market data (free IEX feed) + trading calendar. Data only; no orders."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time

import httpx

from stockbot.clock import ET
from stockbot.data.base import DataError, Timeframe
from stockbot.models import Bar, NewsItem, Session

DATA_URL = "https://data.alpaca.markets"
PAPER_URL = "https://paper-api.alpaca.markets"
RETRY_DELAYS = (1, 2, 4)
_sleep = asyncio.sleep


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AlpacaData:
    def __init__(self, key: str, secret: str, http: httpx.AsyncClient | None = None,
                 timeout_s: float = 20):
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.http = http
        self.timeout = timeout_s

    async def _get(self, url: str, params: dict) -> dict | list:
        last = "unknown"
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                if self.http is not None:
                    r = await self.http.get(url, params=params, headers=self.headers, timeout=self.timeout)
                else:
                    async with httpx.AsyncClient() as http:
                        r = await http.get(url, params=params, headers=self.headers, timeout=self.timeout)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}"
            else:
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
                if r.status_code != 429 and r.status_code < 500:
                    raise DataError(f"{url}: {last} {r.text[:200]}")
            if attempt < len(RETRY_DELAYS):
                await _sleep(RETRY_DELAYS[attempt])
        raise DataError(f"{url}: {last} after {len(RETRY_DELAYS) + 1} attempts")

    async def bars(self, symbol: str, timeframe: Timeframe, start: datetime,
                   end: datetime) -> list[Bar]:
        params = {"timeframe": timeframe, "start": _iso(start), "end": _iso(end), "feed": "iex",
                  "adjustment": "split", "limit": 10000}
        out: list[Bar] = []
        while True:
            data = await self._get(f"{DATA_URL}/v2/stocks/{symbol}/bars", params)
            out += [Bar(symbol, _ts(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"])
                    for b in data.get("bars") or []]
            token = data.get("next_page_token")
            if not token:
                return out
            params = {**params, "page_token": token}

    async def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        data = await self._get(f"{DATA_URL}/v2/stocks/trades/latest",
                               {"symbols": ",".join(symbols), "feed": "iex"})
        return {s: t["p"] for s, t in (data.get("trades") or {}).items()}

    async def news(self, symbols: list[str], since: datetime) -> list[NewsItem]:
        data = await self._get(f"{DATA_URL}/v1beta1/news",
                               {"symbols": ",".join(symbols), "start": _iso(since), "limit": 50})
        return [NewsItem(_ts(n["created_at"]), n["headline"], tuple(n.get("symbols") or ()),
                         n.get("source", "")) for n in data.get("news") or []]

    async def calendar(self, start: date, end: date) -> list[Session]:
        data = await self._get(f"{PAPER_URL}/v2/calendar",
                               {"start": start.isoformat(), "end": end.isoformat()})
        out = []
        for d in data:
            day = date.fromisoformat(d["date"])

            def at(hm: str) -> datetime:
                h, m = map(int, hm.split(":"))
                return datetime.combine(day, time(h, m), tzinfo=ET).astimezone(UTC)
            out.append(Session(day, at(d["open"]), at(d["close"])))
        return out
