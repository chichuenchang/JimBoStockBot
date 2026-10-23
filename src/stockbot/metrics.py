"""Performance metrics."""
from __future__ import annotations

import math
import random
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
    expectancy: float | None  # mean $ per trade: win rate alone says how often, not how much
    pnl_ex_best: float | None  # realized P&L without the best trade; negative = one lucky trade carries it


def daily_closes(series: list[tuple[datetime, float]]) -> dict[date, float]:
    """Last NAV of each ET day, in day order (series must be time-ordered)."""
    return {et_date(ts): nav for ts, nav in series}


def daily_returns(series: list[tuple[datetime, float]]) -> list[float]:
    navs = list(daily_closes(series).values())
    return [b / a - 1 for a, b in zip(navs, navs[1:]) if a]


def sharpe(daily: list[float]) -> float | None:
    # plain floats, not statistics.stdev (exact fractions): sharpe_ci calls this 1000x per render
    n = len(daily)
    if n < 2 or max(daily) == min(daily):
        return None
    mean = math.fsum(daily) / n
    sd = math.sqrt(math.fsum((x - mean) ** 2 for x in daily) / (n - 1))
    return mean / sd * math.sqrt(252)


def aligned_daily_returns(ca: dict[date, float], cb: dict[date, float]) -> tuple[list[float], list[float]]:
    """Daily returns of two daily_closes over the ET days both have."""
    days = [d for d in ca if d in cb]
    ra, rb = [], []
    for d0, d1 in zip(days, days[1:]):
        if ca[d0] and cb[d0]:
            ra.append(ca[d1] / ca[d0] - 1)
            rb.append(cb[d1] / cb[d0] - 1)
    return ra, rb


def alpha_beta(ra: list[float], rb: list[float]) -> tuple[float | None, float | None]:
    """OLS of arm on benchmark daily returns: (yearly alpha, beta). Alpha = return not
    explained by riding the benchmark."""
    if len(ra) < 2:
        return None, None
    var_b = statistics.variance(rb)
    if var_b == 0:
        return None, None
    beta = statistics.covariance(ra, rb) / var_b
    return (statistics.mean(ra) - beta * statistics.mean(rb)) * 252, beta


SHARPE_CI_MIN_DAYS = 20


def sharpe_ci(daily: list[float], level: float = 0.9, n: int = 1000,
              seed: int = 0) -> tuple[float, float] | None:
    """Bootstrap interval for the Sharpe ratio; None until enough days to mean anything."""
    if len(daily) < SHARPE_CI_MIN_DAYS:
        return None
    rng = random.Random(seed)
    vals = sorted(v for v in (sharpe(rng.choices(daily, k=len(daily))) for _ in range(n))
                  if v is not None)
    if not vals:
        return None
    tail = (1 - level) / 2
    return vals[int(tail * (len(vals) - 1))], vals[int((1 - tail) * (len(vals) - 1))]


def max_drawdown(navs: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


CONVICTION_BUCKETS = ["low <0.33", "mid 0.33-0.67", "high >=0.67", "not given"]


def _bucket(c: float | None) -> str:
    if c is None:
        return CONVICTION_BUCKETS[3]
    return CONVICTION_BUCKETS[0 if c < 0.33 else 1 if c < 0.67 else 2]


def conviction_buckets(trades: list[ClosedTrade]) -> list[dict]:
    """Result by the conviction the model gave at entry. Calibrated: avg R rises with conviction."""
    groups: dict[str, list[ClosedTrade]] = {b: [] for b in CONVICTION_BUCKETS}
    for t in trades:
        groups[_bucket(t.conviction)].append(t)
    out = []
    for b, ts in groups.items():
        if not ts:
            continue
        rs = [t.r_multiple for t in ts if t.r_multiple is not None]
        out.append({"conviction": b, "trades": len(ts), "win_rate": trade_stats(ts).win_rate,
                    "avg_r": statistics.mean(rs) if rs else None})
    return out


def trade_stats(trades: list[ClosedTrade]) -> TradeStats:
    if not trades:
        return TradeStats(0, None, None, None, None, None, None)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_loss = -sum(losses)
    return TradeStats(
        n=len(trades),
        win_rate=len(wins) / len(trades),
        avg_win=statistics.mean(wins) if wins else None,
        avg_loss=statistics.mean(losses) if losses else None,
        profit_factor=sum(wins) / gross_loss if gross_loss > 0 else None,
        expectancy=statistics.mean(t.pnl for t in trades),
        pnl_ex_best=sum(t.pnl for t in trades) - max(t.pnl for t in trades),
    )
