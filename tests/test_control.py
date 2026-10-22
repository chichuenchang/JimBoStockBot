import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stockbot.agent.control import random_decision, rules_decision
from stockbot.agent.cycle import run_cycle
from stockbot.config import ConfigError, load_config
from stockbot.models import ExitPlan, Position
from tests.test_config import copy_config
from tests.test_cycle import CFG, PRICES, SLOT, make_ctx, set_features

ARMS = {a.name: a for a in CFG.arms}
UNI = ("MSFT", "NVDA")
T = datetime(2026, 10, 21, 15, tzinfo=UTC)
UP = {"trend_4h": "up", "ema20_4h": 98.0, "rsi14_1h": 55.0, "atr14_1h": 1.0}


def held(sym="MSFT"):
    return {sym: Position(sym, 1, 100, T, ExitPlan(95, 110, "x"), 5)}


def test_rules_enters_uptrend_with_2atr_stop_and_2r_target():
    d = rules_decision({}, {"MSFT": 100.0}, {"MSFT": UP}, UNI)
    m = d.per_symbol["MSFT"]
    assert m.signal == "buy_to_enter"
    assert m.stop_loss == pytest.approx(98.0) and m.profit_target == pytest.approx(104.0)
    assert m.invalidation_condition
    assert d.per_symbol["NVDA"].signal == "hold"  # no features, no price


@pytest.mark.parametrize("change", [{"trend_4h": "sideways"}, {"rsi14_1h": 75.0}, {"ema20_4h": 101.0},
                                    {"atr14_1h": None}])
def test_rules_skips_without_setup(change):
    d = rules_decision({}, {"MSFT": 100.0}, {"MSFT": {**UP, **change}}, UNI)
    assert d.per_symbol["MSFT"].signal == "hold"


def test_rules_exits_when_trend_turns_down():
    d = rules_decision(held(), {"MSFT": 100.0}, {"MSFT": {**UP, "trend_4h": "down"}}, UNI)
    assert d.per_symbol["MSFT"].signal == "close_position"
    d = rules_decision(held(), {"MSFT": 100.0}, {"MSFT": UP}, UNI)
    assert d.per_symbol["MSFT"].signal == "hold"


def test_random_is_deterministic_per_seed():
    feats = {s: UP for s in UNI}
    prices = {s: 100.0 for s in UNI}
    a = [random_decision(random.Random(i), {}, prices, feats, UNI) for i in range(50)]
    b = [random_decision(random.Random(i), {}, prices, feats, UNI) for i in range(50)]
    assert a == b
    entries = sum(d.per_symbol[s].signal == "buy_to_enter" for d in a for s in UNI)
    assert 0 < entries < 100
    for d in a:
        for m in d.per_symbol.values():
            if m.signal == "buy_to_enter":
                assert m.stop_loss == pytest.approx(98.0) and m.profit_target == pytest.approx(104.0)


def test_random_exits_held_sometimes():
    exits = sum(random_decision(random.Random(i), held(), {"MSFT": 100.0}, {}, UNI)
                .per_symbol["MSFT"].signal == "close_position" for i in range(100))
    assert 0 < exits < 50


async def test_control_arm_trades_without_llm(tmp_path):
    ctx, llm = make_ctx(tmp_path, [])
    set_features(ctx, {"MSFT": UP})
    r = await run_cycle(ctx, ARMS["rules-trend"], SLOT, "scheduled", {"MSFT": 100.0, "QQQ": 620.0})
    assert r.status == "ok" and r.orders == 1 and llm.calls == []
    [o] = ctx.db.pending_orders("MSFT")
    assert o["account"] == "rules-trend" and o["qty"] > 0
    assert ctx.db.decision_exists("rules-trend", SLOT)


async def test_control_arm_obeys_harness_rules(tmp_path):
    ctx, _ = make_ctx(tmp_path, [])
    set_features(ctx, {"MSFT": UP, "QQQ": {"sma50_1d": 700.0}})
    r = await run_cycle(ctx, ARMS["rules-trend"], SLOT, "scheduled", {"MSFT": 100.0, "QQQ": 620.0})
    assert r.orders == 0 and r.rejections == 1


async def test_random_arm_runs(tmp_path):
    ctx, llm = make_ctx(tmp_path, [])
    for i in range(10):
        r = await run_cycle(ctx, ARMS["random-control"], SLOT + timedelta(minutes=i), "scheduled", PRICES)
        assert r.status == "ok"
    assert llm.calls == []


def test_control_arm_with_model_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text("arms:\n  - {name: r, model: kimi-k3, variant: rules, enabled: true}\n")
    with pytest.raises(ConfigError, match="no model"):
        load_config(d)


def test_llm_arm_without_model_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text("arms:\n  - {name: r, variant: improved, enabled: true}\n")
    with pytest.raises(ConfigError, match="unknown model"):
        load_config(d)


def test_repo_config_has_control_arms():
    cfg = load_config(Path(__file__).parent.parent / "config")
    assert {a.variant for a in cfg.arms if a.model is None} == {"rules", "random"}
