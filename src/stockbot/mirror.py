"""Read-only copy of the dashboard on Miaoda (spec docs/superpowers/specs/2026-11-24-jbsb-miaoda-mirror-design.md).

crawl(): every GET the pages make, answered by a second in-process app (root = socket mount, mirror = crawl time).
The socket serves those bodies by key; it runs no StockBot code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qsl, quote

import httpx

from stockbot.config import AppConfig
from stockbot.dashboard.app import SCRIPTS, create_app
from stockbot.data.base import MarketData
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.models import BENCHMARK, Bar, Session

log = logging.getLogger(__name__)

# the pages' own parameters: index.html data-sessions, usage.html data-span, _cells.html #tf, equity modes
SESSIONS = (1, 5, 21)
USAGE_SPANS = ("today", "7d")
TFS = ("1Min", "1Hour", "1Day", "1Week", "1Month")
INTRADAY_TFS = TFS[:2]
EQUITY_MODES = ("usd", "pct")


@dataclass(frozen=True)
class Entry:
    content_type: str
    body: bytes


def key(path: str, query: str) -> str:
    """Canonical key, same rule as the socket: params decoded, sorted, re-encoded as encodeURIComponent does."""
    pairs = sorted(parse_qsl(query, keep_blank_values=True))
    if not pairs:
        return path
    return path + "?" + "&".join(f"{quote(k, safe=_JS_SAFE)}={quote(v, safe=_JS_SAFE)}" for k, v in pairs)


_JS_SAFE = "!'()*"  # encodeURIComponent leaves these (and A-Z a-z 0-9 - _ . ~) unescaped


class MemoData:
    """One crawl's market data: each symbol x timeframe fetched once. A crawl asks the same bars per arm and per
    1Day/1Week/1Month, each time with a fresh `now` as end; later calls get the cached bars from their start on."""

    def __init__(self, inner: MarketData):
        self.inner = inner
        self._bars: dict[tuple[str, str], tuple[datetime, list[Bar]]] = {}
        self._calendar: dict[tuple[date, date], list[Session]] = {}
        self._prices: dict[tuple[str, ...], dict[str, float]] = {}

    async def bars(self, symbol, timeframe, start, end):
        hit = self._bars.get((symbol, timeframe))
        if hit is None or start < hit[0]:
            hit = self._bars[(symbol, timeframe)] = (start, await self.inner.bars(symbol, timeframe, start, end))
        return [b for b in hit[1] if b.ts >= start]

    async def calendar(self, start, end):
        if (start, end) not in self._calendar:
            self._calendar[(start, end)] = await self.inner.calendar(start, end)
        return self._calendar[(start, end)]

    async def latest_prices(self, symbols):
        k = tuple(symbols)
        if k not in self._prices:
            self._prices[k] = await self.inner.latest_prices(symbols)
        return self._prices[k]

    async def news(self, symbols, since):
        return await self.inner.news(symbols, since)


async def crawl(db: DB, cfg: AppConfig, budget: Budget, data: MarketData | None, root: str,
                now: datetime, long_candles: bool = True) -> dict[str, Entry]:
    """long_candles False: skip 1Day/1Week/1Month bars (most of the bytes, and only their last bar moves)."""
    app = create_app(db, cfg, budget, MemoData(data) if data is not None else None, root=root, mirror=now)
    out: dict[str, Entry] = {}
    failed: list[str] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mirror") as c:
        async def get(path: str, query: str = "") -> httpx.Response | None:
            r = await c.get(path + ("?" + query if query else ""))
            if r.status_code != 200:
                failed.append(f"{path}?{query} {r.status_code}")
                return None
            out[key(path, query)] = Entry(r.headers.get("content-type", ""), r.content)
            return r

        arms = [a.name for a in cfg.arms if a.enabled]
        universe = list(cfg.settings.universe)
        for path in ("/", "/usage", *(f"/arm/{a}" for a in arms)):
            await get(path)
        version = out["/"].body.split(b"/static/core.js?v=", 1)[1].split(b'"', 1)[0].decode() if "/" in out else ""
        for s in SCRIPTS:
            await get(f"/static/{s}.js", f"v={version}")
        for path in ("/api/status", "/api/leaderboard", "/api/drawdown", "/api/daily_pnl", "/api/heatmap"):
            await get(path)
        for mode in EQUITY_MODES:
            await get("/api/equity", f"mode={mode}")
        for n in SESSIONS:
            await get("/api/market", f"sessions={n}")
        for span in USAGE_SPANS:
            await get("/api/usage", f"span={span}")
        for s in universe:
            for tf in TFS if long_candles else INTRADAY_TFS:
                await get("/api/candles", f"symbol={s}&tf={tf}")
        for arm in arms:
            await get("/api/equity", f"mode=usd&arms={quote(BENCHMARK + ',' + arm, safe='')}")  # arm.js pair
            for part in ("positions", "trades", "conviction", "rejections"):
                await get(f"/api/arm/{arm}/{part}")
            for s in universe:  # the bot page's chart: overview's bars + these (mostly [], one blob on the socket)
                await get(f"/api/arm/{arm}/fills", f"symbol={s}")
            ids: set[int] = set()
            for query in ("", "acted=1"):
                r = await get(f"/api/arm/{arm}/decisions", query)
                if r is not None:
                    ids |= {d["id"] for d in r.json()}
            for i in sorted(ids):
                r = await get(f"/api/decision/{i}")
                for chart in (r.json()["charts"] if r is not None else []):
                    q = "&".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in chart.items())
                    await get("/api/chart", q)
    if failed:
        log.warning("mirror crawl: %d not mirrored, e.g. %s", len(failed), failed[:3])
    return out
