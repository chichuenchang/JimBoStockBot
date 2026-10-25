from datetime import UTC, date, datetime, timedelta

import httpx
import respx

from stockbot import history
from stockbot.clock import ET
from stockbot.data.alpaca import AlpacaData
from stockbot.db import DB
from stockbot.models import Bar


def day_bar(sym, d: date, o, h, l, c, v=100.0):
    return Bar(sym, datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC), o, h, l, c, v)


def days(sym, start: date, closes):
    """Consecutive weekdays from start, one bar per close."""
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(day_bar(sym, d, c, c + 1, c - 1, c))
        d += timedelta(days=1)
    return out


class FakeSource:
    def __init__(self, bars):
        self.bars = bars
        self.calls = []

    async def daily_history(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [b for b in self.bars if b.symbol == symbol and start <= b.ts < end]


def test_daily_roundtrip_and_reopen(tmp_path):
    db = DB.open(tmp_path / "h.db")
    bars = days("NVDA", date(2016, 1, 4), [1.0, 2.0, 3.0])
    db.upsert_daily(bars)
    db.upsert_daily(bars[-1:])  # same day again: replaced, not duplicated
    db.close()
    db = DB.open(tmp_path / "h.db")
    assert db.daily_bars("NVDA", date(2016, 1, 4), date(2016, 1, 6)) == bars[:2]  # end exclusive
    assert db.last_daily("NVDA") == bars[-1]
    assert db.last_daily("AAPL") is None
    db.delete_daily("NVDA")
    assert db.daily_bars("NVDA", date(2016, 1, 1), date(2017, 1, 1)) == []


def test_aggregate_week_month():
    # Mon 2026-01-26 .. Fri 01-30, then Mon 02-02
    bars = days("X", date(2026, 1, 26), [10, 12, 9, 11, 13, 20])
    w = history.aggregate(bars, "1Week")
    assert [(b.open, b.high, b.low, b.close, b.volume) for b in w] == [(10, 14, 8, 13, 500), (20, 21, 19, 20, 100)]
    assert w[0].ts == bars[0].ts and w[1].ts == bars[5].ts
    m = history.aggregate(bars, "1Month")
    assert [(b.open, b.close) for b in m] == [(10, 13), (20, 20)]
    assert history.aggregate(bars, "1Day") == bars


async def test_sync_fills_gap(tmp_path):
    db = DB.open(tmp_path / "h.db")
    all_bars = days("NVDA", date(2016, 1, 4), [1, 2, 3, 4, 5])
    src = FakeSource(all_bars[:3])
    end = datetime(2016, 2, 1, tzinfo=UTC)
    assert await history.sync(db, src, ["NVDA"], date(2016, 1, 1), end) == 3
    src.bars = all_bars
    await history.sync(db, src, ["NVDA"], date(2016, 1, 1), end)
    assert src.calls[1][1] == all_bars[2].ts  # resumes from last stored day, not 2016-01-01
    assert [b.close for b in db.daily_bars("NVDA", date(2016, 1, 1), date(2016, 2, 1))] == [1, 2, 3, 4, 5]


async def test_sync_split_refetches(tmp_path):
    db = DB.open(tmp_path / "h.db")
    end = datetime(2016, 2, 1, tzinfo=UTC)
    await history.sync(db, FakeSource(days("NVDA", date(2016, 1, 4), [100, 200])), ["NVDA"], date(2016, 1, 1), end)
    split = FakeSource(days("NVDA", date(2016, 1, 4), [25, 50, 60]))  # 4:1 split: every old close rescaled
    await history.sync(db, split, ["NVDA"], date(2016, 1, 1), end)
    assert split.calls[-1][1] == datetime(2016, 1, 1, tzinfo=ET).astimezone(UTC)
    assert [b.close for b in db.daily_bars("NVDA", date(2016, 1, 1), date(2016, 2, 1))] == [25, 50, 60]


@respx.mock
async def test_alpaca_daily_history_sip():
    route = respx.get("https://data.alpaca.markets/v2/stocks/NVDA/bars").mock(return_value=httpx.Response(
        200, json={"bars": [{"t": "2016-01-04T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}],
                   "next_page_token": None}))
    bars = await AlpacaData("k", "s").daily_history("NVDA", datetime(2016, 1, 1, tzinfo=UTC),
                                                    datetime(2016, 2, 1, tzinfo=UTC))
    assert [b.close for b in bars] == [1.5]
    p = route.calls[0].request.url.params
    assert p["feed"] == "sip" and p["timeframe"] == "1Day" and p["adjustment"] == "split"


def test_history_start_setting():
    from tests.snapshot import CFG
    assert CFG.settings.history_start == date(2016, 1, 1)
