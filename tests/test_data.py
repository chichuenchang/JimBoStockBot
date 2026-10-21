import json
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from stockbot.clock import ReplayClock
from stockbot.data import alpaca as alpaca_mod
from stockbot.data.alpaca import AlpacaData
from stockbot.data.base import DataError
from stockbot.data.recorded import RecordedData, write_bars

DATA = "https://data.alpaca.markets"
PAPER = "https://paper-api.alpaca.markets"
T0 = datetime(2026, 10, 21, 13, 30, tzinfo=UTC)
T1 = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(alpaca_mod, "_sleep", instant)


def bar_json(t, c):
    return {"t": t, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 100}


@respx.mock
async def test_bars_paginates_and_sends_iex_feed():
    route = respx.get(f"{DATA}/v2/stocks/MSFT/bars").mock(side_effect=[
        httpx.Response(200, json={"bars": [bar_json("2026-10-21T13:30:00Z", 100)], "next_page_token": "p2"}),
        httpx.Response(200, json={"bars": [bar_json("2026-10-21T13:31:00Z", 101)], "next_page_token": None}),
    ])
    bars = await AlpacaData("k", "s").bars("MSFT", "1Min", T0, T1)
    assert [b.close for b in bars] == [100, 101]
    assert bars[0].ts == T0 and bars[0].symbol == "MSFT"
    first = route.calls[0].request
    assert first.url.params["feed"] == "iex" and first.url.params["timeframe"] == "1Min"
    assert first.headers["APCA-API-KEY-ID"] == "k"
    assert route.calls[1].request.url.params["page_token"] == "p2"


@respx.mock
async def test_bars_null_list():
    respx.get(f"{DATA}/v2/stocks/MSFT/bars").mock(
        return_value=httpx.Response(200, json={"bars": None, "next_page_token": None}))
    assert await AlpacaData("k", "s").bars("MSFT", "1Min", T0, T1) == []


@respx.mock
async def test_latest_prices():
    respx.get(f"{DATA}/v2/stocks/trades/latest").mock(return_value=httpx.Response(
        200, json={"trades": {"MSFT": {"t": "2026-10-21T14:00:00Z", "p": 483.1},
                              "NVDA": {"t": "2026-10-21T14:00:00Z", "p": 182.2}}}))
    assert await AlpacaData("k", "s").latest_prices(["MSFT", "NVDA"]) == {"MSFT": 483.1, "NVDA": 182.2}


@respx.mock
async def test_news():
    respx.get(f"{DATA}/v1beta1/news").mock(return_value=httpx.Response(200, json={
        "news": [{"created_at": "2026-10-21T12:00:00Z", "headline": "MSFT up",
                  "symbols": ["MSFT"], "source": "benzinga"}], "next_page_token": None}))
    [n] = await AlpacaData("k", "s").news(["MSFT"], T0)
    assert n.headline == "MSFT up" and n.symbols == ("MSFT",)


@respx.mock
async def test_calendar_converts_et():
    respx.get(f"{PAPER}/v2/calendar").mock(return_value=httpx.Response(
        200, json=[{"date": "2026-11-27", "open": "09:30", "close": "13:00"}]))
    [s] = await AlpacaData("k", "s").calendar(date(2026, 11, 27), date(2026, 11, 27))
    assert s.open_utc == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert s.close_utc == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


@respx.mock
async def test_retries_then_data_error():
    route = respx.get(f"{DATA}/v2/stocks/MSFT/bars").mock(return_value=httpx.Response(429))
    with pytest.raises(DataError):
        await AlpacaData("k", "s").bars("MSFT", "1Min", T0, T1)
    assert route.call_count == 4


@respx.mock
async def test_retry_succeeds():
    respx.get(f"{DATA}/v2/stocks/MSFT/bars").mock(side_effect=[
        httpx.Response(503),
        httpx.Response(200, json={"bars": [bar_json("2026-10-21T13:30:00Z", 100)]}),
    ])
    assert len(await AlpacaData("k", "s").bars("MSFT", "1Min", T0, T1)) == 1


@respx.mock
async def test_timeout_retried_then_error():
    respx.get(f"{DATA}/v2/stocks/trades/latest").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(DataError):
        await AlpacaData("k", "s").latest_prices(["MSFT"])


@respx.mock
async def test_401_not_retried():
    route = respx.get(f"{DATA}/v2/stocks/trades/latest").mock(return_value=httpx.Response(401))
    with pytest.raises(DataError, match="401"):
        await AlpacaData("k", "s").latest_prices(["MSFT"])
    assert route.call_count == 1


def make_recorded(tmp_path):
    from stockbot.models import Bar
    bars = [Bar("MSFT", datetime(2026, 10, 21, 13, 30 + i, tzinfo=UTC), 100 + i, 101 + i, 99 + i, 100.5 + i, 10)
            for i in range(10)]
    write_bars(tmp_path, "MSFT", "1Min", bars)
    (tmp_path / "calendar.json").write_text(json.dumps(
        [{"date": "2026-10-21", "open_utc": "2026-10-21T13:30:00+00:00", "close_utc": "2026-10-21T20:00:00+00:00"}]))
    (tmp_path / "news.json").write_text(json.dumps(
        [{"ts": "2026-10-21T13:35:00+00:00", "headline": "late", "symbols": ["MSFT"], "source": "x"},
         {"ts": "2026-10-21T13:00:00+00:00", "headline": "early", "symbols": ["MSFT"], "source": "x"}]))


async def test_recorded_no_lookahead(tmp_path):
    make_recorded(tmp_path)
    clock = ReplayClock(datetime(2026, 10, 21, 13, 35, tzinfo=UTC))
    rd = RecordedData(tmp_path, clock)
    bars = await rd.bars("MSFT", "1Min", T0, T1)
    assert len(bars) == 5  # 13:30..13:34; the 13:35 bar has not finished yet
    assert await rd.latest_prices(["MSFT", "NVDA"]) == {"MSFT": 104.5}
    assert [n.headline for n in await rd.news(["MSFT"], T0.replace(hour=12))] == ["early"]
    [s] = await rd.calendar(date(2026, 10, 21), date(2026, 10, 21))
    assert s.close_utc == T1
