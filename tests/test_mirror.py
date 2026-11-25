"""Miaoda mirror: crawl, push, daemon task (spec docs/superpowers/specs/2026-11-24-jbsb-miaoda-mirror-design.md)."""
import asyncio
import base64
import gzip
import json
import logging
from datetime import timedelta

import httpx
import pytest
import respx

from stockbot import interval
from stockbot.clock import ReplayClock
from stockbot.llm.budget import Budget
from stockbot.mirror import Entry, MemoData, Mirror, Pusher, crawl, key
from stockbot.models import BENCHMARK, Bar
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
    # Miaoda's own static middleware answers /static/* (404 File not found) before the app: the mirror's JS lives apart
    assert any(k.startswith("/jbsb-js/core.js?v=") for k in keys) and not [k for k in keys if k.startswith("/static/")]
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
    assert b'src="/app/x/jbsb-js/core.js?v=' in page.body


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
            return [Bar(symbol, T0 + timedelta(minutes=m), 1, 1, 1, 1, 1) for m in range(3)]
    inner = One()
    memo = MemoData(inner)
    asyncio.run(memo.bars("MSFT", "1Min", T0, T0 + timedelta(hours=1)))
    later = asyncio.run(memo.bars("MSFT", "1Min", T0 + timedelta(minutes=1), T0 + timedelta(hours=1)))
    assert inner.bars_calls == 1 and [b.ts for b in later] == [T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]


# ---------- push: gzip, content-addressed blobs, then one index commit ----------
URL = "https://host/app/app_x"
ENTRIES = {f"/api/k{i}": Entry("application/json", json.dumps({"i": i, "pad": "x" * 300}).encode()) for i in range(3)}


def pusher(max_request=400_000, fresh=False):
    p = Pusher(URL, "KEY", httpx.AsyncClient(), max_request)
    p.fresh = fresh  # warm: has pushed before, so it uploads before committing
    return p


def sent_blobs(route):
    return [b for call in route.calls for b in json.loads(call.request.content)["blobs"]]


@respx.mock
def test_push_chunks_then_commits():
    blobs = respx.post(f"{URL}/openapi/jbsb/blobs").mock(return_value=httpx.Response(200, json={}))
    commit = respx.post(f"{URL}/openapi/jbsb/commit").mock(return_value=httpx.Response(200, json={}))
    asyncio.run(pusher(max_request=700).push(ENTRIES, T0))
    assert blobs.call_count >= 2 and commit.call_count == 1
    index = json.loads(commit.calls[0].request.content)["index"]
    assert set(index) == set(ENTRIES)
    got = {b["hash"]: gzip.decompress(base64.b64decode(b["gz_b64"])) for b in sent_blobs(blobs)}
    assert {index[k]: e.body for k, e in ENTRIES.items()} == got
    assert all(c.request.headers["authorization"] == "Bearer KEY" for c in [*blobs.calls, *commit.calls])


@respx.mock
def test_push_skips_acked():
    blobs = respx.post(f"{URL}/openapi/jbsb/blobs").mock(return_value=httpx.Response(200, json={}))
    commit = respx.post(f"{URL}/openapi/jbsb/commit").mock(return_value=httpx.Response(200, json={}))
    p = pusher()
    asyncio.run(p.push(ENTRIES, T0))
    first = blobs.call_count
    assert asyncio.run(p.push(ENTRIES, T0)) == 0
    assert blobs.call_count == first and commit.call_count == 2


@respx.mock
def test_push_uploads_missing_on_409():
    blobs = respx.post(f"{URL}/openapi/jbsb/blobs").mock(return_value=httpx.Response(200, json={}))
    p = pusher()
    asyncio.run(_ok_push(p))  # all acked now
    lost = next(iter(p.acked))
    commit = respx.post(f"{URL}/openapi/jbsb/commit").mock(side_effect=[
        httpx.Response(409, json={"missing": [lost]}), httpx.Response(200, json={})])
    before = blobs.call_count
    asyncio.run(p.push(ENTRIES, T0))
    assert commit.call_count == 2 and blobs.call_count == before + 1 and sent_blobs(blobs)[-1]["hash"] == lost


@respx.mock
def test_fresh_pusher_asks_socket_first():
    """A restarted daemon knows nothing acked: commit first, upload only what the socket lacks."""
    blobs = respx.post(f"{URL}/openapi/jbsb/blobs").mock(return_value=httpx.Response(200, json={}))
    p = pusher(fresh=True)
    index = {}

    def first(request):
        index.update(json.loads(request.content)["index"])
        return httpx.Response(409, json={"missing": [index["/api/k0"]]})
    commit = respx.post(f"{URL}/openapi/jbsb/commit").mock(side_effect=[first, httpx.Response(200, json={})])
    asyncio.run(p.push(ENTRIES, T0))
    assert commit.call_count == 2 and [b["hash"] for b in sent_blobs(blobs)] == [index["/api/k0"]]
    assert p.acked == set(index.values())


async def _ok_push(p):
    with respx.mock:
        respx.post(f"{URL}/openapi/jbsb/blobs").mock(return_value=httpx.Response(200, json={}))
        respx.post(f"{URL}/openapi/jbsb/commit").mock(return_value=httpx.Response(200, json={}))
        await p.push(ENTRIES, T0)


@respx.mock
def test_push_no_commit_on_blob_error():
    respx.post(f"{URL}/openapi/jbsb/blobs").mock(return_value=httpx.Response(500))
    commit = respx.post(f"{URL}/openapi/jbsb/commit").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(pusher().push(ENTRIES, T0))
    assert commit.call_count == 0


# ---------- daemon task: cadence, never raises ----------
class FakePusher:
    def __init__(self, fail=False):
        self.pushes, self.fail = [], fail

    async def push(self, entries, pushed_at):
        if self.fail:
            raise httpx.ConnectError("down")
        self.pushes.append((pushed_at, entries))
        return 1


def mirror(db, p, crawls):
    async def counting(*a, **kw):
        crawls.append(kw.get("long_candles", True))
        return await crawl(*a, **kw)
    return Mirror(db, CFG, Budget(db, CFG, ReplayClock(T0)), None, "/app/x", p, crawl_fn=counting)


def test_mirror_off_without_env(monkeypatch, caplog):
    monkeypatch.delenv("JBSB_MIAODA_URL", raising=False)
    monkeypatch.setattr("stockbot.mirror.load_dotenv", lambda: None)
    with caplog.at_level(logging.INFO, "stockbot.mirror"):
        assert Mirror.from_env(None) is None
    assert "mirror off" in caplog.text


def test_tick_swallows_errors(db, caplog):
    crawls = []
    m = mirror(db, FakePusher(fail=True), crawls)
    asyncio.run(m.tick(T0))  # no raise
    assert "mirror push failed" in caplog.text
    # socket down: back off, a crawl is ~35 market-data calls the trading loop shares a rate limit with
    asyncio.run(m.tick(T0 + timedelta(seconds=30)))
    assert len(crawls) == 1
    asyncio.run(m.tick(T0 + timedelta(minutes=5)))
    assert len(crawls) == 2


def test_tick_cadence(db):
    crawls, p = [], FakePusher()
    m = mirror(db, p, crawls)
    db.kv_set("market_open", "1")
    interval.set(db, 5)
    for minutes in (0, 4, 5):  # open: every decision interval
        asyncio.run(m.tick(T0 + timedelta(minutes=minutes)))
    assert crawls == [True, False]
    db.kv_set("market_open", "0")
    for minutes in (30, 59, 66):  # closed: hourly; the hourly crawl takes long candles again
        asyncio.run(m.tick(T0 + timedelta(minutes=minutes)))
    assert crawls == [True, False, True]


def test_tick_skips_unchanged_push_and_keeps_long_candles(db):
    crawls, p = [], FakePusher()
    m = mirror(db, p, crawls)
    db.kv_set("market_open", "1")
    interval.set(db, 5)
    asyncio.run(m.tick(T0))
    db.snapshot_equity("kimi-improved", T0 + timedelta(minutes=5), 10600)  # /api data moved
    asyncio.run(m.tick(T0 + timedelta(minutes=5)))
    asyncio.run(m.tick(T0 + timedelta(minutes=10)))  # nothing moved: crawled, not pushed
    assert len(crawls) == 3 and len(p.pushes) == 2
    assert "/api/candles?symbol=MSFT&tf=1Day" in p.pushes[1][1]  # short crawl keeps the last long candles
