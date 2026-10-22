import sqlite3
from datetime import UTC, datetime

import pytest

from stockbot.db import DB
from stockbot.models import AccountState, ExitPlan, Fill, Position

T0 = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    return DB.open(tmp_path / "t.db")


def test_schema_idempotent(tmp_path):
    p = tmp_path / "t.db"
    DB.open(p).close()
    DB.open(p).close()


def test_account_roundtrip(db):
    db.ensure_account("a", 10000)
    db.ensure_account("a", 5)  # no reset
    st = db.load_account("a")
    assert st.cash == 10000 and st.positions == {}
    pos = Position("MSFT", 10.0, 100.0, T0, ExitPlan(98.0, 103.0, "4h close < 97"), 2.0)
    db.save_account(AccountState("a", 9000.0, {"MSFT": pos}))
    assert db.load_account("a") == AccountState("a", 9000.0, {"MSFT": pos})
    db.save_account(AccountState("a", 10050.0, {}))
    assert db.load_account("a").positions == {}


def test_decision_unique_per_slot(db):
    args = dict(arm="a", slot_ts=T0, reason="scheduled", report_id=None, prompt="p", raw="r",
                parsed_json="{}", status="ok", input_tokens=1, cached_tokens=0,
                output_tokens=1, cost=0.1)
    db.insert_decision(**args)
    assert db.decision_exists("a", T0)
    assert not db.decision_exists("b", T0)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_decision(**args)


def test_spent_filters_month_and_model(db):
    oct_ts = datetime(2026, 10, 15, tzinfo=UTC)
    nov_ts = datetime(2026, 11, 15, tzinfo=UTC)
    db.insert_usage("kimi-k3", "a", "decision", oct_ts, 1, 0, 1, 1.5)
    db.insert_usage("deepseek-v4.1-flash", None, "analyst", oct_ts, 1, 0, 1, 0.5)
    db.insert_usage("kimi-k3", "a", "decision", nov_ts, 1, 0, 1, 9)
    assert db.spent("kimi-k3", "2026-10") == pytest.approx(1.5)
    assert db.spent(None, "2026-10") == pytest.approx(2.0)
    assert db.spent("kimi-k3", "2026-12") == 0


def test_spent_month_is_eastern(db):
    # 2026-11-01 03:00 UTC is still Oct 31 in New York
    db.insert_usage("kimi-k3", "a", "decision", datetime(2026, 11, 1, 3, tzinfo=UTC), 1, 0, 1, 2.0)
    assert db.spent("kimi-k3", "2026-10") == pytest.approx(2.0)


def test_closed_trades_pairs_fills(db):
    db.insert_fill(Fill("a", "MSFT", "buy", 10, 100.0, T0, "entry", False),
                   justification="breakout", risk_per_share=2.0)
    db.insert_fill(Fill("a", "MSFT", "sell", 10, 104.0, T0.replace(hour=15), "target", False))
    [t] = db.closed_trades("a")
    assert t.pnl == pytest.approx(40)
    assert t.r_multiple == pytest.approx(2.0)
    assert t.exit_reason == "target"
    assert t.justification == "breakout"


def test_open_trade_not_closed(db):
    db.insert_fill(Fill("a", "MSFT", "buy", 10, 100.0, T0, "entry", False))
    assert db.closed_trades("a") == []


def test_kv_and_journal(db):
    assert db.kv_get("x") is None
    db.kv_set("x", "1")
    db.kv_set("x", "2")
    assert db.kv_get("x") == "2"
    for i in range(4):
        db.journal_append("a", T0, "summary", f"s{i}")
    assert db.journal_recent("a", "summary", 2) == ["s2", "s3"]


def test_report_and_features(db):
    assert db.latest_report() is None
    rid = db.insert_report(T0, "text", '{"MSFT": {"atr14_1h": 1.5}}')
    assert db.latest_report() == (rid, "text")
    assert db.latest_report_features() == {"MSFT": {"atr14_1h": 1.5}}


def test_equity_series_and_last_before(db):
    db.snapshot_equity("a", T0, 10000)
    db.snapshot_equity("a", T0.replace(hour=15), 10100)
    assert db.equity_series("a") == [(T0, 10000), (T0.replace(hour=15), 10100)]
    assert db.last_snapshot_before("a", T0.replace(hour=15)) == 10000
    assert db.last_snapshot_before("a", T0) is None


def test_orders_lifecycle(db):
    oid = db.insert_order("a", "MSFT", "buy", 5, ExitPlan(98, 103, "x"), "entry", T0)
    [o] = db.pending_orders("MSFT")
    assert o["id"] == oid and o["qty"] == 5 and o["stop_loss"] == 98
    db.cancel_order(oid, "insufficient_cash")
    assert db.pending_orders("MSFT") == []


def test_position_peak_roundtrip(db):
    db.ensure_account("a", 1000)
    st = db.load_account("a")
    st.positions["MSFT"] = Position("MSFT", 1, 100, T0, ExitPlan(98, 110), 2.0, peak=104.5)
    db.save_account(st)
    assert db.load_account("a").positions["MSFT"].peak == 104.5


def test_old_db_gains_peak_column(tmp_path):
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE positions (account TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, "
                 "entry_price REAL NOT NULL, entry_ts TEXT NOT NULL, stop_loss REAL, profit_target REAL, "
                 "invalidation TEXT NOT NULL DEFAULT '', risk_per_share REAL, PRIMARY KEY (account, symbol))")
    conn.commit()
    conn.close()
    db = DB.open(p)
    db.ensure_account("a", 1000)
    st = db.load_account("a")
    st.positions["MSFT"] = Position("MSFT", 1, 100, T0, ExitPlan(98, 110), None)
    db.save_account(st)
    assert db.load_account("a").positions["MSFT"].peak is None
