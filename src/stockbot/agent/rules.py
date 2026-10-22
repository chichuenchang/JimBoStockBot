"""Improved-arm entry rules and risk-based sizing. The LLM proposes; these decide."""
from __future__ import annotations

from dataclasses import dataclass

from stockbot.broker.sim import buy_price, floor3
from stockbot.config import Settings
from stockbot.models import Position

DEFAULT_CONVICTION = 0.5


@dataclass(frozen=True)
class Proposal:
    symbol: str
    entry_price: float
    stop: float | None
    target: float | None
    invalidation: str
    conviction: float | None  # an add is detected from held positions, not a flag


@dataclass(frozen=True)
class RuleContext:
    nav: float
    cash: float
    positions: dict[str, Position]
    entries_today: int
    exited_today: set[str]
    day_pnl_frac: float
    atr_1h: dict[str, float | None]


def _eff_entry(p: Proposal, s: Settings) -> float:
    return buy_price(p.entry_price, s.slippage_bps)


def _cap_room(p: Proposal, ctx: RuleContext, s: Settings) -> float:
    """Shares still allowed under the per-position notional cap."""
    e = _eff_entry(p, s)
    held = ctx.positions.get(p.symbol)
    existing = held.qty if held else 0.0
    return s.max_position_frac * ctx.nav / e - existing


def size_entry(p: Proposal, ctx: RuleContext, s: Settings) -> float:
    e = _eff_entry(p, s)
    conviction = DEFAULT_CONVICTION if p.conviction is None else p.conviction
    qty = s.risk_per_trade * ctx.nav / (e - p.stop) * conviction
    qty = min(qty, _cap_room(p, ctx, s), ctx.cash / e)
    return max(floor3(qty), 0.0)


def check_entry(p: Proposal, ctx: RuleContext, s: Settings) -> str | None:
    if p.stop is None or p.target is None or not p.invalidation.strip():
        return "missing_exit_plan"
    if p.stop >= p.entry_price:
        return "stop_not_below_entry"
    if p.target <= p.entry_price:
        return "target_not_above_entry"
    e = _eff_entry(p, s)
    if e <= p.stop or (p.target - e) / (e - p.stop) < s.min_rr:
        return "rr_below_min"
    atr = ctx.atr_1h.get(p.symbol)
    if atr is not None and (p.entry_price - p.stop) < s.min_stop_atr * atr:
        return "stop_too_tight"
    adding = p.symbol in ctx.positions
    if not adding and len(ctx.positions) >= s.max_positions:
        return "max_positions"
    if ctx.entries_today >= s.max_entries_per_day:
        return "max_entries_per_day"
    if p.symbol in ctx.exited_today:
        return "cooldown"
    if ctx.day_pnl_frac <= -s.daily_loss_freeze:
        return "daily_loss_freeze"
    if _cap_room(p, ctx, s) < 0.001:
        return "position_cap_reached"
    if size_entry(p, ctx, s) <= 0:
        return "zero_size"
    return None


def check_stop_update(old: float | None, new: float | None) -> str | None:
    if old is None:
        return None
    if new is None:
        return "stop_removed"
    if new < old:
        return "stop_lowered"
    return None
