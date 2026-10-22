from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stockbot.agent.rules import Proposal, RuleContext, check_entry, check_stop_update, size_entry
from stockbot.config import load_config
from stockbot.models import ExitPlan, Position

S = load_config(Path(__file__).parent.parent / "config").settings
T = datetime(2026, 10, 21, 15, tzinfo=UTC)


def ctx(**kw):
    base = dict(nav=10000.0, cash=10000.0, positions={}, entries_today=0, exited_today=set(),
                day_pnl_frac=0.0, atr_1h={"MSFT": 1.0, "NVDA": 1.0})
    base.update(kw)
    return RuleContext(**base)


def prop(**kw):
    base = dict(symbol="MSFT", entry_price=100.0, stop=98.0, target=103.2,
                invalidation="4h close < 97", conviction=1.0)
    base.update(kw)
    return Proposal(**base)


def pos(sym, qty=1.0, entry=100.0):
    return Position(sym, qty, entry, T, ExitPlan(90, 120, "x"), 10)


def test_rr_reject():
    assert check_entry(prop(target=103.0), ctx(), S) == "rr_below_min"


def test_rr_pass_and_size_capped():
    assert check_entry(prop(), ctx(), S) is None
    assert size_entry(prop(), ctx(), S) == pytest.approx(24.987)


def test_size_risk_based():
    p = prop(stop=95.0, target=110.0, conviction=0.5)
    assert size_entry(p, ctx(), S) == pytest.approx(9.900)


def test_size_capped_by_cash():
    p = prop(stop=95.0, target=110.0, conviction=0.5)
    assert size_entry(p, ctx(cash=500.0), S) == pytest.approx(4.997)


def test_missing_conviction_defaults_half():
    p = prop(stop=95.0, target=110.0, conviction=None)
    assert size_entry(p, ctx(), S) == pytest.approx(9.900)


@pytest.mark.parametrize("kw", [dict(stop=None), dict(target=None), dict(invalidation="  ")])
def test_missing_exit_plan(kw):
    assert check_entry(prop(**kw), ctx(), S) == "missing_exit_plan"


def test_stop_not_below_entry():
    assert check_entry(prop(stop=100.5), ctx(), S) == "stop_not_below_entry"


def test_target_not_above_entry():
    assert check_entry(prop(target=99.0), ctx(), S) == "target_not_above_entry"


def test_stop_too_tight():
    p = prop(stop=98.5, target=105.0)
    assert check_entry(p, ctx(atr_1h={"MSFT": 4.0}), S) == "stop_too_tight"


def test_stop_tight_check_skipped_without_atr():
    p = prop(stop=98.5, target=105.0)
    assert check_entry(p, ctx(atr_1h={}), S) is None


def test_max_positions_add_exempt():
    held = {s: pos(s) for s in ("NVDA", "TSLA", "QQQ", "AMZN", "PLTR")}
    c = ctx(positions=held)
    assert check_entry(prop(), c, S) == "max_positions"
    assert check_entry(prop(symbol="NVDA"), c, S) != "max_positions"


def test_max_entries_per_day():
    assert check_entry(prop(), ctx(entries_today=3), S) == "max_entries_per_day"


def test_cooldown():
    assert check_entry(prop(), ctx(exited_today={"MSFT"}), S) == "cooldown"


def test_daily_loss_freeze():
    assert check_entry(prop(), ctx(day_pnl_frac=-0.03), S) == "daily_loss_freeze"


def test_position_cap_reached_on_add():
    c = ctx(positions={"MSFT": pos("MSFT", qty=25, entry=100.0)})
    assert check_entry(prop(), c, S) == "position_cap_reached"


def test_zero_size_when_no_cash():
    assert check_entry(prop(), ctx(cash=0.0), S) == "zero_size"


def test_stop_only_moves_up():
    assert check_stop_update(98, 97) == "stop_lowered"
    assert check_stop_update(98, 99) is None
    assert check_stop_update(None, 97) is None
    assert check_stop_update(98, None) == "stop_removed"
