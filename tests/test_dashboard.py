import json
import random
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from stockbot import interval
from stockbot.clock import ReplayClock, et_date
from stockbot.dashboard.app import create_app
from stockbot.dashboard.glossary import BOARDS, COLUMN_GROUPS, TIPS
from stockbot.dashboard.queries import LEADERBOARD_KEYS, boards, daily_pnl, drawdown, leaderboard, market
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.llm.quota import Window
from stockbot.metrics import sharpe
from stockbot.models import BENCHMARK, Fill
from stockbot.pause import is_paused
from tests.snapshot import CFG

T0 = datetime(2026, 10, 20, 14, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    db = DB.open(tmp_path / "t.db")
    for a in ("kimi-improved", "deepseek-baseline", BENCHMARK):
        db.ensure_account(a, 10000)
    # kimi-improved: +5% ; benchmark +2%
    db.snapshot_equity("kimi-improved", T0, 10000)
    db.snapshot_equity("kimi-improved", T0 + timedelta(days=1), 10500)
    db.snapshot_equity(BENCHMARK, T0, 10000)
    db.snapshot_equity(BENCHMARK, T0 + timedelta(days=1), 10200)
    acct = db.load_account("kimi-improved")
    acct.cash = 10500
    db.save_account(acct)
    acct = db.load_account(BENCHMARK)
    acct.cash = 10200
    db.save_account(acct)
    db.insert_fill(Fill("kimi-improved", "MSFT", "buy", 10, 100.05, T0, "entry", False), "why", 2.0)
    db.insert_fill(Fill("kimi-improved", "MSFT", "sell", 10, 105.0, T0 + timedelta(hours=2), "target", False))
    db.insert_usage("kimi-k3", "kimi-improved", "decision", T0, 1, 0, 1, 20.0)
    db.insert_decision("kimi-improved", T0, "scheduled", None, "FULL PROMPT", "RAW REPLY",
                       '{"per_symbol": {"MSFT": {"signal": "hold", "justification": "wait"}}}', "ok", 1, 0, 1, 20.0)
    return db


def client(db):
    clock = ReplayClock(T0 + timedelta(days=1))
    return TestClient(create_app(db, CFG, Budget(db, CFG, clock)))


def row(rows, arm):
    return next(r for r in rows if r["arm"] == arm)


def test_leaderboard_rows(db):
    rows = leaderboard(db, CFG)
    assert {r["arm"] for r in rows} == {"kimi-improved", "deepseek-baseline", BENCHMARK}
    for r in rows:
        assert list(r) == LEADERBOARD_KEYS
    assert row(rows, BENCHMARK)["variant"] == "benchmark"


def test_vs_benchmark(db):
    r = row(leaderboard(db, CFG), "kimi-improved")
    assert r["return_pct"] == pytest.approx(5.0)
    assert r["vs_benchmark_pct"] == pytest.approx(3.0)


def test_net_after_llm(db):
    r = row(leaderboard(db, CFG), "kimi-improved")
    assert r["pnl"] == pytest.approx(500)
    assert r["llm_spend"] == pytest.approx(20)
    assert r["net_after_llm"] == pytest.approx(480)
    assert r["trades"] == 1 and r["win_rate"] == pytest.approx(100.0)
    assert r["costs"] > 0  # slippage estimate on both fills


def test_alpha_beta_and_sharpe_ci_vs_benchmark(tmp_path):
    db = DB.open(tmp_path / "ab.db")
    rng = random.Random(3)
    arm, bench = 10000.0, 10000.0
    for a in ("kimi-improved", BENCHMARK):
        db.ensure_account(a, 10000)
    for i in range(30):
        r = rng.gauss(0, 0.01)
        bench *= 1 + r
        arm *= 1 + 0.5 * r
        db.snapshot_equity("kimi-improved", T0 + timedelta(days=i), arm)
        db.snapshot_equity(BENCHMARK, T0 + timedelta(days=i), bench)
    r = row(leaderboard(db, CFG), "kimi-improved")
    assert r["beta"] == pytest.approx(0.5)
    assert r["alpha_pct"] == pytest.approx(0.0, abs=1e-9)
    lo, hi = r["sharpe_ci"]
    assert lo < hi
    assert row(leaderboard(db, CFG), BENCHMARK)["beta"] is None
    html = client(db).get("/").text
    assert f"{lo:.2f} to {hi:.2f}" in html


def test_sharpe_alpha_beta_hidden_until_enough_days(tmp_path):
    db = DB.open(tmp_path / "few.db")
    for a in ("kimi-improved", BENCHMARK):
        db.ensure_account(a, 10000)
    for i, (arm, bench) in enumerate([(10000, 10000), (10100, 10050), (10050, 10120), (10200, 10180)]):
        db.snapshot_equity("kimi-improved", T0 + timedelta(days=i), arm)
        db.snapshot_equity(BENCHMARK, T0 + timedelta(days=i), bench)
    for arm in ("kimi-improved", BENCHMARK):
        r = row(leaderboard(db, CFG), arm)
        assert r["sharpe"] is None and r["alpha_pct"] is None and r["beta"] is None


def test_sharpe_counts_first_day_vs_start_cash(tmp_path):
    db = DB.open(tmp_path / "first.db")
    db.ensure_account("kimi-improved", 10000)
    rng = random.Random(5)
    navs = [9500.0]  # day one: -5% from start cash
    for _ in range(24):
        navs.append(navs[-1] * (1 + rng.gauss(0.002, 0.01)))
    for i, nav in enumerate(navs):
        db.snapshot_equity("kimi-improved", T0 + timedelta(days=i), nav)
    rets = [-0.05] + [b / a - 1 for a, b in zip(navs, navs[1:])]
    assert row(leaderboard(db, CFG), "kimi-improved")["sharpe"] == pytest.approx(sharpe(rets))


def test_drawdown_chart_counts_from_start_cash(tmp_path):
    db = DB.open(tmp_path / "dd.db")
    db.ensure_account("kimi-improved", 10000)
    for i, nav in enumerate([9500, 9800]):
        db.snapshot_equity("kimi-improved", T0 + timedelta(days=i), nav)
    assert [p[1] for p in drawdown(db)["kimi-improved"]] == pytest.approx([-5.0, -2.0])
    assert row(leaderboard(db, CFG), "kimi-improved")["max_drawdown"] == pytest.approx(5.0)


def test_daily_pnl_day_one_vs_start_cash(tmp_path):
    db = DB.open(tmp_path / "pnl.db")
    db.ensure_account("kimi-improved", 10000)
    for i, nav in enumerate([9500, 9600, 9800]):
        db.snapshot_equity("kimi-improved", T0 + timedelta(hours=i), nav)  # last snapshot of a day wins
    db.snapshot_equity("kimi-improved", T0 + timedelta(days=1), 9700)
    assert [p[1] for p in daily_pnl(db)["kimi-improved"]] == pytest.approx([-200, -100])


def test_alpha_none_with_one_day(db):
    r = row(leaderboard(db, CFG), "kimi-improved")
    assert r["alpha_pct"] is None and r["beta"] is None and r["sharpe_ci"] is None


def test_pause_endpoint_sets_kv(db):
    c = client(db)
    assert c.post("/api/pause/kimi-improved").status_code == 200
    assert is_paused(db, "kimi-improved")
    c.post("/api/resume/kimi-improved")
    assert not is_paused(db, "kimi-improved")
    assert c.post("/api/pause/nope").status_code == 404


def test_interval_endpoint(db):
    c = client(db)
    assert c.post("/api/interval", json={"minutes": 5}).status_code == 200
    assert interval.get(db, CFG.settings) == 5
    assert c.get("/api/status").json()["interval"] == {"minutes": 5, "source": "override"}
    for bad in (0, 61, "abc", 2.5):
        assert c.post("/api/interval", json={"minutes": bad}).status_code == 400
    assert c.post("/api/interval", json={"minutes": 7},
                  headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/interval", json={"minutes": None}).status_code == 200
    assert c.get("/api/status").json()["interval"] == {"minutes": CFG.settings.cycle_every_min,
                                                        "source": "default"}


def tab_arms(html):
    nav = html.split('<nav class="tabs">')[1].split("</nav>")[0]
    return re.findall(r'data-arm="([^"]+)"', nav)


def test_tab_order_endpoint(db):
    c = client(db)
    arms = tab_arms(c.get("/").text)
    assert arms == [a.name for a in CFG.arms if a.enabled]
    moved = arms[2:] + arms[:2]
    assert c.post("/api/tab_order", json={"arms": moved}).status_code == 200
    assert tab_arms(client(db).get("/").text) == moved  # fresh app = daemon restart; order kept
    for bad in (arms[1:], arms + [arms[0]], arms[1:] + ["nope"], "abc"):
        assert c.post("/api/tab_order", json={"arms": bad}).status_code == 400
    # SERIES (colors) stays in config order
    assert f"window.SERIES = {json.dumps(arms + [BENCHMARK])}" in c.get("/").text


def test_tab_order_survives_config_change(db):
    db.kv_set("tab_order", '["rules-momentum", "gone-arm", "kimi-improved"]')
    arms = tab_arms(client(db).get("/").text)
    assert arms[:2] == ["rules-momentum", "kimi-improved"]
    assert sorted(arms) == sorted(a.name for a in CFG.arms if a.enabled)


@pytest.mark.parametrize("raw", ["not json", "42", '{"a": 1}'])
def test_tab_order_bad_value_falls_back(db, raw):
    db.kv_set("tab_order", raw)
    assert tab_arms(client(db).get("/").text) == [a.name for a in CFG.arms if a.enabled]


@pytest.mark.parametrize("used,state", [(0.2, "ok"), (0.9, "warn"), (1.0, "exhausted")])
def test_status_quota(db, used, state):
    db.insert_quota("kimi-k3", T0, [Window("5h", used, T0), Window("7d", 0.1, None)])
    q = client(db).get("/api/status").json()["quota"]
    assert list(q) == ["kimi-k3"] and q["kimi-k3"]["state"] == state
    assert [w["window"] for w in q["kimi-k3"]["windows"]] == ["5h", "7d"]


def test_cross_site_post_refused(db):
    """Any web page the user visits could POST to 127.0.0.1; the browser labels it."""
    c = client(db)
    for site in ("cross-site", "same-site"):
        assert c.post("/api/pause_all", headers={"Sec-Fetch-Site": site}).status_code == 403
    assert not is_paused(db, "kimi-improved")
    assert c.post("/api/pause_all", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_glossary_covers_all_columns():
    assert set(LEADERBOARD_KEYS) - {"arm", "model", "variant"} <= set(TIPS)
    assert all(len(v.split()) <= 25 for v in TIPS.values())
    assert sorted(k for _, keys in COLUMN_GROUPS for k in keys) == sorted(set(LEADERBOARD_KEYS) - {"model", "variant"})


def test_api_shapes(db):
    c = client(db)
    eq = c.get("/api/equity?mode=pct").json()
    assert eq["kimi-improved"][-1][1] == pytest.approx(5.0)
    assert c.get("/api/drawdown").status_code == 200
    assert c.get("/api/daily_pnl").json()["kimi-improved"]
    assert c.get("/api/heatmap").json()["kimi-improved"]["MSFT"] == pytest.approx(49.5)
    st = c.get("/api/status").json()
    assert "budget" in st and "arms" in st and st["quota"] == {}
    trades = c.get("/api/arm/kimi-improved/trades").json()
    assert trades[0]["symbol"] == "MSFT"
    [d] = c.get("/api/arm/kimi-improved/decisions").json()
    full = c.get(f"/api/decision/{d['id']}").json()
    assert full["prompt"] == "FULL PROMPT" and full["raw"] == "RAW REPLY" and full["charts"] == []
    candles = c.get("/api/candles?symbol=MSFT&arm=kimi-improved").json()
    assert len(candles["markers"]) == 2 and candles["bars"] == []


def test_intraday_candles_regular_session_only(db):
    from stockbot.clock import et_at
    from stockbot.models import Bar, Session
    day, half = et_date(T0), et_date(T0) + timedelta(days=1)  # half: early 13:00 close
    times = {"1Min": [(day, hm) for hm in ("08:00", "09:29", "09:30", "15:59", "16:00", "17:00")]
             + [(half, "12:59"), (half, "13:00")],
             "1Hour": [(day, hm) for hm in ("08:00", "09:00", "15:00", "16:00")] + [(half, "12:00"), (half, "13:00")]}

    class Live:
        async def bars(self, symbol, tf, start, end):
            return [Bar(symbol, et_at(d, hm), 1, 1, 1, 1, 1) for d, hm in times[tf]]

        async def calendar(self, start, end):
            return [Session(day, et_at(day, "09:30"), et_at(day, "16:00")),
                    Session(half, et_at(half, "09:30"), et_at(half, "13:00"))]

    def ts(d, hm):
        return int(et_at(d, hm).timestamp())

    c = TestClient(create_app(db, CFG, Budget(db, CFG, ReplayClock(T0)), Live()))
    kept = {tf: [b["time"] for b in c.get(f"/api/candles?symbol=MSFT&arm=kimi-improved&tf={tf}").json()["bars"]]
            for tf in times}
    assert kept == {"1Min": [ts(day, "09:30"), ts(day, "15:59"), ts(half, "12:59")],
                    "1Hour": [ts(day, "09:00"), ts(day, "15:00"), ts(half, "12:00")]}


def test_no_secrets_in_responses(db, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-secret-value")
    c = client(db)
    for path in ["/", "/arm/kimi-improved", "/api/leaderboard", "/api/status", "/api/equity",
                 "/api/arm/kimi-improved/decisions", "/api/arm/kimi-improved/positions"]:
        assert "sk-secret-value" not in c.get(path).text, path


def test_index_renders(db):
    r = client(db).get("/")
    assert r.status_code == 200 and "Leaderboard" in r.text
    assert '<span class="rank">#1</span>' in r.text
    assert client(db).get("/arm/kimi-improved").status_code == 200
    assert client(db).get("/arm/nope").status_code == 404


def test_boards_rank_bots_only_nulls_last(db):
    rows = leaderboard(db, CFG)
    bs = boards(rows, BOARDS)
    assert [b["key"] for b in bs] == [k for _, k, _ in BOARDS]
    for b in bs:
        assert BENCHMARK not in [r["arm"] for r in b["rows"]]
        vals = [r[b["key"]] for r in b["rows"]]
        known = [v for v in vals if v is not None]
        assert vals[:len(known)] == sorted(known, reverse=True)  # None (too few days) sinks


def test_index_layout(db):
    html = client(db).get("/").text
    assert '<aside class="side">' in html and 'id="treemap"' in html and 'id="gainers"' in html
    assert "Full leaderboard" in html and html.count('class="board"') == len(BOARDS)


def test_arm_label_shown_id_kept(db):
    r = client(db).get("/")
    assert '<a href="/arm/deepseek-improved"' in r.text and "Jimbo StockBot</a>" in r.text
    assert "deepseek-improved</a>" not in r.text
    db.ensure_account("deepseek-improved", 10000)
    r = client(db).get("/arm/deepseek-improved")
    assert "<title>Jimbo StockBot · JBSB</title>" in r.text and "Jimbo StockBot</h1>" in r.text
    assert 'window.ARM = "deepseek-improved"' in r.text


def test_replayed_fills_marked(tmp_path):
    db = DB.open(tmp_path / "r.db")
    db.ensure_account("kimi-improved", 10000)
    db.insert_fill(Fill("kimi-improved", "MSFT", "buy", 1, 100, T0, "entry", False))
    db.insert_fill(Fill("kimi-improved", "MSFT", "sell", 1, 97, T0 + timedelta(hours=1), "stop", True))
    c = client(db)
    [t] = c.get("/api/arm/kimi-improved/trades").json()
    assert t["replayed"] is True
    markers = c.get("/api/candles?symbol=MSFT&arm=kimi-improved").json()["markers"]
    assert [m["replayed"] for m in markers] == [False, True]


def test_conviction_api(tmp_path):
    db = DB.open(tmp_path / "c.db")
    db.ensure_account("kimi-improved", 10000)
    db.insert_fill(Fill("kimi-improved", "MSFT", "buy", 1, 100, T0, "entry", False), risk_per_share=2.0,
                   conviction=0.9)
    db.insert_fill(Fill("kimi-improved", "MSFT", "sell", 1, 104, T0 + timedelta(hours=1), "target", False))
    c = client(db)
    [t] = c.get("/api/arm/kimi-improved/trades").json()
    assert t["conviction"] == 0.9
    [row] = c.get("/api/arm/kimi-improved/conviction").json()
    assert row == {"conviction": "high >=0.67", "trades": 1, "win_rate": 100.0, "avg_r": 2.0}


def seed_daily(db):
    from datetime import date

    from tests.test_history import days
    db.upsert_daily(days("MSFT", date(2026, 1, 26), [10, 12, 9, 11, 13, 20]))  # Mon..Fri + next Mon


def test_market_closed_uses_last_session(db):
    from datetime import date
    seed_daily(db)  # MSFT closes 10,12,9,11,13 (Mon-Fri 01-26..30), 20 (Mon 02-02); volume 100 each
    [m] = market(db, ["MSFT", "NOPE"], date(2026, 2, 3), {}, 1, is_open=False)
    assert m["symbol"] == "MSFT" and m["price"] == 20
    assert m["change_pct"] == pytest.approx((20 / 13 - 1) * 100)
    assert m["dollar_volume"] == 2000 and m["share_pct"] == 100
    assert m["spark"] == [10, 12, 9, 11, 13, 20]
    [w] = market(db, ["MSFT"], date(2026, 2, 3), {}, 5, is_open=False)
    assert w["change_pct"] == pytest.approx((20 / 10 - 1) * 100) and w["dollar_volume"] == 100 * (12 + 9 + 11 + 13 + 20)


def test_market_open_uses_live_price(db):
    from datetime import date
    seed_daily(db)
    [m] = market(db, ["MSFT"], date(2026, 2, 2), {"MSFT": 26.0}, 1, is_open=True)  # 02-02 bar not final yet
    assert m["price"] == 26 and m["change_pct"] == pytest.approx(100.0)  # vs Fri close 13
    assert m["spark"] == [10, 12, 9, 11, 13, 26]


def test_api_market(db):
    seed_daily(db)
    r = client(db).get("/api/market?sessions=5").json()
    assert [m["symbol"] for m in r] == ["MSFT"]


def test_api_market_one_feed_call_for_concurrent_polls(db):
    import asyncio

    import httpx

    class Feed:
        calls = 0

        async def latest_prices(self, symbols):
            Feed.calls += 1
            await asyncio.sleep(0.01)  # still in flight when the other polls arrive
            return {"MSFT": 26.0}

    seed_daily(db)
    db.kv_set("market_open", "1")
    app = create_app(db, CFG, Budget(db, CFG, ReplayClock(T0)), Feed())

    async def poll_all():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await asyncio.gather(*(c.get("/api/market") for _ in range(3)))

    assert all(r.status_code == 200 for r in asyncio.run(poll_all()))
    assert Feed.calls == 1


def test_candles_week_from_history(db):
    seed_daily(db)
    bars = client(db).get("/api/candles?symbol=MSFT&arm=kimi-improved&tf=1Week").json()["bars"]
    assert [(b["open"], b["high"], b["low"], b["close"]) for b in bars] == [(10, 14, 8, 13), (20, 21, 19, 20)]
    month = client(db).get("/api/candles?symbol=MSFT&arm=kimi-improved&tf=1Month").json()["bars"]
    assert len(month) == 2
    day = client(db).get("/api/candles?symbol=MSFT&arm=kimi-improved&tf=1Day").json()["bars"]
    assert len(day) == 6


def test_candles_week_empty_history(db):
    r = client(db).get("/api/candles?symbol=MSFT&arm=kimi-improved&tf=1Week")
    assert r.status_code == 200 and r.json()["bars"] == []


def test_chart_png(db):
    seed_daily(db)
    r = client(db).get("/api/chart?symbol=MSFT&tf=1Day&start=2026-01-01&end=2026-03-01&log=true")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"


def test_decision_charts_redraw(db):
    seed_daily(db)
    prompt = "x\n# CHART image 1: MSFT 1Week 2026-01-01..2026-01-30 log\n# CHART 2 skipped: ZZZZ not in universe"
    i = db.insert_decision("kimi-improved", T0 + timedelta(days=1), "scheduled", None, prompt, "{}", "{}", "ok", 0, 0, 0, 0.0)
    c = client(db)
    [chart] = c.get(f"/api/decision/{i}").json()["charts"]
    assert chart == {"symbol": "MSFT", "tf": "1Week", "start": "2026-01-01", "end": "2026-01-30", "log": True}
    r = c.get("/api/chart", params=chart)  # app.js passes the object as-is
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_chart_bad_symbol_or_empty_404(db):
    seed_daily(db)
    assert client(db).get("/api/chart?symbol=ZZZZ&tf=1Day&start=2026-01-01&end=2026-03-01").status_code == 404
    assert client(db).get("/api/chart?symbol=MSFT&tf=1Day&start=2020-01-01&end=2020-03-01").status_code == 404


async def test_candles_day_appends_live_after_history(db):
    from datetime import date

    from stockbot.data.base import DataError
    from tests.test_history import day_bar
    seed_daily(db)

    class Live:
        calls = []

        async def bars(self, symbol, tf, start, end):
            self.calls.append((tf, start))
            if symbol == "NVDA":
                raise DataError("HTTP 500")
            return [day_bar(symbol, date(2026, 2, 3), 21, 22, 20, 21.5)]

    clock = ReplayClock(T0)
    c = TestClient(create_app(db, CFG, Budget(db, CFG, clock), Live()))
    bars = c.get("/api/candles?symbol=MSFT&arm=kimi-improved&tf=1Day").json()["bars"]
    assert [b["close"] for b in bars][-2:] == [20, 21.5]
    assert Live.calls[0] == ("1Day", day_bar("X", date(2026, 2, 3), 0, 0, 0, 0).ts)  # day after last stored
    db.upsert_daily([day_bar("NVDA", date(2026, 1, 26), 1, 2, 0.5, 1.5)])
    r = c.get("/api/candles?symbol=NVDA&arm=kimi-improved&tf=1Week").json()
    assert [b["close"] for b in r["bars"]] == [1.5] and "HTTP 500" in r["error"]  # stored history survives feed error


def test_candles_history_through_today_skips_live(db):
    from tests.test_history import day_bar

    class Live:
        async def bars(self, symbol, tf, start, end):
            raise AssertionError("start after end: Alpaca answers HTTP 400")

    db.upsert_daily([day_bar("MSFT", et_date(datetime.now(UTC)), 1, 2, 0.5, 1.5)])  # evening: close synced
    r = TestClient(create_app(db, CFG, Budget(db, CFG, ReplayClock(T0)), Live())).get(
        "/api/candles?symbol=MSFT&arm=kimi-improved&tf=1Day").json()
    assert r["error"] is None and [b["close"] for b in r["bars"]] == [1.5]


NOW = T0 + timedelta(days=1)  # client() clock: 10:00 ET


def test_usage_page_in_nav(db):
    c = client(db)
    assert '<a href="/usage" class="">Usage</a>' in c.get("/").text
    page = c.get("/usage").text
    assert '<a href="/usage" class="on">Usage</a>' in page and 'data-page="usage"' in page
    assert tab_arms(page) == [a.name for a in CFG.arms if a.enabled]  # Usage tab is not draggable


def test_usage_per_bot(db):
    db.insert_usage("kimi-k3", "kimi-improved", "decision", NOW - timedelta(hours=1), 100, 40, 10, 0.5)
    db.insert_usage("kimi-k3", "kimi-improved", "decision", NOW - timedelta(minutes=5), 200, 0, 20, 0.5)
    db.insert_usage("deepseek-v4.1-flash", None, "analyst", NOW - timedelta(hours=2), 50, 0, 5, 0.1)
    u = client(db).get("/api/usage").json()
    bots = {b["bot"]: b for b in u["bots"]}
    assert list(bots)[:4] == ["kimi-baseline", "kimi-improved", "deepseek-baseline", "deepseek-improved"]
    assert "rules-trend" not in bots  # no model, no tokens
    k = bots["kimi-improved"]
    assert k["today"] == {"calls": 2, "input": 300, "cached": 40, "output": 30, "cost": 1.0}
    assert k["month"]["calls"] == 3 and k["month"]["cost"] == pytest.approx(21.0)  # fixture call: yesterday
    assert bots["analyst"]["model"] == "deepseek-v4.1-flash" and bots["analyst"]["today"]["calls"] == 1
    assert bots["kimi-baseline"]["today"]["calls"] == 0
    assert [p[1] for p in u["series"]["kimi-improved"]] == [110, 330]  # running total, today only
    week = client(db).get("/api/usage?span=7d").json()
    assert week["start"] == datetime(2026, 10, 15, 4, tzinfo=UTC).isoformat()  # ET midnight 6 days back: 7 days
    assert week["series"] == u["series"]  # running total is today's whatever the span
    daily = week["daily"]  # every ET day of the span, empty ones too
    assert [d["day"] for d in daily] == [f"2026-10-{d}" for d in range(15, 22)]
    assert daily[0]["bots"] == {} and daily[-2]["bots"]["kimi-improved"]["calls"] == 1  # fixture call yesterday
    assert daily[-1]["bots"]["kimi-improved"] == k["today"] and daily[-1]["bots"]["analyst"]["calls"] == 1
    assert [d["day"] for d in u["daily"]] == ["2026-10-21"]


def test_usage_polled_model_is_flat(db):
    """A model with quota polls counts as a flat plan even without quota_url in config."""
    db.insert_quota("kimi-k3", NOW, [Window("5h", 0.2, None)])
    assert {b["bot"]: b["flat"] for b in client(db).get("/api/usage").json()["bots"]}["kimi-baseline"] is True


def test_usage_follows_config(db):
    """A bot added to arms.yaml (here on a flat plan not polled yet) or dropped from it needs no code change."""
    import dataclasses

    from stockbot.config import ArmCfg
    from stockbot.dashboard.queries import usage

    plan = dataclasses.replace(CFG.models["gpt"], enabled=True, quota_url="https://q")
    cfg = dataclasses.replace(CFG, models={**CFG.models, "gpt": plan},
                              arms=(*CFG.arms, ArmCfg("new-bot", "gpt", "baseline", True)))
    db.insert_usage("deepseek-v4.1-flash", "old-bot", "decision", NOW - timedelta(hours=1), 10, 0, 5, 0.2)
    u = usage(db, cfg, NOW)
    bots = {b["bot"]: b for b in u["bots"]}
    assert bots["new-bot"]["flat"] and bots["new-bot"]["today"]["calls"] == 0
    assert bots["old-bot"]["model"] == "deepseek-v4.1-flash" and bots["old-bot"]["today"]["cost"] == 0.2


def test_usage_removed_bot_latest_model(db):
    """Model of a bot gone from config = its latest row, not alphabetical max ("kimi-k3" > "deepseek...")."""
    db.insert_usage("kimi-k3", "old-bot", "decision", NOW - timedelta(days=15), 10, 0, 5, 0)
    db.insert_usage("deepseek-v4.1-flash", "old-bot", "decision", NOW - timedelta(days=10), 10, 0, 5, 0.2)
    old = {b["bot"]: b for b in client(db).get("/api/usage").json()["bots"]}["old-bot"]
    assert old["model"] == "deepseek-v4.1-flash" and not old["flat"]
    assert old["today"]["calls"] == 0 and old["month"]["calls"] == 2 and old["month"]["cost"] == 0.2
