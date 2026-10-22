"""Alpha Vantage earnings dates + company overview. Free key = 25 calls/day, so one snapshot
(1 calendar + 1 overview per symbol) per ET day, cached in kv."""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, timedelta
from typing import Protocol

import httpx

from stockbot.data.base import DataError
from stockbot.db import DB

AV_URL = "https://www.alphavantage.co/query"
log = logging.getLogger("stockbot")
# field -> (OVERVIEW key, scale). Growth comes as a fraction.
OVERVIEW_FIELDS = {"pe_ttm": ("PERatio", 1), "fwd_pe": ("ForwardPE", 1),
                   "rev_growth_yoy_pct": ("QuarterlyRevenueGrowthYOY", 100),
                   "eps_growth_yoy_pct": ("QuarterlyEarningsGrowthYOY", 100)}


class FundamentalsSource(Protocol):
    async def snapshot(self, symbols: list[str], today: date) -> dict[str, dict]: ...


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):  # "None", "-", missing
        return None


def business_days_until(today: date, d: date) -> int:
    """Weekdays after today up to and including d; 0 same day, -1 past. Ignores holidays."""
    if d < today:
        return -1
    return sum(1 for i in range(1, (d - today).days + 1) if (today + timedelta(days=i)).weekday() < 5)


class AlphaVantage:
    def __init__(self, key: str, http: httpx.AsyncClient | None = None, timeout_s: float = 30):
        self.key = key
        self.http = http or httpx.AsyncClient()
        self.timeout = timeout_s

    async def _get(self, params: dict) -> httpx.Response:
        try:
            r = await self.http.get(AV_URL, params={**params, "apikey": self.key}, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise DataError(f"alphavantage {params['function']}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise DataError(f"alphavantage {params['function']}: HTTP {r.status_code}")
        if r.text.lstrip().startswith("{"):  # errors and rate limits come back as JSON, even for CSV calls
            body = r.json()
            msg = body.get("Information") or body.get("Note") or body.get("Error Message")
            if msg:
                raise DataError(f"alphavantage {params['function']}: {msg[:200]}")
        return r

    async def earnings(self, symbols: list[str], today: date) -> dict[str, date]:
        r = await self._get({"function": "EARNINGS_CALENDAR", "horizon": "3month"})
        out: dict[str, date] = {}
        for row in csv.DictReader(io.StringIO(r.text)):
            sym = row.get("symbol")
            try:
                d = date.fromisoformat(row.get("reportDate") or "")
            except ValueError:
                continue
            if sym in symbols and d >= today and (sym not in out or d < out[sym]):
                out[sym] = d
        return out

    async def overview(self, symbol: str) -> dict:
        return (await self._get({"function": "OVERVIEW", "symbol": symbol})).json()

    async def snapshot(self, symbols: list[str], today: date) -> dict[str, dict]:
        earnings = await self.earnings(symbols, today)  # blackout depends on it: errors propagate
        out = {}
        for sym in symbols:
            try:
                ov = await self.overview(sym)
            except DataError as e:  # nice-to-have: one missing overview must not lose the calendar
                log.warning("%s", e)
                ov = {}
            row = {"next_earnings": earnings[sym].isoformat() if sym in earnings else None}
            for field, (key, scale) in OVERVIEW_FIELDS.items():
                v = _num(ov.get(key))
                row[field] = None if v is None else v * scale
            out[sym] = row
        return out


async def cached_snapshot(src: FundamentalsSource | None, db: DB, symbols: list[str],
                          today: date) -> dict[str, dict]:
    """Today's snapshot, fetched at most once per day. {} when no source or the fetch failed
    (retried at the next report)."""
    if src is None:
        return {}
    key = f"fundamentals:{today.isoformat()}"
    raw = db.kv_get(key)
    if raw is not None:
        return json.loads(raw)
    try:
        snap = await src.snapshot(symbols, today)
    except DataError as e:
        log.error("fundamentals: %s", e)
        return {}
    db.kv_set(key, json.dumps(snap))
    return snap
