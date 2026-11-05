from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from stockbot import history
from stockbot.clock import ET
from stockbot.data.alpaca import AlpacaData
from stockbot.data.base import DataError
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
    def __init__(self, bars, minutes=()):
        self.bars = bars
        self.minutes = minutes
        self.calls = []
        self.minute_calls = []

    async def daily_history(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [b for b in self.bars if b.symbol == symbol and start <= b.ts < end]

    async def minute_history(self, symbol, start, end):
        self.minute_calls.append((symbol, start, end))
        return [b for b in self.minutes if b.symbol == symbol and start <= b.ts < end]


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


async def test_sync_failed_symbol_does_not_block_the_rest(tmp_path):
    class OneBad(FakeSource):
        async def daily_history(self, symbol, start, end):
            if symbol == "BAD":
                raise DataError("HTTP 422")
            return await super().daily_history(symbol, start, end)

    db = DB.open(tmp_path / "h.db")
    end = datetime(2016, 2, 1, tzinfo=UTC)
    src = OneBad(days("NVDA", date(2016, 1, 4), [1, 2]) + days("AAPL", date(2016, 1, 4), [3]))
    with pytest.raises(DataError, match="BAD"):  # still raises: caller retries later
        await history.sync(db, src, ["NVDA", "BAD", "AAPL"], date(2016, 1, 1), end)
    assert db.last_daily("AAPL").close == 3


async def test_sync_stops_when_feed_looks_down(tmp_path):
    class Down(FakeSource):
        async def daily_history(self, symbol, start, end):
            self.calls.append(symbol)
            raise DataError("HTTP 500")

    src = Down([])
    with pytest.raises(DataError):
        await history.sync(DB.open(tmp_path / "h.db"), src, ["A", "B", "C", "D"], date(2016, 1, 1),
                           datetime(2016, 2, 1, tzinfo=UTC))
    assert src.calls == ["A", "B"]


WICK_DAY = date(2024, 6, 10)
SYNC_END = datetime(2024, 7, 1, tzinfo=UTC)


def minutes_ranging(low, high):
    """One day's minutes, 10:00 ET on, reaching exactly low and high."""
    t = datetime(2024, 6, 10, 10, tzinfo=ET).astimezone(UTC)
    return [Bar("NVDA", t, high, high, low, low, 1), Bar("NVDA", t + timedelta(minutes=1), low, high, low, high, 1)]


async def synced_range(tmp_path, day, minutes):
    db = DB.open(tmp_path / "h.db")
    await history.sync(db, FakeSource([day], minutes), ["NVDA"], date(2016, 1, 1), SYNC_END)
    [b] = db.daily_bars("NVDA", WICK_DAY, WICK_DAY + timedelta(days=1))
    return b.low, b.high


async def test_sync_trims_wick_the_minutes_never_reached(tmp_path):
    day = day_bar("NVDA", WICK_DAY, 120.37, 195.95, 117.01, 121.79)  # what Alpaca really returned
    assert await synced_range(tmp_path, day, minutes_ranging(117.01, 123.1)) == (117.01, 123.1)


async def test_sync_keeps_wick_the_minutes_confirm(tmp_path):
    day = day_bar("NVDA", WICK_DAY, 100, 150, 99, 101)
    assert await synced_range(tmp_path, day, minutes_ranging(99, 150)) == (99, 150)


async def test_sync_trim_never_widens_to_extended_hours(tmp_path):
    day = day_bar("NVDA", WICK_DAY, 370, 380, 322, 372)  # minutes run past the day high: pre-market
    assert await synced_range(tmp_path, day, minutes_ranging(364, 382)) == (364, 380)


async def test_sync_trim_keeps_open_and_close_inside_range(tmp_path):
    day = day_bar("NVDA", WICK_DAY, 100, 150, 99, 110)  # official close above every minute's high
    assert await synced_range(tmp_path, day, minutes_ranging(99, 105)) == (99, 110)


async def test_sync_keeps_wick_without_minutes(tmp_path):
    day = day_bar("NVDA", WICK_DAY, 100, 150, 99, 101)
    assert await synced_range(tmp_path, day, []) == (99, 150)


async def test_sync_asks_minutes_only_for_big_wicks_and_never_past_end(tmp_path):
    db = DB.open(tmp_path / "h.db")
    calm, wick = day_bar("NVDA", date(2024, 6, 7), 100, 105, 95, 101), day_bar("NVDA", WICK_DAY, 100, 150, 99, 101)
    src = FakeSource([calm, wick])
    end = wick.ts + timedelta(hours=16, minutes=20)  # today, just after the close: free SIP refuses the last 15 min
    await history.sync(db, src, ["NVDA"], date(2016, 1, 1), end)
    assert src.minute_calls == [("NVDA", wick.ts, end)]


async def test_sync_repairs_stored_wicks_once(tmp_path):
    db = DB.open(tmp_path / "h.db")
    day = day_bar("NVDA", date(2024, 6, 7), 100, 150, 99, 101)
    db.upsert_daily([day, day_bar("NVDA", WICK_DAY, 101, 102, 100, 101)])  # stored before trimming; sync resumes from the last
    src = FakeSource([], [Bar("NVDA", day.ts + timedelta(hours=10), 100, 110, 99, 101, 1)])
    await history.sync(db, src, ["NVDA"], date(2016, 1, 1), SYNC_END)
    assert db.daily_bars("NVDA", day.ts.date(), WICK_DAY)[0].high == 110
    src.minute_calls.clear()
    db.upsert_daily([day])  # bad again: only a second repair would touch it
    await history.sync(db, src, ["NVDA"], date(2016, 1, 1), SYNC_END)
    assert src.minute_calls == []


@respx.mock
async def test_alpaca_minute_history_sip():
    route = respx.get("https://data.alpaca.markets/v2/stocks/NVDA/bars").mock(return_value=httpx.Response(
        200, json={"bars": [{"t": "2024-06-10T14:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}],
                   "next_page_token": None}))
    bars = await AlpacaData("k", "s").minute_history("NVDA", datetime(2024, 6, 10, tzinfo=UTC),
                                                     datetime(2024, 6, 11, tzinfo=UTC))
    assert [b.high for b in bars] == [2]
    p = route.calls[0].request.url.params
    assert p["feed"] == "sip" and p["timeframe"] == "1Min" and p["adjustment"] == "split"


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
