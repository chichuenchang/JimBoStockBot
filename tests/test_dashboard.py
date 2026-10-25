import random
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from stockbot import interval
from stockbot.clock import ReplayClock, et_date
from stockbot.dashboard.app import create_app
from stockbot.dashboard.glossary import COLUMN_GROUPS, TIPS
from stockbot.dashboard.queries import LEADERBOARD_KEYS, leaderboard
from stockbot.db import DB
from stockbot.llm.budget import Budget
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
    assert "budget" in st and "arms" in st
    trades = c.get("/api/arm/kimi-improved/trades").json()
    assert trades[0]["symbol"] == "MSFT"
    [d] = c.get("/api/arm/kimi-improved/decisions").json()
    full = c.get(f"/api/decision/{d['id']}").json()
    assert full["prompt"] == "FULL PROMPT" and full["raw"] == "RAW REPLY"
    candles = c.get("/api/candles?symbol=MSFT&arm=kimi-improved").json()
    assert len(candles["markers"]) == 2 and candles["bars"] == []


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
