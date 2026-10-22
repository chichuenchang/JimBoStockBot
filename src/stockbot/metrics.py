"""Performance metrics."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime

from stockbot.clock import et_date
from stockbot.models import ClosedTrade


@dataclass(frozen=True)
class TradeStats:
    n: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    profit_factor: float | None


def daily_closes(series: list[tuple[datetime, float]]) -> dict[date, float]:
    """Last NAV of each ET day, in day order (series must be time-ordered)."""
    return {et_date(ts): nav for ts, nav in series}


def daily_returns(series: list[tuple[datetime, float]]) -> list[float]:
    navs = list(daily_closes(series).values())
    return [b / a - 1 for a, b in zip(navs, navs[1:]) if a]


def sharpe(daily: list[float]) -> float | None:
    if len(daily) < 2:
        return None
    sd = statistics.stdev(daily)
    if sd == 0:
        return None
    return statistics.mean(daily) / sd * math.sqrt(252)


def max_drawdown(navs: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def trade_stats(trades: list[ClosedTrade]) -> TradeStats:
    if not trades:
        return TradeStats(0, None, None, None, None)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_loss = -sum(losses)
    return TradeStats(
        n=len(trades),
        win_rate=len(wins) / len(trades),
        avg_win=statistics.mean(wins) if wins else None,
        avg_loss=statistics.mean(losses) if losses else None,
        profit_factor=sum(wins) / gross_loss if gross_loss > 0 else None,
    )
