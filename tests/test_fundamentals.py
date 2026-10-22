from datetime import date

import httpx
import pytest
import respx

from stockbot.data import fundamentals
from stockbot.data.base import DataError
from stockbot.data.fundamentals import AV_URL, AlphaVantage, PartialSnapshot, business_days_until, cached_snapshot
from stockbot.db import DB

TODAY = date(2026, 10, 21)
CSV = ("symbol,name,reportDate,fiscalDateEnding,estimate,currency\r\n"
       "MSFT,Microsoft,2026-10-28,2026-09-30,3.1,USD\r\n"
       "MSFT,Microsoft,2027-01-27,2026-12-31,3.3,USD\r\n"
       "IBM,IBM,2026-10-22,2026-09-30,2.2,USD\r\n"
       "NVDA,Nvidia,2026-10-20,2026-09-30,1.0,USD\r\n")
OVERVIEW = {"Symbol": "MSFT", "PERatio": "35.2", "ForwardPE": "31.0",
            "QuarterlyRevenueGrowthYOY": "0.123", "QuarterlyEarningsGrowthYOY": "None"}


def route(function, **kw):
    return respx.get(AV_URL, params={"function": function}).mock(**kw)


@respx.mock
async def test_snapshot_parses_earnings_and_overview():
    route("EARNINGS_CALENDAR", return_value=httpx.Response(200, text=CSV))
    route("OVERVIEW", side_effect=lambda req: httpx.Response(
        200, json=OVERVIEW if req.url.params["symbol"] == "MSFT" else {}))
    snap = await AlphaVantage("k", gap_s=0).snapshot(["MSFT", "NVDA", "QQQ"], TODAY)
    assert snap["MSFT"] == {"next_earnings": "2026-10-28", "pe_ttm": 35.2, "fwd_pe": 31.0,
                            "rev_growth_yoy_pct": pytest.approx(12.3), "eps_growth_yoy_pct": None}
    assert snap["NVDA"]["next_earnings"] is None  # past date only
    assert snap["QQQ"] == {"next_earnings": None, "pe_ttm": None, "fwd_pe": None,
                           "rev_growth_yoy_pct": None, "eps_growth_yoy_pct": None}


@respx.mock
async def test_rate_limit_note_is_data_error():
    route("EARNINGS_CALENDAR", return_value=httpx.Response(
        200, json={"Information": "We have detected your API key ... 25 requests per day"}))
    with pytest.raises(DataError, match="25 requests"):
        await AlphaVantage("k", gap_s=0).snapshot(["MSFT"], TODAY)


@respx.mock
async def test_failed_overview_keeps_calendar_and_skips_cache(tmp_path):
    route("EARNINGS_CALENDAR", return_value=httpx.Response(200, text=CSV))
    ov = route("OVERVIEW", return_value=httpx.Response(200, json={"Information": "spread out your requests"}))
    db, src = DB.open(tmp_path / "t.db"), AlphaVantage("k", gap_s=0)
    with pytest.raises(PartialSnapshot, match="MSFT"):
        await src.snapshot(["MSFT"], TODAY)
    snap = await cached_snapshot(src, db, ["MSFT"], TODAY)
    assert snap["MSFT"]["next_earnings"] == "2026-10-28" and snap["MSFT"]["pe_ttm"] is None
    await cached_snapshot(src, db, ["MSFT"], TODAY)
    assert ov.call_count == 3  # not cached: every report retries


@respx.mock
async def test_calls_spaced_by_gap(monkeypatch):
    waits = []

    async def sleep(s):
        waits.append(s)
    monkeypatch.setattr(fundamentals.asyncio, "sleep", sleep)
    route("OVERVIEW", return_value=httpx.Response(200, json={}))
    src = AlphaVantage("k", gap_s=5)
    await src.overview("MSFT")
    await src.overview("NVDA")
    assert waits[0] == 0 and 4 < waits[1] <= 5


class Counting:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    async def snapshot(self, symbols, today):
        self.calls += 1
        if self.fail:
            raise DataError("HTTP 503")
        return {s: {"next_earnings": "2026-10-28"} for s in symbols}


async def test_cached_snapshot_fetches_once_per_day(tmp_path):
    db, src = DB.open(tmp_path / "t.db"), Counting()
    a = await cached_snapshot(src, db, ["MSFT"], TODAY)
    b = await cached_snapshot(src, db, ["MSFT"], TODAY)
    assert a == b == {"MSFT": {"next_earnings": "2026-10-28"}} and src.calls == 1
    await cached_snapshot(src, db, ["MSFT"], date(2026, 10, 22))
    assert src.calls == 2


async def test_cached_snapshot_empty_on_error_and_no_source(tmp_path):
    db = DB.open(tmp_path / "t.db")
    assert await cached_snapshot(Counting(fail=True), db, ["MSFT"], TODAY) == {}
    assert await cached_snapshot(None, db, ["MSFT"], TODAY) == {}


@pytest.mark.parametrize("today,d,n", [
    (date(2026, 10, 21), date(2026, 10, 21), 0),   # Wed, same day
    (date(2026, 10, 21), date(2026, 10, 22), 1),   # Wed -> Thu
    (date(2026, 10, 23), date(2026, 10, 26), 1),   # Fri -> Mon
    (date(2026, 10, 21), date(2026, 10, 28), 5),
    (date(2026, 10, 21), date(2026, 10, 20), -1),  # past
])
def test_business_days_until(today, d, n):
    assert business_days_until(today, d) == n
