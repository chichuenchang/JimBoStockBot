"""Synthetic recorded market data for replay tests."""
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from stockbot.data.recorded import write_bars, write_calendar, write_news
from stockbot.models import Bar, NewsItem, Session

BASE = {"TSLA": 450, "QQQ": 620, "NVDA": 180, "MSFT": 480, "AMZN": 230, "GOOGL": 320, "PLTR": 180}


def _walk(rng, symbol, start, step, n, price):
    out = []
    for i in range(n):
        o = price
        c = max(1.0, o * (1 + rng.gauss(0, 0.002)))
        h, l = max(o, c) * (1 + abs(rng.gauss(0, 0.001))), min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
        out.append(Bar(symbol, start + step * i, round(o, 2), round(h, 2), round(l, 2), round(c, 2), 1000))
        price = c
    return out


def make_day(dir: Path, day: date = date(2026, 10, 20), seed: int = 1) -> Session:
    rng = random.Random(seed)
    open_ = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    session = Session(day, open_, open_ + timedelta(hours=6, minutes=30))
    hist0 = open_ - timedelta(days=45)
    for sym, p in BASE.items():
        write_bars(dir, sym, "1Day", _walk(rng, sym, hist0, timedelta(days=1), 45, p))
        write_bars(dir, sym, "4Hour", _walk(rng, sym, hist0, timedelta(hours=4), 45 * 6, p))
        write_bars(dir, sym, "1Hour", _walk(rng, sym, open_ - timedelta(days=15), timedelta(hours=1), 15 * 24, p))
        write_bars(dir, sym, "1Min", _walk(rng, sym, open_, timedelta(minutes=1), 390, p))
    write_news(dir, [NewsItem(open_ - timedelta(hours=3), "Chip demand strong", ("NVDA",), "synth")])
    write_calendar(dir, [session])
    return session
