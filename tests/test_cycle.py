import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stockbot.agent.cycle import AppContext, run_cycle
from stockbot.agent.rules import Proposal, RuleContext, size_entry
from stockbot.broker.sim import SimBroker
from stockbot.clock import ReplayClock
from stockbot.config import Event, load_config
from stockbot.db import DB
from stockbot.llm.base import LLMError
from stockbot.llm.budget import Budget
from stockbot.llm.fake import FakeLLM
from stockbot.models import Bar, ExitPlan
from stockbot.pause import ALL, set_paused

CFG = load_config(Path(__file__).parent.parent / "config")
SLOT = datetime(2026, 10, 21, 14, 15, tzinfo=UTC)
ARMS = {a.name: a for a in CFG.arms}
PRICES = {"MSFT": 100.0, "NVDA": 180.0, "TSLA": 450.0, "QQQ": 620.0, "AMZN": 230.0,
          "GOOGL": 320.0, "PLTR": 180.0}


class StaticData:
    def __init__(self, prices):
        self.prices = prices

    async def latest_prices(self, symbols):
        return {s: self.prices[s] for s in symbols if s in self.prices}


class Boom:
    async def complete(self, *a, **k):
        raise LLMError("kimi-k3: HTTP 500")


def make_ctx(tmp_path, replies, prices=PRICES, atr=1.0):
    db = DB.open(tmp_path / "t.db")
    for a in CFG.arms:
        db.ensure_account(a.name, 10000)
    clock = ReplayClock(SLOT)
    llm = replies if hasattr(replies, "complete") else FakeLLM(replies)
    db.insert_report(SLOT - timedelta(hours=1), "REPORT TEXT",
                     json.dumps({s: {"atr14_1h": atr} for s in PRICES}))
    ctx = AppContext(CFG, db, StaticData(prices), SimBroker(db, 5, 0), Budget(db, CFG, clock),
                     {"kimi-k3": llm, "deepseek-v4.1-flash": llm}, clock)
    return ctx, llm


def buy(sym="MSFT", **kw):
    d = {"signal": "buy_to_enter", "stop_loss": 95, "profit_target": 110,
         "invalidation_condition": "1h close < 95", "conviction": 1, "justification": "setup"}
    d.update(kw)
    return json.dumps({sym: d})


async def test_duplicate_slot_skipped(tmp_path):
    ctx, llm = make_ctx(tmp_path, ['{"MSFT": {"signal": "hold"}}'])
    r1 = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    r2 = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r1.status == "ok" and r2.status == "skipped_duplicate"
    assert len(llm.calls) == 1


async def test_prompt_has_report_then_state(tmp_path):
    ctx, llm = make_ctx(tmp_path, ['{}'])
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    system, user = llm.calls[0]
    assert "conviction" in system
    assert user.index("REPORT TEXT") < user.index("# CURRENT NAV")


async def test_hold_only_when_paused(tmp_path):
    ctx, llm = make_ctx(tmp_path, ['{}'])
    set_paused(ctx.db, "kimi-improved", True)
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.status == "hold_only" and llm.calls == []
    assert ctx.db.decision_exists("kimi-improved", SLOT)


async def test_hold_only_when_paused_all(tmp_path):
    ctx, llm = make_ctx(tmp_path, ['{}'])
    set_paused(ctx.db, ALL, True)
    assert (await run_cycle(ctx, ARMS["deepseek-baseline"], SLOT, "scheduled")).status == "hold_only"


async def test_hold_only_when_budget_exhausted(tmp_path):
    ctx, llm = make_ctx(tmp_path, ['{}'])
    ctx.db.insert_usage("kimi-k3", None, "decision", SLOT, 0, 0, 0, 80)
    r = await run_cycle(ctx, ARMS["kimi-baseline"], SLOT, "scheduled")
    assert r.status == "hold_only" and llm.calls == []


async def test_llm_failure_all_hold(tmp_path):
    ctx, _ = make_ctx(tmp_path, Boom())
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.status == "llm_failed" and r.orders == 0


async def test_parse_retry_then_ok(tmp_path):
    ctx, llm = make_ctx(tmp_path, ["garbage", buy()])
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.status == "ok" and r.orders == 1
    assert "previous reply was invalid" in llm.calls[1][1]
    purposes = [row["purpose"] for row in ctx.db._q("SELECT purpose FROM llm_usage ORDER BY id")]
    assert purposes == ["decision", "retry"]
    [d] = ctx.db._q("SELECT prompt, raw FROM decisions")
    assert "previous reply was invalid" in d["prompt"]
    assert d["raw"].startswith("garbage") and d["raw"].endswith(buy())


async def test_parse_fail_twice_all_hold(tmp_path):
    ctx, _ = make_ctx(tmp_path, ["garbage", "still garbage"])
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.status == "parse_failed" and r.orders == 0


async def test_improved_rejection_logged(tmp_path):
    ctx, _ = make_ctx(tmp_path, [buy(stop_loss=None)])
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.rejections == 1 and r.orders == 0
    [row] = ctx.db._q("SELECT rule, symbol FROM rejections")
    assert (row["rule"], row["symbol"]) == ("missing_exit_plan", "MSFT")


async def test_improved_sized_order(tmp_path):
    ctx, _ = make_ctx(tmp_path, [buy()])
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    [o] = ctx.db.pending_orders("MSFT")
    rc = RuleContext(10000, 10000, {}, 0, set(), 0.0, {"MSFT": 1.0})
    expected = size_entry(Proposal("MSFT", 100.0, 95, 110, "x", 1.0), rc, CFG.settings)
    assert o["qty"] == pytest.approx(expected)
    assert o["stop_loss"] == 95 and o["risk_per_share"] == pytest.approx(100.05 - 95)


async def test_baseline_uses_llm_quantity(tmp_path):
    ctx, _ = make_ctx(tmp_path, [json.dumps({"MSFT": {"signal": "buy_to_enter", "quantity": 3}})])
    await run_cycle(ctx, ARMS["kimi-baseline"], SLOT, "scheduled")
    [o] = ctx.db.pending_orders("MSFT")
    assert o["qty"] == 3


async def test_baseline_missing_quantity_rejected(tmp_path):
    ctx, _ = make_ctx(tmp_path, [json.dumps({"MSFT": {"signal": "buy_to_enter"}})])
    r = await run_cycle(ctx, ARMS["kimi-baseline"], SLOT, "scheduled")
    assert r.rejections == 1 and ctx.db.pending_orders("MSFT") == []


def hold_msft(ctx, arm, stop=98.0, target=110.0):
    ctx.broker.submit_market(arm, "MSFT", "buy", 10, ExitPlan(stop, target, "x"), "entry",
                             SLOT - timedelta(minutes=30))
    ctx.broker.on_bar(Bar("MSFT", SLOT - timedelta(minutes=29), 100, 100, 100, 100, 1), lambda a: False)


async def test_baseline_due_exit_closed_before_llm(tmp_path):
    ctx, _ = make_ctx(tmp_path, ['{}'], prices={**PRICES, "MSFT": 97.0})
    hold_msft(ctx, "kimi-baseline")
    await run_cycle(ctx, ARMS["kimi-baseline"], SLOT, "scheduled")
    [o] = ctx.db.pending_orders("MSFT")
    assert o["side"] == "sell" and o["reason"] == "stop"


async def test_close_position(tmp_path):
    ctx, _ = make_ctx(tmp_path, [json.dumps({"MSFT": {"signal": "close_position"}})])
    hold_msft(ctx, "kimi-improved")
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    [o] = ctx.db.pending_orders("MSFT")
    assert o["side"] == "sell" and o["qty"] is None and o["reason"] == "close"


async def test_improved_stop_cannot_lower(tmp_path):
    ctx, _ = make_ctx(tmp_path, [json.dumps({"MSFT": {"signal": "hold", "stop_loss": 90, "profit_target": 120}})])
    hold_msft(ctx, "kimi-improved")
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    plan = ctx.db.load_account("kimi-improved").positions["MSFT"].exit_plan
    assert r.rejections == 1 and plan.stop_loss == 98.0 and plan.profit_target == 120


async def test_baseline_hold_updates_plan(tmp_path):
    ctx, _ = make_ctx(tmp_path, [json.dumps({"MSFT": {"signal": "hold", "stop_loss": 90}})])
    hold_msft(ctx, "kimi-baseline")
    await run_cycle(ctx, ARMS["kimi-baseline"], SLOT, "scheduled")
    plan = ctx.db.load_account("kimi-baseline").positions["MSFT"].exit_plan
    assert plan.stop_loss == 90 and plan.profit_target == 110


async def test_notes_and_summary_journaled(tmp_path):
    reply = json.dumps({"MSFT": {"signal": "hold", "justification": "waiting"}, "notes_to_self": "be patient"})
    ctx, llm = make_ctx(tmp_path, [reply, '{}'])
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert ctx.db.journal_recent("kimi-improved", "notes", 1) == ["be patient"]
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT + timedelta(minutes=45), "scheduled")
    assert "be patient" in llm.calls[1][1] and "waiting" in llm.calls[1][1]


async def test_rejected_buy_does_not_consume_entry_slot(tmp_path):
    good = {"signal": "buy_to_enter", "stop_loss": 1, "profit_target": 10_000,
            "invalidation_condition": "x", "conviction": 0.1}
    reply = json.dumps({"MSFT": good, "NVDA": {"signal": "buy_to_enter"},  # NVDA: no exit plan
                        "AMZN": good, "GOOGL": good})
    ctx, _ = make_ctx(tmp_path, [reply])
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.orders == 3 and r.rejections == 1


async def test_same_cycle_entries_count_toward_position_cap(tmp_path):
    good = {"signal": "buy_to_enter", "stop_loss": 1, "profit_target": 10_000,
            "invalidation_condition": "x", "conviction": 0.1}
    reply = json.dumps({"AMZN": good, "GOOGL": good})
    ctx, _ = make_ctx(tmp_path, [reply])
    for i, sym in enumerate(("MSFT", "NVDA", "TSLA", "QQQ")):
        ctx.broker.submit_market("kimi-improved", sym, "buy", 1, ExitPlan(1, 9999, "x"), "entry",
                                 SLOT - timedelta(days=1, minutes=30))
        ctx.broker.on_bar(Bar(sym, SLOT - timedelta(days=1), 100, 100, 100, 100, 1), lambda a: False)
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.orders == 1
    [row] = ctx.db._q("SELECT rule FROM rejections")
    assert row["rule"] == "max_positions"


class Slow:
    async def complete(self, *a, **k):
        raise LLMError("kimi-k3: timeout after 120s", billable=True)


async def test_timeout_cost_estimated_into_budget(tmp_path):
    ctx, _ = make_ctx(tmp_path, Slow())
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.status == "llm_failed"
    [row] = ctx.db._q("SELECT purpose, cost FROM llm_usage")
    assert row["purpose"] == "timeout_estimate" and row["cost"] > 0.05


async def test_http_error_not_billed(tmp_path):
    ctx, _ = make_ctx(tmp_path, Boom())
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert ctx.db._q("SELECT COUNT(*) n FROM llm_usage")[0]["n"] == 0


async def test_apply_crash_records_decision_no_repeat_call(tmp_path, monkeypatch):
    import stockbot.agent.cycle as cyc

    def boom(*a, **k):
        raise RuntimeError("bug in apply")
    monkeypatch.setattr(cyc, "_apply", boom)
    ctx, llm = make_ctx(tmp_path, [buy()])
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.status == "apply_failed"
    assert ctx.db.decision_exists("kimi-improved", SLOT)
    assert (await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")).status == "skipped_duplicate"
    assert len(llm.calls) == 1


async def test_stop_exit_today_blocks_reentry(tmp_path):
    ctx, _ = make_ctx(tmp_path, [buy()])
    hold_msft(ctx, "kimi-improved")
    [f] = ctx.broker.on_bar(Bar("MSFT", SLOT - timedelta(minutes=20), 99, 99, 97, 97, 1), lambda a: True)
    assert f.reason == "stop"
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.orders == 0
    [row] = ctx.db._q("SELECT rule FROM rejections")
    assert row["rule"] == "cooldown"


def set_features(ctx, feats):
    ctx.db.insert_report(SLOT - timedelta(minutes=30), "REPORT TEXT", json.dumps(feats))


async def test_entry_blocked_when_benchmark_below_sma50(tmp_path):
    ctx, _ = make_ctx(tmp_path, [buy()])
    set_features(ctx, {"MSFT": {"atr14_1h": 1.0}, "QQQ": {"sma50_1d": 700.0}})  # QQQ 620 < 700
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    [row] = ctx.db._q("SELECT rule FROM rejections")
    assert r.orders == 0 and row["rule"] == "regime_risk_off"


async def test_entry_allowed_when_benchmark_above_sma50(tmp_path):
    ctx, _ = make_ctx(tmp_path, [buy()])
    set_features(ctx, {"MSFT": {"atr14_1h": 1.0}, "QQQ": {"sma50_1d": 600.0}})
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert r.orders == 1


async def test_improved_prompt_states_regime_rule(tmp_path):
    ctx, llm = make_ctx(tmp_path, ['{}'])
    await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert "50-day average" in llm.calls[0][1]
    assert "Stops trail automatically" in llm.calls[0][1]


@pytest.mark.parametrize("earnings,blocked", [("2026-10-22", True), ("2026-10-21", True),
                                              ("2026-10-23", False), (None, False)])
async def test_entry_blocked_before_earnings(tmp_path, earnings, blocked):
    ctx, _ = make_ctx(tmp_path, [buy()])  # SLOT is Wed 2026-10-21
    set_features(ctx, {"MSFT": {"atr14_1h": 1.0, "next_earnings": earnings}})
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    assert (r.orders == 0) == blocked


async def test_symbol_event_from_config_blocks_entry(tmp_path):
    cfg = dataclasses.replace(CFG, events=(Event(date(2026, 10, 22), None, "MSFT earnings", ("MSFT",)),))
    ctx, _ = make_ctx(tmp_path, [buy()])
    ctx.cfg = cfg
    r = await run_cycle(ctx, ARMS["kimi-improved"], SLOT, "scheduled")
    [row] = ctx.db._q("SELECT rule FROM rejections")
    assert r.orders == 0 and row["rule"] == "earnings_blackout"
