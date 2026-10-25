"""Price trajectory PNG for the chart tool and dashboard: close line, price y axis, date x axis."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure  # no pyplot: no global state, safe off the main thread
from matplotlib.ticker import StrMethodFormatter

from stockbot.clock import ET
from stockbot.db import DB
from stockbot.history import HistoryTf, aggregate
from stockbot.models import Bar

SIZE_IN, DPI = (10.24, 5.12), 100  # 1024 x 512 px


@dataclass(frozen=True)
class ChartSpec:
    """One history chart, start..end inclusive: drawn for the bot, re-drawn on the dashboard."""
    symbol: str
    tf: HistoryTf
    start: date
    end: date
    log: bool = False


def history_chart(db: DB, c: ChartSpec) -> bytes:
    """Chart of stored daily history. ValueError when no bars."""
    bars = aggregate(db.daily_bars(c.symbol, c.start, c.end + timedelta(days=1)), c.tf)
    return render(bars, f"{c.symbol} {c.tf} {c.start}..{c.end}" + (" (log scale)" if c.log else ""), c.log)


def render(bars: list[Bar], title: str, log: bool = False) -> bytes:
    buf = io.BytesIO()
    figure(bars, title, log).savefig(buf, format="png")
    return buf.getvalue()


def figure(bars: list[Bar], title: str, log: bool = False) -> Figure:
    if not bars:
        raise ValueError("no bars to draw")
    fig = Figure(figsize=SIZE_IN, dpi=DPI)
    ax = fig.add_subplot()
    ax.plot([b.ts.astimezone(ET).replace(tzinfo=None) for b in bars], [b.close for b in bars], linewidth=1.5)
    if log:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:g}"))  # "100", not "10^2": the model reads prices
    loc = AutoDateLocator()
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(loc))
    ax.set_ylabel("price ($)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig
