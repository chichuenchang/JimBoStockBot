"""Read-only copy of the dashboard on Miaoda (spec docs/superpowers/specs/2026-11-24-jbsb-miaoda-mirror-design.md).

crawl(): every GET the pages make, answered by a second in-process app (root = socket mount, mirror = crawl time).
The socket serves those bodies by key; it runs no StockBot code.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote, urlparse

import httpx
from dotenv import load_dotenv

from stockbot import interval
from stockbot.dashboard.app import INTRADAY_SESSIONS, MIRROR_STATIC, SCRIPTS, create_app
from stockbot.data.base import MarketData
from stockbot.models import BENCHMARK, Bar, Session

if TYPE_CHECKING:
    from stockbot.agent.cycle import AppContext

log = logging.getLogger(__name__)


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


OFF_HOURS = timedelta(hours=1)  # push cadence with the market closed; also long candles' refresh
RETRY = timedelta(minutes=5)  # after a failed push: a crawl is ~35 market-data calls trading shares a limit with
MAX_REQUEST = 400_000  # bytes per blobs request: the socket's body cap is ~1 MB (spec §Measured)
BLOB_OVERHEAD = 200  # JSON around one blob's base64: hash, content_type, keys


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


async def crawl(ctx: AppContext, root: str, now: datetime, long_candles: bool = True) -> dict[str, Entry]:
    """long_candles False: skip 1Day/1Week/1Month bars (most of the bytes, and only their last bar moves)."""
    data = MemoData(ctx.data) if ctx.data is not None else None
    app = create_app(ctx.db, ctx.cfg, ctx.budget, data, root=root, mirror=now)
    cfg = ctx.cfg
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
        index, usage = (out[p].body if p in out else b"" for p in ("/", "/usage"))
        version = (_offered(index, rb'/core\.js\?v=([^"]+)"') or [""])[0]
        for s in SCRIPTS:
            await get(f"{MIRROR_STATIC}/{s}.js", f"v={version}")
        for path in ("/api/status", "/api/leaderboard", "/api/drawdown", "/api/daily_pnl", "/api/heatmap"):
            await get(path)
        for mode in _offered(index, rb'data-mode="([^"]+)"'):
            await get("/api/equity", f"mode={mode}")
        for n in _offered(index, rb'data-sessions="([^"]+)"'):
            await get("/api/market", f"sessions={n}")
        for span in _offered(usage, rb'data-span="([^"]+)"'):
            await get("/api/usage", f"span={span}")
        tfs = _offered(b"".join(re.findall(rb'<select id="tf".*?</select>', index, re.DOTALL)), rb'value="([^"]+)"')
        for s in universe:
            for tf in tfs if long_candles else [t for t in tfs if t in INTRADAY_SESSIONS]:
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
                    chart_query = "&".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}"
                                           for k, v in chart.items())  # as arm.js URLSearchParams sends it
                    await get("/api/chart", chart_query)
    if failed:
        log.warning("mirror crawl: %d not mirrored, e.g. %s", len(failed), failed[:3])
    return out


class Pusher:
    """Delta push (spec §Push): gzip each body, hash it, upload the blobs the socket lacks, then commit the whole
    key -> hash index at once. Until the commit lands the socket serves the previous index."""

    def __init__(self, url: str, api_key: str, client: httpx.AsyncClient, max_request: int = MAX_REQUEST):
        self.url, self.client, self.max_request = url.rstrip("/"), client, max_request
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.acked: set[str] = set()  # blobs the socket holds: what the last commit indexed, + uploads since
        self.fresh = True  # nothing known yet (daemon restart): commit first, the 409 names what to upload

    async def _post(self, path: str, body: dict) -> httpx.Response:
        return await self.client.post(f"{self.url}/openapi/jbsb/{path}", json=body, headers=self.headers,
                                      timeout=120)

    async def _upload(self, blobs: dict[str, dict]) -> int:
        sent, chunk, size = 0, [], 0
        for h, b in blobs.items():
            n = len(b["gz_b64"]) + BLOB_OVERHEAD
            if chunk and size + n > self.max_request:
                sent += await self._send(chunk)
                chunk, size = [], 0
            chunk.append({"hash": h, **b})
            size += n
        if chunk:
            sent += await self._send(chunk)
        return sent

    async def _send(self, chunk: list[dict]) -> int:
        (await self._post("blobs", {"blobs": chunk})).raise_for_status()
        self.acked |= {b["hash"] for b in chunk}
        return sum(len(b["gz_b64"]) for b in chunk)

    async def push(self, entries: dict[str, Entry], pushed_at: datetime) -> int:
        """Bytes uploaded. Raises on any failed request."""
        # thread: gzip + hash of ~22 MB held the daemon's loop 1.1 s (spec §Measured)
        index, blobs, too_big = await asyncio.to_thread(_pack, entries, self.max_request)
        if too_big:  # one request can't carry it: left out (404 "not mirrored"), not retried forever
            log.warning("mirror: %d bodies over %d B gzipped, not mirrored: %s", len(too_big), self.max_request,
                        too_big[:3])
        sent = 0 if self.fresh else await self._upload({h: b for h, b in blobs.items() if h not in self.acked})
        commit = {"index": index, "pushed_at": pushed_at.isoformat()}
        r = await self._post("commit", commit)
        if r.status_code == 409:  # fresh, or the socket lost blobs (DB reset): send those, commit once more
            missing = set(r.json().get("missing", []))
            self.acked -= missing
            sent += await self._upload({h: b for h, b in blobs.items() if h in missing})
            r = await self._post("commit", commit)
        r.raise_for_status()
        self.acked, self.fresh = set(index.values()), False  # the commit pruned every blob it did not index
        return sent


def _pack(entries: dict[str, Entry], max_request: int) -> tuple[dict[str, str], dict[str, dict], list[str]]:
    """key -> hash index, hash -> blob, and the keys whose blob alone exceeds a request."""
    index: dict[str, str] = {}
    blobs: dict[str, dict] = {}
    too_big: list[str] = []
    for k, e in entries.items():
        gz = gzip.compress(e.body, mtime=0)  # mtime 0: equal bodies, equal hash
        b64 = base64.b64encode(gz).decode()
        if len(b64) + BLOB_OVERHEAD > max_request:
            too_big.append(k)
            continue
        h = hashlib.sha256(gz).hexdigest()
        index[k] = h
        blobs[h] = {"content_type": e.content_type, "gz_b64": b64}
    return index, blobs, too_big


class Mirror:
    """Daemon side: crawl + push every decision interval while the market is open, hourly otherwise.
    Long candles (crawl long_candles) only hourly; between, the last ones ride along."""

    def __init__(self, ctx: AppContext, root: str, pusher: Pusher,
                 crawl_fn: Callable[..., Awaitable[dict[str, Entry]]] = crawl):
        self.ctx, self.root, self.pusher, self.crawl = ctx, root, pusher, crawl_fn
        self.pushed: datetime | None = None
        self.long_candles_at: datetime | None = None
        self.long_candles: dict[str, Entry] = {}  # from the last crawl that took them
        self.failed: datetime | None = None

    @classmethod
    def from_env(cls, ctx: AppContext) -> Mirror | None:
        load_dotenv()
        url, api_key = os.environ.get("JBSB_MIAODA_URL"), os.environ.get("JBSB_MIAODA_KEY")
        if not url or not api_key:
            log.info("mirror off: JBSB_MIAODA_URL / JBSB_MIAODA_KEY not set")
            return None
        return cls(ctx, urlparse(url).path.rstrip("/"), Pusher(url, api_key, httpx.AsyncClient()))

    def _due(self, now: datetime) -> bool:
        if self.failed is not None and now - self.failed < RETRY:
            return False
        if self.pushed is None:
            return True
        db = self.ctx.db
        every = timedelta(minutes=interval.get(db, self.ctx.cfg.settings))
        return now - self.pushed >= (every if db.kv_get("market_open") == "1" else OFF_HOURS)

    async def tick(self, now: datetime) -> None:
        """Never raises: a dead socket must not touch trading."""
        if not self._due(now):
            return
        try:
            with_long = self.long_candles_at is None or now - self.long_candles_at >= OFF_HOURS
            entries = await self.crawl(self.ctx, root=self.root, now=now, long_candles=with_long)
            if with_long:
                self.long_candles = {k: e for k, e in entries.items() if _is_long_candles(k)}
            entries = {**self.long_candles, **entries}
            # always push: /api/status heartbeat moves every step anyway, and the new stamp says the PC is up.
            # Unchanged bodies cost nothing: the socket already holds their blobs
            sent = await self.pusher.push(entries, now)
            log.info("mirror pushed %d keys, %d kB new", len(entries), sent // 1000)
            self.pushed, self.failed = now, None
            if with_long:
                self.long_candles_at = now
        except Exception:
            self.failed = now
            log.exception("mirror push failed; retrying in %s", RETRY)


def _offered(page: bytes, pattern: bytes) -> list[str]:
    """Values a page hands its JS (segmented buttons' data-*, <select> options), in order: the crawl asks exactly
    what the page can, so a template change needs no edit here."""
    return list(dict.fromkeys(m.decode() for m in re.findall(pattern, page)))


def _is_long_candles(k: str) -> bool:
    return k.startswith("/api/candles?") and not any(f"tf={tf}" in k for tf in INTRADAY_SESSIONS)
