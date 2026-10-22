"""Recorded market data for replay runs and tests. Never returns data from the future."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from stockbot.clock import Clock
from stockbot.data.base import TF_DURATION, Timeframe
from stockbot.models import Bar, NewsItem, Session


def write_bars(dir: Path, symbol: str, timeframe: str, bars: list[Bar]) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": b.ts.isoformat(), "o": b.open, "h": b.high, "l": b.low, "c": b.close,
             "v": b.volume} for b in bars]
    (dir / f"{symbol}_{timeframe}.json").write_text(json.dumps(rows))


def write_news(dir: Path, items: list[NewsItem]) -> None:
    (dir / "news.json").write_text(json.dumps(
        [{"ts": n.ts.isoformat(), "headline": n.headline, "symbols": list(n.symbols),
          "source": n.source} for n in items]))


def write_calendar(dir: Path, sessions: list[Session]) -> None:
    (dir / "calendar.json").write_text(json.dumps(
        [{"date": s.day.isoformat(), "open_utc": s.open_utc.isoformat(),
          "close_utc": s.close_utc.isoformat()} for s in sessions]))


class RecordedData:
    def __init__(self, dir: Path, clock: Clock):
        self.dir = Path(dir)
        self.clock = clock
        self._cache: dict[tuple[str, str], list[Bar]] = {}

    def _load(self, symbol: str, timeframe: str) -> list[Bar]:
        key = (symbol, timeframe)
        if key not in self._cache:
            p = self.dir / f"{symbol}_{timeframe}.json"
            rows = json.loads(p.read_text()) if p.exists() else []
            self._cache[key] = [Bar(symbol, datetime.fromisoformat(r["ts"]), r["o"], r["h"], r["l"],
                                    r["c"], r["v"]) for r in rows]
        return self._cache[key]

    async def bars(self, symbol: str, timeframe: Timeframe, start: datetime,
                   end: datetime) -> list[Bar]:
        now, dur = self.clock.now(), TF_DURATION[timeframe]
        return [b for b in self._load(symbol, timeframe)
                if start <= b.ts < end and b.ts + dur <= now]

    async def latest_prices(self, symbols: list[str]) -> dict[str, float]:
        now, dur = self.clock.now(), TF_DURATION["1Min"]
        out = {}
        for s in symbols:
            done = [b for b in self._load(s, "1Min") if b.ts + dur <= now]
            if done:
                out[s] = done[-1].close
        return out

    async def news(self, symbols: list[str], since: datetime) -> list[NewsItem]:
        p = self.dir / "news.json"
        if not p.exists():
            return []
        now = self.clock.now()
        items = [NewsItem(datetime.fromisoformat(r["ts"]), r["headline"], tuple(r["symbols"]), r["source"])
                 for r in json.loads(p.read_text())]
        return [n for n in items if since <= n.ts < now and set(n.symbols) & set(symbols)]

    async def calendar(self, start: date, end: date) -> list[Session]:
        p = self.dir / "calendar.json"
        rows = json.loads(p.read_text()) if p.exists() else []
        out = [Session(date.fromisoformat(r["date"]), datetime.fromisoformat(r["open_utc"]),
                       datetime.fromisoformat(r["close_utc"])) for r in rows]
        return [s for s in out if start <= s.day <= end]
