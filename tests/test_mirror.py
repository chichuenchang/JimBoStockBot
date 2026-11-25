"""Miaoda mirror: crawl, push, daemon task (spec docs/superpowers/specs/2026-11-24-jbsb-miaoda-mirror-design.md)."""
import asyncio
from datetime import timedelta

from stockbot.clock import ReplayClock
from stockbot.llm.budget import Budget
from stockbot.mirror import MemoData, crawl, key
from stockbot.models import BENCHMARK
from tests.snapshot import CFG
from tests.test_dashboard import T0, db  # noqa: F401  (fixture)


def run_crawl(db, data=None):
    budget = Budget(db, CFG, ReplayClock(T0 + timedelta(days=1)))
    return asyncio.run(crawl(db, CFG, budget, data, root="/app/x", now=T0))


def test_key_sorts_query():
    assert key("/api/x", "b=2&a=1") == "/api/x?a=1&b=2"
    assert key("/api/x", "") == "/api/x"
    assert key("/api/equity", "mode=usd&arms=a,b") == "/api/equity?arms=a%2Cb&mode=usd"  # as encodeURIComponent


def test_crawl_keys(db):
    keys = set(run_crawl(db))
    for k in ("/", "/usage", "/arm/kimi-improved", "/api/status", "/api/leaderboard", "/api/equity?mode=pct",
              "/api/equity?mode=usd", key("/api/equity", f"mode=usd&arms={BENCHMARK},kimi-improved"),
              "/api/market?sessions=21", "/api/usage?span=7d", "/api/arm/kimi-improved/decisions?acted=1",
              "/api/arm/kimi-improved/positions", "/api/candles?symbol=MSFT&tf=1Day",
              "/api/candles?symbol=MSFT&tf=1Min", "/api/arm/kimi-improved/fills?symbol=MSFT"):
        assert k in keys, k
    assert any(k.startswith("/static/core.js?v=") for k in keys)
    assert not [k for k in keys if k.startswith("/api/candles?arm=")]  # bars once, fills apart


def test_crawl_without_long_candles(db):
    budget = Budget(db, CFG, ReplayClock(T0 + timedelta(days=1)))
    keys = set(asyncio.run(crawl(db, CFG, budget, None, root="/app/x", now=T0, long_candles=False)))
    assert "/api/candles?symbol=MSFT&tf=1Min" in keys and "/api/candles?symbol=MSFT&tf=1Hour" in keys
    assert not [k for k in keys if "tf=1Day" in k or "tf=1Week" in k or "tf=1Month" in k]


def test_crawl_pages_carry_root_and_stamp(db):
    page = run_crawl(db)["/"]
    assert page.content_type.startswith("text/html")
    assert b'data-root="/app/x"' in page.body and b'id="mirrorstamp"' in page.body


def test_crawl_no_writes(db):
    assert not [k for k in run_crawl(db) if any(w in k for w in ("pause", "resume", "interval", "tab_order"))]


def test_decisions_follow_listed_ids(db):
    for i in range(1, 61):  # fixture holds T0
        db.insert_decision("kimi-improved", T0 + timedelta(minutes=i), "scheduled", None, "P", "R",
                           '{"per_symbol": {}}', "ok", 1, 0, 1, 0.1)
    detail = sorted(int(k.rsplit("/", 1)[1]) for k in run_crawl(db) if k.startswith("/api/decision/"))
    assert detail == [1, *range(12, 62)]  # what the page lists: the latest 50 + the acted one (T0, filled)


class CountingData:
    def __init__(self):
        self.bars_calls = 0

    async def bars(self, symbol, tf, start, end):
        self.bars_calls += 1
        return []

    async def calendar(self, start, end):
        return []

    async def latest_prices(self, symbols):
        return {}

    async def news(self, symbols, since):
        return []


def test_memo_data_dedupes(db):
    data = CountingData()
    run_crawl(db, data)
    # 1Day/1Week/1Month share one 1Day fetch per symbol, every arm shares the overview's
    assert 0 < data.bars_calls <= len(CFG.settings.universe)


def test_memo_data_filters_later_start():
    class One(CountingData):
        async def bars(self, symbol, tf, start, end):
            await super().bars(symbol, tf, start, end)
            from stockbot.models import Bar
            return [Bar(symbol, T0 + timedelta(minutes=m), 1, 1, 1, 1, 1) for m in range(3)]
    inner = One()
    memo = MemoData(inner)
    asyncio.run(memo.bars("MSFT", "1Min", T0, T0 + timedelta(hours=1)))
    later = asyncio.run(memo.bars("MSFT", "1Min", T0 + timedelta(minutes=1), T0 + timedelta(hours=1)))
    assert inner.bars_calls == 1 and [b.ts for b in later] == [T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]
