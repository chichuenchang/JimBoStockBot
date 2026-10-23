from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stockbot.agent.cycle import AppContext
from stockbot.broker.sim import SimBroker
from stockbot.clock import ReplayClock
from stockbot.config import load_config
from stockbot.data.recorded import RecordedData, write_bars
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.models import Bar, ExitPlan, Session
from stockbot.monitor import monitor_tick, wake_due
from stockbot.pause import set_paused

CFG = load_config(Path(__file__).parent / "config")
ARMS = {a.name: a for a in CFG.arms}
OPEN = datetime(2026, 10, 21, 13, 30, tzinfo=UTC)
SESS = [Session(date(2026, 10, 21), OPEN, OPEN + timedelta(hours=6, minutes=30))]


def mk(tmp_path, msft_bars):
    d = tmp_path / "rec"
    write_bars(d, "MSFT", "1Min", msft_bars)
    db = DB.open(tmp_path / "t.db")
    for a in CFG.arms:
        db.ensure_account(a.name, 10000)
    clock = ReplayClock(OPEN + timedelta(hours=7))
    ctx = AppContext(CFG, db, RecordedData(d, clock), SimBroker(db, 5, 0), Budget(db, CFG, clock), {}, clock)
    return ctx


def give(ctx, arm, stop=98.0, target=110.0, entry_ts=OPEN):
    ctx.broker.submit_market(arm, "MSFT", "buy", 10, ExitPlan(stop, target, "x"), "entry",
                             entry_ts - timedelta(minutes=1))
    ctx.broker.on_bar(Bar("MSFT", entry_ts, 100, 100, 100, 100, 1), lambda a: False)


def b(minute, o, h, l, c):
    return Bar("MSFT", OPEN + timedelta(minutes=minute), o, h, l, c, 10)


async def test_baseline_stop_not_enforced_intraday(tmp_path):
    ctx = mk(tmp_path, [b(5, 99, 99, 97, 97.5)])
    give(ctx, "kimi-baseline")
    r = await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    assert r.fills == []
    assert "MSFT" in ctx.db.load_account("kimi-baseline").positions


async def test_improved_stop_enforced(tmp_path):
    ctx = mk(tmp_path, [b(5, 99, 99, 97, 97.5)])
    give(ctx, "kimi-improved")
    r = await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    [f] = r.fills
    assert f.reason == "stop" and f.account == "kimi-improved"


async def test_paused_baseline_stop_enforced(tmp_path):
    ctx = mk(tmp_path, [b(5, 99, 99, 97, 97.5)])
    give(ctx, "kimi-baseline")
    set_paused(ctx.db, "kimi-baseline", True)
    r = await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    assert [f.account for f in r.fills] == ["kimi-baseline"]


async def test_out_of_session_bar_ignored(tmp_path):
    pre = Bar("MSFT", OPEN - timedelta(hours=1), 99, 99, 90, 90, 10)
    ctx = mk(tmp_path, [pre])
    give(ctx, "kimi-improved")
    r = await monitor_tick(ctx, OPEN - timedelta(hours=2), OPEN, SESS, False)
    assert r.fills == [] and r.bars == []
    assert ctx.db.equity_series("kimi-improved") == []


async def test_equity_snapshot_per_minute(tmp_path):
    ctx = mk(tmp_path, [b(5, 100, 101, 99, 101), b(6, 101, 102, 100, 102)])
    give(ctx, "kimi-improved")
    await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    series = ctx.db.equity_series("kimi-improved")
    assert [ts for ts, _ in series] == [OPEN + timedelta(minutes=6), OPEN + timedelta(minutes=7)]
    cash = ctx.db.load_account("kimi-improved").cash
    assert series[-1][1] == pytest.approx(cash + 10 * 102)


async def test_restart_replay_marks_fills(tmp_path):
    ctx = mk(tmp_path, [b(20, 99, 99, 97, 97.5)])
    give(ctx, "kimi-improved")
    r = await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=31), SESS, True)
    assert r.fills[0].replayed
    assert ctx.db.kv_get("last_bar_ts") == (OPEN + timedelta(minutes=31)).isoformat()


def feats(highs=(), lows=()):
    return {"MSFT": {"swing_highs": list(highs), "swing_lows": list(lows)}}


async def test_wake_on_approaching_stop(tmp_path):
    ctx = mk(tmp_path, [])
    give(ctx, "kimi-improved", stop=96.0)
    near = b(10, 98.9, 99, 98.9, 98.9)    # 75% of way 100 -> 96 is 97.0; not yet
    close = b(11, 97.5, 97.5, 96.9, 96.9)
    assert not wake_due(ctx, ARMS["kimi-improved"], near, feats())
    assert wake_due(ctx, ARMS["kimi-improved"], close, feats())


async def test_wake_on_level_cross(tmp_path):
    ctx = mk(tmp_path, [])
    give(ctx, "kimi-improved", stop=50.0)
    assert wake_due(ctx, ARMS["kimi-improved"], b(10, 100, 103, 100, 102.5), feats(highs=[102]))
    assert not wake_due(ctx, ARMS["kimi-improved"], b(11, 102.5, 103, 102.4, 102.8), feats(highs=[102]))


async def test_wake_only_improved_and_held(tmp_path):
    ctx = mk(tmp_path, [])
    give(ctx, "kimi-baseline", stop=96.0)
    crash = b(10, 96.5, 96.5, 96.1, 96.1)
    assert not wake_due(ctx, ARMS["kimi-baseline"], crash, feats())
    assert not wake_due(ctx, ARMS["kimi-improved"], crash, feats())  # not held


async def test_wake_limits(tmp_path):
    ctx = mk(tmp_path, [])
    give(ctx, "kimi-improved", stop=96.0)
    hits = [wake_due(ctx, ARMS["kimi-improved"], b(10 + i * 20, 96.5, 96.5, 96.1, 96.1), feats())
            for i in range(4)]
    assert hits == [True, True, True, False]


async def test_wake_spacing_after_decision(tmp_path):
    ctx = mk(tmp_path, [])
    give(ctx, "kimi-improved", stop=96.0)
    ctx.db.insert_decision("kimi-improved", OPEN + timedelta(minutes=5), "scheduled", None, "", "",
                           "{}", "ok", 0, 0, 0, 0)
    assert not wake_due(ctx, ARMS["kimi-improved"], b(15, 96.5, 96.5, 96.1, 96.1), feats())
    assert wake_due(ctx, ARMS["kimi-improved"], b(21, 96.5, 96.5, 96.1, 96.1), feats())


async def test_improved_stop_trails_to_breakeven_then_fills(tmp_path):
    ctx = mk(tmp_path, [b(5, 101, 102.5, 101, 102), b(6, 101, 101, 99.5, 99.6)])
    ctx.broker.submit_market("kimi-improved", "MSFT", "buy", 10, ExitPlan(98, 110, "x"), "entry",
                             OPEN - timedelta(minutes=1), risk_per_share=2.0)
    ctx.broker.on_bar(Bar("MSFT", OPEN, 100, 100, 100, 100, 1), lambda a: False)
    entry = ctx.db.load_account("kimi-improved").positions["MSFT"].entry_price
    r = await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    [f] = [f for f in r.fills if f.account == "kimi-improved"]
    assert f.reason == "stop" and f.price == pytest.approx(min(entry, 101) * (1 - 5 / 1e4))


async def test_baseline_stop_not_trailed(tmp_path):
    ctx = mk(tmp_path, [b(5, 101, 105, 101, 104)])
    give(ctx, "kimi-baseline")
    await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    assert ctx.db.load_account("kimi-baseline").positions["MSFT"].exit_plan.stop_loss == 98.0


async def test_control_arm_stop_enforced(tmp_path):
    ctx = mk(tmp_path, [b(5, 99, 99, 97, 97.5)])
    give(ctx, "rules-trend")
    r = await monitor_tick(ctx, OPEN + timedelta(minutes=1), OPEN + timedelta(minutes=30), SESS, False)
    assert [f.reason for f in r.fills if f.account == "rules-trend"] == ["stop"]
