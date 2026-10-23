from datetime import UTC, datetime, timedelta

import pytest

from stockbot.broker.sim import SimBroker
from stockbot.db import DB
from stockbot.models import Bar, ExitPlan

T0 = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
ALWAYS = lambda account: True  # noqa: E731
NEVER = lambda account: False  # noqa: E731


def bar(sym, minute, o, h=None, l=None, c=None):
    h = o if h is None else h
    l = o if l is None else l
    c = o if c is None else c
    return Bar(sym, T0 + timedelta(minutes=minute), o, h, l, c, 1000)


@pytest.fixture
def broker(tmp_path):
    db = DB.open(tmp_path / "t.db")
    db.ensure_account("a", 10000)
    return SimBroker(db, slippage_bps=5, commission=0)


def hold(broker, qty=10, stop=98.0, target=103.0, sym="MSFT"):
    broker.submit_market("a", sym, "buy", qty, ExitPlan(stop, target, "x"), "entry", T0)
    broker.on_bar(bar(sym, 1, 100), ALWAYS)


def test_market_buy_fills_next_bar_open_with_slippage(broker):
    broker.submit_market("a", "MSFT", "buy", 10, ExitPlan(98, 103, "x"), "entry", T0)
    [f] = broker.on_bar(bar("MSFT", 1, 100), ALWAYS)
    assert f.price == pytest.approx(100.05) and f.qty == 10 and f.reason == "entry"
    assert broker.db.load_account("a").cash == pytest.approx(8999.5)


def test_conviction_passes_from_order_to_fill(broker):
    broker.submit_market("a", "MSFT", "buy", 10, ExitPlan(98, 103, "x"), "entry", T0, 2.0, "why", 0.7)
    broker.on_bar(bar("MSFT", 1, 100), ALWAYS)
    [row] = broker.db._q("SELECT conviction FROM fills")
    assert row["conviction"] == 0.7


def test_order_not_filled_by_bar_at_or_before_submit(broker):
    broker.submit_market("a", "MSFT", "buy", 10, None, "entry", T0)
    assert broker.on_bar(bar("MSFT", 0, 100), ALWAYS) == []


def test_order_waits_for_symbol_bar(broker):
    broker.submit_market("a", "MSFT", "buy", 10, None, "entry", T0)
    assert broker.on_bar(bar("NVDA", 1, 180), ALWAYS) == []
    [f] = broker.on_bar(bar("MSFT", 3, 100), ALWAYS)
    assert f.ts == T0 + timedelta(minutes=3)


def test_buy_clipped_to_cash(broker):
    acct = broker.db.load_account("a")
    acct.cash = 500
    broker.db.save_account(acct)
    broker.submit_market("a", "MSFT", "buy", 10, None, "entry", T0)
    [f] = broker.on_bar(bar("MSFT", 1, 100), ALWAYS)
    assert f.qty == pytest.approx(4.997)


def test_buy_with_no_cash_cancelled(broker):
    acct = broker.db.load_account("a")
    acct.cash = 0.01
    broker.db.save_account(acct)
    broker.submit_market("a", "MSFT", "buy", 10, None, "entry", T0)
    assert broker.on_bar(bar("MSFT", 1, 100), ALWAYS) == []
    assert broker.db.pending_orders("MSFT") == []


def test_stop_fill(broker):
    hold(broker)
    [f] = broker.on_bar(bar("MSFT", 2, 99, h=99, l=97.5), ALWAYS)
    assert f.reason == "stop" and f.price == pytest.approx(97.951)
    assert broker.db.load_account("a").positions == {}


def test_gap_through_stop(broker):
    hold(broker)
    [f] = broker.on_bar(bar("MSFT", 2, 96, h=96.5, l=95), ALWAYS)
    assert f.price == pytest.approx(95.952)


def test_target_fill(broker):
    hold(broker)
    [f] = broker.on_bar(bar("MSFT", 2, 102, h=103.5, l=101.8), ALWAYS)
    assert f.reason == "target" and f.price == 103.0


def test_stop_wins_same_bar(broker):
    hold(broker)
    [f] = broker.on_bar(bar("MSFT", 2, 100, h=104, l=97), ALWAYS)
    assert f.reason == "stop"


def test_exits_not_enforced_when_flag_false(broker):
    hold(broker)
    assert broker.on_bar(bar("MSFT", 2, 99, l=97), NEVER) == []


def test_due_exits(broker):
    hold(broker)
    assert broker.due_exits("a", {"MSFT": 97.0}) == {"MSFT": "stop"}
    assert broker.due_exits("a", {"MSFT": 104.0}) == {"MSFT": "target"}
    assert broker.due_exits("a", {"MSFT": 100.0}) == {}


def test_full_close_order(broker):
    hold(broker)
    broker.submit_market("a", "MSFT", "sell", None, None, "close", T0 + timedelta(minutes=1))
    [f] = broker.on_bar(bar("MSFT", 2, 101), NEVER)
    assert f.qty == 10 and f.price == pytest.approx(100.9495)


def test_add_averages_entry(broker):
    hold(broker)
    broker.submit_market("a", "MSFT", "buy", 10, None, "add", T0 + timedelta(minutes=1))
    broker.on_bar(bar("MSFT", 2, 102), NEVER)
    p = broker.db.load_account("a").positions["MSFT"]
    assert p.qty == 20 and p.entry_price == pytest.approx((100.05 + 102.051) / 2)
    assert p.exit_plan.stop_loss == 98.0  # kept when add has no plan


def test_nav_uses_last_close_when_no_new_bar(broker):
    broker.submit_market("a", "MSFT", "buy", 10, None, "entry", T0)
    broker.on_bar(bar("MSFT", 1, 100, c=105), ALWAYS)
    cash = broker.db.load_account("a").cash
    assert broker.nav("a", {}) == pytest.approx(cash + 1050)
    assert broker.nav("a", {"MSFT": 110}) == pytest.approx(cash + 1100)


def test_last_close_persists(broker, tmp_path):
    broker.on_bar(bar("MSFT", 1, 100, c=101), ALWAYS)
    again = SimBroker(broker.db, 5, 0)
    assert again.last_close("MSFT") == 101


def test_update_exit_plan(broker):
    hold(broker)
    broker.update_exit_plan("a", "MSFT", ExitPlan(99, 105, "y"))
    assert broker.db.load_account("a").positions["MSFT"].exit_plan == ExitPlan(99, 105, "y")


def test_replayed_flag_set(broker):
    broker.submit_market("a", "MSFT", "buy", 1, None, "entry", T0)
    [f] = broker.on_bar(bar("MSFT", 1, 100), ALWAYS, replayed=True)
    assert f.replayed


def test_crash_mid_fill_leaves_no_partial_state(broker, monkeypatch):
    broker.submit_market("a", "MSFT", "buy", 10, None, "entry", T0)

    def crash(*a):
        raise RuntimeError("power cut")
    monkeypatch.setattr(broker.db, "mark_order_filled", crash)
    with pytest.raises(RuntimeError):
        broker.on_bar(bar("MSFT", 1, 100), ALWAYS)
    assert broker.db.load_account("a").cash == 10000 and broker.db.fills("a") == []
    monkeypatch.undo()
    [f] = broker.on_bar(bar("MSFT", 2, 100), ALWAYS)  # order still pending: fills exactly once
    assert broker.db.load_account("a").positions["MSFT"].qty == f.qty == 10
