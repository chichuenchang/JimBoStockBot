"""After-action review: one lesson per closed trade, written by the arm's own model and shown
in its journal. TradingAgents-style settle-and-reflect."""
from __future__ import annotations

from datetime import datetime

from stockbot.clock import ET
from stockbot.config import ArmCfg
from stockbot.llm.base import LLMError
from stockbot.models import BENCHMARK, ClosedTrade

REFLECT_SYSTEM = """You review one closed trade of a long-only swing trader (paper account, US stocks).
Write ONE lesson the trader should apply to future decisions: entry timing, stop placement,
target, sizing or holding. Max 2 sentences, under 250 characters. Judge the process, not the
luck: a good trade can lose. No preamble, no markdown."""
MAX_PER_CYCLE = 3  # caps cost when a backlog of trades exists
LESSON_MAX = 300


def _pct(a: float | None, b: float | None) -> float | None:
    return None if not a or b is None else (b / a - 1) * 100


def _facts(t: ClosedTrade, bench_pct: float | None) -> str:
    trade_pct = (t.exit_price / t.entry_price - 1) * 100
    lines = [
        f"Symbol: {t.symbol}",
        f"Entry: {t.entry_price:.2f} at {t.entry_ts.astimezone(ET):%Y-%m-%d %H:%M} ET",
        f"Exit: {t.exit_price:.2f} at {t.exit_ts.astimezone(ET):%Y-%m-%d %H:%M} ET, by {t.exit_reason}",
        f"Held: {t.exit_ts - t.entry_ts}",
        f"P&L: {t.pnl:+.2f} USD ({trade_pct:+.2f}%)"
        + (f", {t.r_multiple:+.2f}R" if t.r_multiple is not None else ""),
        f"Entry reasoning: {t.justification or '(none)'}",
    ]
    if t.exit_justification:
        lines.append(f"Exit reasoning: {t.exit_justification}")
    if bench_pct is not None:
        lines.append(f"QQQ over the same window: {bench_pct:+.2f}%, "
                     f"trade {trade_pct - bench_pct:+.2f} points {'ahead' if trade_pct >= bench_pct else 'behind'}")
    return "\n".join(lines)


def _tag(t: ClosedTrade) -> str:
    return f"{t.symbol} {t.r_multiple:+.2f}R" if t.r_multiple is not None else f"{t.symbol} {t.pnl:+.2f}USD"


async def reflect_new_trades(ctx, arm: ArmCfg, now: datetime) -> None:
    """Lessons for trades closed since the last call. Each trade gets one attempt: a failed
    call is not retried, so a broken provider cannot bill the same review twice."""
    db = ctx.db
    key = f"reflected:{arm.name}"
    done = int(db.kv_get(key) or 0)
    client = ctx.clients.get(arm.model)
    for t in db.closed_trades(arm.name)[done:done + MAX_PER_CYCLE]:
        done += 1
        db.kv_set(key, str(done))
        if client is None or ctx.budget.state(arm.model) == "exhausted":
            continue
        bench = _pct(db.last_snapshot_before(BENCHMARK, t.entry_ts), db.last_snapshot_before(BENCHMARK, t.exit_ts))
        try:
            res, _ = await ctx.budget.complete(client, arm.model, arm.name, "reflection", REFLECT_SYSTEM,
                                               _facts(t, bench), json_mode=False)
        except LLMError:
            continue
        db.journal_append(arm.name, now, "lesson", f"{_tag(t)}: {res.text.strip()}"[:LESSON_MAX])
