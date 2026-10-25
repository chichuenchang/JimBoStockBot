"""Daily price history since settings.history_start, kept in the daily_bars table."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from stockbot.clock import et_at, et_date
from stockbot.db import DB
from stockbot.models import Bar

HistoryTf = Literal["1Day", "1Week", "1Month"]
HISTORY_TFS: tuple[HistoryTf, ...] = ("1Day", "1Week", "1Month")
SPLIT_TOLERANCE = 0.01  # refetched last day off by more = prices were re-adjusted (split)


class HistorySource(Protocol):
    async def daily_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...


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
    n = 0
    for sym in symbols:
        last = db.last_daily(sym)
        bars = await source.daily_history(sym, last.ts if last else et_at(start, "00:00"), end)
        if last and bars and bars[0].ts == last.ts and abs(bars[0].close / last.close - 1) > SPLIT_TOLERANCE:
            db.delete_daily(sym)
            bars = await source.daily_history(sym, et_at(start, "00:00"), end)
        db.upsert_daily(bars)
        n += len(bars)
    return n
