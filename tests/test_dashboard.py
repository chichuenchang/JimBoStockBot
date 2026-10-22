from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockbot.pause import is_paused
from stockbot.clock import ReplayClock
from stockbot.config import load_config
from stockbot.daemon import BENCHMARK
from stockbot.dashboard.app import create_app
from stockbot.dashboard.glossary import TIPS
from stockbot.dashboard.queries import LEADERBOARD_KEYS, leaderboard
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.models import Fill

CFG = load_config(Path(__file__).parent.parent / "config")
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


def test_pause_endpoint_sets_kv(db):
    c = client(db)
    assert c.post("/api/pause/kimi-improved").status_code == 200
    assert is_paused(db, "kimi-improved")
    c.post("/api/resume/kimi-improved")
    assert not is_paused(db, "kimi-improved")
    assert c.post("/api/pause/nope").status_code == 404


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
    assert client(db).get("/arm/kimi-improved").status_code == 200
    assert client(db).get("/arm/nope").status_code == 404


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
