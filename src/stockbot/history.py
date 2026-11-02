"""Daily price history since settings.history_start, kept in the daily_bars table."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from stockbot.clock import et_at, et_date
from stockbot.db import DB
from stockbot.models import Bar

HistoryTf = Literal["1Day", "1Week", "1Month"]
HISTORY_TFS: tuple[HistoryTf, ...] = ("1Day", "1Week", "1Month")
SPLIT_TOLERANCE = 0.01  # refetched last day off by more = prices were re-adjusted (split)
WICK_SUSPECT = 0.10  # high or low this far past open and close: check it against the day's minutes
WICKS_REPAIRED = "daily_wicks_repaired"  # kv: rows stored before trimming existed have been checked


class HistorySource(Protocol):
    async def daily_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...
    async def minute_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...


def _suspect(b: Bar) -> bool:
    return b.high > max(b.open, b.close) * (1 + WICK_SUSPECT) or b.low < min(b.open, b.close) * (1 - WICK_SUSPECT)


async def _trim_wicks(source: HistorySource, bars: list[Bar], end: datetime) -> list[Bar]:
    """Alpaca SIP day bars hold extremes its own minutes never reached (NVDA 2024-06-10: high 195.95, minutes 123.10).
    Only narrows: minutes include extended hours, day bars do not."""
    out = []
    for b in bars:
        minutes = await source.minute_history(b.symbol, b.ts, min(b.ts + timedelta(days=1), end)) if _suspect(b) else None
        if minutes:
            b = replace(b, high=max(min(b.high, max(m.high for m in minutes)), b.open, b.close),
                        low=min(max(b.low, min(m.low for m in minutes)), b.open, b.close))
        out.append(b)
    return out


def aggregate(bars: list[Bar], tf: HistoryTf) -> list[Bar]:
    """Daily bars into ISO weeks or calendar months; ts = first day's ts."""
    if tf == "1Day":
        return bars
    key = (lambda d: d.isocalendar()[:2]) if tf == "1Week" else (lambda d: (d.year, d.month))
    out: list[Bar] = []
    last = None
    for b in bars:
        k = key(et_date(b.ts))
        if k != last:
            out.append(b)
            last = k
        else:
            p = out[-1]
            out[-1] = Bar(p.symbol, p.ts, p.open, max(p.high, b.high), min(p.low, b.low), b.close,
                          p.volume + b.volume)
    return out


async def sync(db: DB, source: HistorySource, symbols: list[str], start: date, end: datetime) -> int:
    """Fetch each symbol's missing days up to end; returns bars written."""
    n, origin, repair = 0, et_at(start, "00:00"), db.kv_get(WICKS_REPAIRED) is None
    for sym in symbols:
        if repair:
            stored = db.daily_bars(sym, start, et_date(end) + timedelta(days=1))
            db.upsert_daily(await _trim_wicks(source, [b for b in stored if _suspect(b)], end))
        last = db.last_daily(sym)
        bars = await source.daily_history(sym, last.ts if last else origin, end)
        if last and bars and bars[0].ts == last.ts and abs(bars[0].close / last.close - 1) > SPLIT_TOLERANCE:
            db.delete_daily(sym)
            bars = await source.daily_history(sym, origin, end)
        db.upsert_daily(await _trim_wicks(source, bars, end))
        n += len(bars)
    if repair:
        db.kv_set(WICKS_REPAIRED, "1")
    return n
