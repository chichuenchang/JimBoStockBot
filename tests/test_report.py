import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stockbot.clock import ReplayClock
from stockbot.config import load_config
from stockbot.data.recorded import RecordedData, write_bars, write_news
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.llm.fake import FakeLLM
from stockbot.models import Bar, NewsItem
from stockbot.report.analyst import build_report
from stockbot.report.features import render_computed_table, ticker_features

CFG = load_config(Path(__file__).parent.parent / "config")
NOW = datetime(2026, 10, 21, 13, 15, tzinfo=UTC)


def rising(symbol, tf_hours, n, end):
    # every timeframe ends at close 200 so the series agree
    return [Bar(symbol, end - timedelta(hours=tf_hours * (n - i)), 199.5 - (n - 1 - i), 201 - (n - 1 - i),
                199 - (n - 1 - i), 200 - (n - 1 - i), 1000 + i) for i in range(n)]


def test_features_trend_up():
    f = ticker_features("MSFT", rising("MSFT", 1, 60, NOW), rising("MSFT", 4, 80, NOW),
                        rising("MSFT", 24, 30, NOW))
    assert f["trend_4h"] == "up"
    assert f["atr14_1h"] > 0
    assert f["last"] == pytest.approx(200)
    assert set(f) == {"last", "chg_1d_pct", "ema20_4h", "ema50_4h", "rsi14_1h", "macd_hist_1h",
                      "atr14_1h", "swing_highs", "swing_lows", "rel_volume", "trend_4h"}


def test_features_empty_bars():
    f = ticker_features("MSFT", [], [], [])
    assert f["last"] is None and f["trend_4h"] == "unknown"


def test_computed_table_lists_symbols():
    t = render_computed_table({"MSFT": ticker_features("MSFT", [], [], [])})
    assert "| MSFT |" in t


@pytest.fixture
def env(tmp_path):
    d = tmp_path / "rec"
    for s in CFG.settings.universe:
        write_bars(d, s, "1Hour", rising(s, 1, 60, NOW))
        write_bars(d, s, "4Hour", rising(s, 4, 80, NOW))
        write_bars(d, s, "1Day", rising(s, 24, 30, NOW))
    write_news(d, [NewsItem(NOW - timedelta(hours=2), "Big MSFT news", ("MSFT",), "x")])
    clock = ReplayClock(NOW)
    db = DB.open(tmp_path / "t.db")
    return RecordedData(d, clock), db, Budget(db, CFG, clock)


async def test_report_appends_computed_table(env):
    data, db, budget = env
    llm = FakeLLM(["## 0. Context Snapshot\nRisk-on."])
    rid, text = await build_report(data, llm, budget, CFG, db, NOW)
    assert "Risk-on." in text and "## Computed Data (harness, authoritative)" in text
    assert text.startswith("# CURRENT STATE OF MARKETS")
    assert db.latest_report()[0] == rid
    assert db.latest_report_features()["MSFT"]["trend_4h"] == "up"
    _, user = llm.calls[0]
    assert "Big MSFT news" in user and "FOMC" in user


async def test_report_fallback_when_budget_exhausted(env):
    data, db, budget = env
    db.insert_usage(CFG.settings.analyst_model, None, "analyst", NOW, 0, 0, 0, 20)
    llm = FakeLLM(["never"])
    _, text = await build_report(data, llm, budget, CFG, db, NOW)
    assert "Analyst unavailable: budget" in text and "## Computed Data" in text
    assert "Big MSFT news" in text
    assert llm.calls == []


async def test_report_fallback_on_llm_error(env):
    from stockbot.llm.base import LLMError

    class Boom:
        async def complete(self, *a, **k):
            raise LLMError("x: HTTP 500")
    data, db, budget = env
    _, text = await build_report(data, Boom(), budget, CFG, db, NOW)
    assert "Analyst unavailable: x: HTTP 500" in text


async def test_report_records_usage(env):
    data, db, budget = env
    await build_report(data, FakeLLM(["ok"]), budget, CFG, db, NOW)
    assert db.spent(CFG.settings.analyst_model, "2026-10") > 0
