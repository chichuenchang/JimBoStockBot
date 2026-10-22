"""System prompts and per-arm state rendering.

Prompt order is system > report > state, so the shared report forms a stable prefix
that providers can cache across arms within one cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from stockbot.clock import ET
from stockbot.config import Settings
from stockbot.models import AccountState, ClosedTrade

Variant = Literal["baseline", "improved"]

_COMMON = """You are an autonomous swing trader managing a paper account of US stocks.
Tradeable symbols: {universe}. Long only, cash only: no shorting, no leverage.
You are called a few times per trading day. Each call you receive a shared market report,
then your own account state. Decide for every symbol you hold or want to hold.

Reply with ONE JSON object keyed by symbol. Each value:
  "signal": "hold" | "buy_to_enter" | "close_position"
  "stop_loss": price, "profit_target": price, "invalidation_condition": text,
  "is_add": true when buying more of a symbol you already hold,
  "justification": one or two sentences,
Symbols you omit are treated as "hold". "close_position" sells the whole position.
"""

_BASELINE = """  "quantity": number of shares to buy (buy_to_enter only).
Size positions yourself; orders larger than your available cash are cut to fit.
Your stop_loss and profit_target are checked each time you are called."""

_IMPROVED = """  "conviction": 0 to 1, how strong the setup is (buy_to_enter only).
The harness sizes every entry from your stop distance and conviction; do not send quantity.
Every buy_to_enter needs stop_loss below price, profit_target above price, and an
invalidation_condition. Entries that break the harness rules are rejected and logged.
Your stop_loss and profit_target are enforced every minute. A stop may only be raised.
You may add a top-level "notes_to_self" string (max 500 chars); it is shown to you next call."""


def system_prompt(variant: Variant, s: Settings) -> str:
    extra = _BASELINE if variant == "baseline" else _IMPROVED
    head = _COMMON.format(universe=", ".join(s.universe))
    return head + extra


@dataclass(frozen=True)
class JournalView:
    summaries: list[str]
    closed: list[ClosedTrade]
    notes: str | None
    lessons: list[str] = field(default_factory=list)  # one per closed trade, see agent/reflect.py


def _et(now: datetime) -> str:
    t = now.astimezone(ET)
    return t.strftime("%A, %B %d, %Y ") + t.strftime("%I:%M %p").lower() + " ET"


def _rules(s: Settings) -> str:
    return "\n".join([
        f"- Risk per trade: {s.risk_per_trade:.0%} of NAV x conviction, sized from stop distance",
        f"- Max position size: {s.max_position_frac:.0%} of NAV",
        f"- Max open positions: {s.max_positions}; max new entries per day: {s.max_entries_per_day}",
        f"- Reward:risk after slippage must be at least {s.min_rr}",
        f"- Stop distance must be at least {s.min_stop_atr} x ATR14 (1h)",
        "- No re-entry in a symbol on the day you exited it",
        f"- New entries frozen for the day once the account is down {s.daily_loss_freeze:.0%} today",
        *([f"- No new entries while {s.benchmark_symbol} is below its 50-day average (see Market Regime)"]
          if s.regime_gate else []),
        *([f"- No entries in a symbol from {s.earnings_blackout_days} session(s) before its earnings through earnings day"]
          if s.earnings_blackout_days >= 0 else []),
        *([f"- Stops trail automatically: to entry once price gains {s.trail_breakeven_r:g}R"]
          if s.trail_breakeven_r else []),
        *([f"- Stops trail automatically: kept at least {s.trail_atr_mult:g} x ATR14 (1h) under the high since entry"]
          if s.trail_atr_mult else []),
    ])


def render_state(variant: Variant, state: AccountState, prices: dict[str, float], nav: float,
                 now: datetime, journal: JournalView | None, s: Settings) -> str:
    lines = [f"# CURRENT TIME\nIt is {_et(now)}.", "",
             f"# CURRENT AVAILABLE CAPITAL\n{state.cash:.2f}", "",
             f"# CURRENT NAV\n{nav:.2f}", "",
             f"# CURRENT PRICES OF TRADEABLE COINS\n{ {k: prices[k] for k in s.universe if k in prices} }",
             "", "# CURRENT LIVE POSITIONS AND PERFORMANCE", ""]
    if not state.positions:
        lines.append("(none)")
    for p in state.positions.values():
        px = prices.get(p.symbol, p.entry_price)
        lines.append(repr({
            "symbol": p.symbol, "quantity": p.qty, "entry_price": round(p.entry_price, 4),
            "current_price": px, "unrealized_pnl": round((px - p.entry_price) * p.qty, 2),
            "exit_plan": {"profit_target": p.exit_plan.profit_target,
                          "stop_loss": p.exit_plan.stop_loss,
                          "invalidation_condition": p.exit_plan.invalidation},
            "notional_usd": round(px * p.qty, 2)}))
    if variant == "improved":
        lines += ["", "# YOUR JOURNAL", "## Recent decisions"]
        j = journal or JournalView([], [], None)
        lines += [f"- {x}" for x in j.summaries] or ["(none)"]
        lines.append("## Recent closed trades")
        lines += [f"- {t.symbol}: {t.entry_price:.2f} -> {t.exit_price:.2f}, P&L {t.pnl:+.2f}, "
                  f"exit {t.exit_reason}" + (f", R {t.r_multiple:+.2f}" if t.r_multiple is not None else "")
                  for t in j.closed] or ["(none)"]
        lines.append("## Lessons from closed trades")
        lines += [f"- {x}" for x in j.lessons] or ["(none)"]
        lines += ["## Notes to self", j.notes or "(none)", "", "# HARNESS RULES", _rules(s)]
    return "\n".join(lines)


def build_user_prompt(report_text: str, state_text: str) -> str:
    return f"{report_text}\n\n{state_text}"
