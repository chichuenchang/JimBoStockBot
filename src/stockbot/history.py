"""Daily price history since settings.history_start, kept in the daily_bars table."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from stockbot.clock import et_at, et_date
from stockbot.data.base import DataError
from stockbot.db import DB
from stockbot.models import Bar

HistoryTf = Literal["1Day", "1Week", "1Month"]
HISTORY_TFS: tuple[HistoryTf, ...] = ("1Day", "1Week", "1Month")
SPLIT_TOLERANCE = 0.01  # refetched last day off by more = prices were re-adjusted (split)
WICK_SUSPECT = 0.10  # high or low this far past open and close: check it against the day's minutes
WICKS_REPAIRED = "daily_wicks_repaired"  # kv: rows stored before trimming existed have been checked
FEED_DOWN_AFTER = 2  # consecutive failed symbols = feed down, not one bad symbol: stop the sync


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
    return rollup(bars, lambda b: key(et_date(b.ts)))


def rollup(bars: list[Bar], key: Callable[[Bar], object]) -> list[Bar]:
    """One bar per run of equal keys; ts = first bar's ts."""
    out: list[Bar] = []
    last = None
    for b in bars:
        k = key(b)
        if k != last:
            out.append(b)
            last = k
        else:
            p = out[-1]
            out[-1] = Bar(p.symbol, p.ts, p.open, max(p.high, b.high), min(p.low, b.low), b.close,
                          p.volume + b.volume)
    return out


async def sync(db: DB, source: HistorySource, symbols: list[str], start: date, end: datetime) -> int:
    """Fetch each symbol's missing days up to end; returns bars written.
    A failed symbol does not stop the rest; DataError naming the failures is raised after the loop.
    FEED_DOWN_AFTER failures in a row stop it early: each costs the source's full retry backoff."""
    n, failed, streak, repair = 0, [], 0, db.kv_get(WICKS_REPAIRED) is None
    for sym in symbols:
        try:
            n += await _sync_one(db, source, sym, start, end, repair)
            streak = 0
        except DataError as e:
            failed.append(f"{sym}: {e}")
            streak += 1
            if streak == FEED_DOWN_AFTER:
                break
    if failed:
        raise DataError("; ".join(failed) + f" ({n} bars written)")
    if repair:
        db.kv_set(WICKS_REPAIRED, "1")
    return n


async def _sync_one(db: DB, source: HistorySource, sym: str, start: date, end: datetime, repair: bool) -> int:
    origin = et_at(start, "00:00")
    if repair:
        stored = db.daily_bars(sym, start, et_date(end) + timedelta(days=1))
        db.upsert_daily(await _trim_wicks(source, [b for b in stored if _suspect(b)], end))
    last = db.last_daily(sym)
    bars = await source.daily_history(sym, last.ts if last else origin, end)
    if last and bars and bars[0].ts == last.ts and abs(bars[0].close / last.close - 1) > SPLIT_TOLERANCE:
        db.delete_daily(sym)
        bars = await source.daily_history(sym, origin, end)
    db.upsert_daily(await _trim_wicks(source, bars, end))
    return len(bars)
