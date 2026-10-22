"""Minute monitor: feed bars to the broker, snapshot equity, detect wake-ups."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from stockbot.agent.cycle import AppContext, hold_only
from stockbot.agent.rules import trail_stop
from stockbot.clock import et_date, in_session
from stockbot.config import ArmCfg
from stockbot.models import Bar, Fill, Session

WAKE_STOP_FRACTION = 0.75
WAKE_MIN_GAP = timedelta(minutes=15)
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class TickResult:
    fills: list[Fill]
    bars: list[Bar]


def _enforce_map(ctx: AppContext) -> dict[str, bool]:
    """Accounts whose stops/targets the harness enforces every minute.
    Active baseline arms are left alone: AA checked exits only when the model ran."""
    arms = {a.name: a for a in ctx.cfg.arms}
    out = {}
    for account in ctx.db.accounts():
        arm = arms.get(account)
        out[account] = arm is None or arm.ruled or hold_only(ctx, arm)
    return out


def _trail(ctx: AppContext, bar: Bar, trailing: set[str], atr: dict[str, float | None]) -> None:
    """Track each trailing position's peak and raise its stop. Runs after the bar's exit
    check, so a stop raised on this bar's high only acts from the next bar."""
    for account in ctx.db.holders(bar.symbol):
        if account not in trailing:
            continue
        st = ctx.db.load_account(account)
        pos = st.positions[bar.symbol]
        peak = max(pos.peak or pos.entry_price, bar.high)
        if peak == pos.peak:
            continue
        pos.peak = peak
        new = trail_stop(pos, atr.get(bar.symbol), ctx.cfg.settings)
        if new is not None:
            pos.exit_plan = replace(pos.exit_plan, stop_loss=new)
        ctx.db.save_account(st)


async def monitor_tick(ctx: AppContext, start: datetime, end: datetime,
                       sessions: list[Session], replayed: bool) -> TickResult:
    bars: list[Bar] = []
    for sym in ctx.cfg.settings.universe:
        bars += await ctx.data.bars(sym, "1Min", start, end)
    bars = [b for b in bars if any(in_session(b.ts, s) for s in sessions)]
    bars.sort(key=lambda b: (b.ts, b.symbol))
    enforce = _enforce_map(ctx)
    trailing = {a.name for a in ctx.cfg.arms if a.enabled and a.ruled}
    atr = {k: v.get("atr14_1h") for k, v in ctx.db.latest_report_features().items()}
    accounts = list(enforce)
    fills: list[Fill] = []
    for i, bar in enumerate(bars):
        fills += ctx.broker.on_bar(bar, lambda a: enforce.get(a, True), replayed)
        _trail(ctx, bar, trailing, atr)
        last_of_minute = i + 1 == len(bars) or bars[i + 1].ts != bar.ts
        if last_of_minute:
            for a in accounts:
                ctx.db.snapshot_equity(a, bar.ts + MINUTE, ctx.broker.nav(a, {}))
    ctx.db.kv_set("last_bar_ts", end.isoformat())
    return TickResult(fills, bars)


def wake_due(ctx: AppContext, arm: ArmCfg, bar: Bar, features: dict) -> bool:
    """Extra decision for an improved arm when a held symbol nears its stop or crosses a
    report swing level. Counts toward the daily wake limit when it returns True."""
    if arm.variant != "improved" or hold_only(ctx, arm):
        return False
    pos = ctx.db.load_account(arm.name).positions.get(bar.symbol)
    if pos is None:
        return False
    sl = pos.exit_plan.stop_loss
    near_stop = sl is not None and sl < pos.entry_price and \
        bar.close <= pos.entry_price - WAKE_STOP_FRACTION * (pos.entry_price - sl)
    f = features.get(bar.symbol) or {}
    lo, hi = min(bar.open, bar.close), max(bar.open, bar.close)
    crossed = any(lo < lvl < hi for lvl in (f.get("swing_highs") or []) + (f.get("swing_lows") or [])
                  if lvl is not None)
    if not (near_stop or crossed):
        return False
    key = f"wakes:{arm.name}:{et_date(bar.ts)}"
    used = int(ctx.db.kv_get(key) or 0)
    if used >= ctx.cfg.settings.max_wakes_per_day:
        return False
    last = ctx.db.last_decision_ts(arm.name)
    if last is not None and bar.ts - last < WAKE_MIN_GAP:
        return False
    ctx.db.kv_set(key, str(used + 1))
    return True
