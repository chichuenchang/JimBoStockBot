from datetime import timedelta

from stockbot.agent.cycle import run_cycle
from stockbot.agent.reflect import reflect_new_trades
from stockbot.models import BENCHMARK, Fill
from tests.test_cycle import ARMS, SLOT, Boom, make_ctx

ARM = ARMS["kimi-improved"]
T0 = SLOT - timedelta(days=2)


def close_trade(db, sym="MSFT", exit_px=104.0, n=1):
    for i in range(n):
        t = T0 + timedelta(hours=i)
        db.insert_fill(Fill(ARM.name, sym, "buy", 10, 100.0, t, "entry"), "breakout above 99", 2.0)
        db.insert_fill(Fill(ARM.name, sym, "sell", 10, exit_px, t + timedelta(minutes=30), "target"))


async def test_lesson_written_for_closed_trade(tmp_path):
    ctx, llm = make_ctx(tmp_path, ["Took profit at target; size was fine."])
    ctx.db.ensure_account(BENCHMARK, 10000)
    ctx.db.snapshot_equity(BENCHMARK, T0 - timedelta(minutes=1), 10000)
    ctx.db.snapshot_equity(BENCHMARK, T0 + timedelta(minutes=29), 10100)
    close_trade(ctx.db)
    await reflect_new_trades(ctx, ARM, SLOT)
    [(system, user)] = llm.calls
    assert "MSFT" in user and "+2.00R" in user and "breakout above 99" in user
    assert "QQQ over the same window: +1.00%" in user
    assert ctx.db.journal_recent(ARM.name, "lesson", 5) == ["MSFT +2.00R: Took profit at target; size was fine."]
    assert ctx.db.spent_by_arm(ARM.name) > 0


async def test_each_trade_reflected_once(tmp_path):
    ctx, llm = make_ctx(tmp_path, ["lesson"])
    close_trade(ctx.db)
    await reflect_new_trades(ctx, ARM, SLOT)
    await reflect_new_trades(ctx, ARM, SLOT)
    assert len(llm.calls) == 1


async def test_backlog_capped_per_cycle(tmp_path):
    ctx, llm = make_ctx(tmp_path, ["lesson"])
    close_trade(ctx.db, n=5)
    await reflect_new_trades(ctx, ARM, SLOT)
    assert len(llm.calls) == 3
    await reflect_new_trades(ctx, ARM, SLOT)
    assert len(llm.calls) == 5


async def test_failed_call_not_retried(tmp_path):
    ctx, _ = make_ctx(tmp_path, Boom())
    close_trade(ctx.db)
    await reflect_new_trades(ctx, ARM, SLOT)
    assert ctx.db.journal_recent(ARM.name, "lesson", 5) == []
    assert ctx.db.kv_get(f"reflected:{ARM.name}") == "1"


async def test_improved_prompt_shows_lessons(tmp_path):
    ctx, llm = make_ctx(tmp_path, ["Stop was too tight for the ATR.", "{}"])
    close_trade(ctx.db, exit_px=99.0)
    await run_cycle(ctx, ARM, SLOT, "scheduled")
    decision_user = llm.calls[-1][1]
    assert "## Lessons from closed trades" in decision_user
    assert "MSFT -0.50R: Stop was too tight for the ATR." in decision_user


async def test_baseline_never_reflects(tmp_path):
    ctx, llm = make_ctx(tmp_path, ["{}"])
    close_trade(ctx.db)
    await run_cycle(ctx, ARMS["kimi-baseline"], SLOT, "scheduled")
    assert len(llm.calls) == 1
