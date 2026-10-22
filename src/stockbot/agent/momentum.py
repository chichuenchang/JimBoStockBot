"""Noise-band intraday momentum control arm: Zarattini, Aziz, Barbon 2024 (SSRN 4824172) on the
benchmark, long only, cash only, at our cycle times. Why: docs/research/trading-strategy-evidence.md.

Long once price clears the day's noise band top; exit below max(band top, VWAP); flat at the
last slot of the day. No harness rules: the paper's own rules are the strategy."""
from __future__ import annotations

from datetime import datetime, timedelta

from stockbot.agent.schema import Decision, SymbolDecision
from stockbot.broker.sim import buy_price, floor3
from stockbot.clock import et_date
from stockbot.data.base import MarketData
from stockbot.models import Bar, Position, Session

NOISE_DAYS = 14
MIN_NOISE_DAYS = 5
CALENDAR_LOOKBACK = timedelta(days=30)  # covers NOISE_DAYS sessions plus holidays


def noise_sigma(prior: list[list[Bar]], minute: int) -> float | None:
    """Mean |price / day open - 1| `minute` minutes after the open, over prior sessions."""
    moves = []
    for bars in prior[-NOISE_DAYS:]:
        upto = [b for b in bars if b.ts < bars[0].ts + timedelta(minutes=minute)] if bars else []
        if upto:
            moves.append(abs(upto[-1].close / bars[0].open - 1))
    return sum(moves) / len(moves) if len(moves) >= MIN_NOISE_DAYS else None


def vwap(bars: list[Bar]) -> float | None:
    vol = sum(b.volume for b in bars)
    return sum((b.high + b.low + b.close) / 3 * b.volume for b in bars) / vol if vol else None


def band_top(day_open: float, prev_close: float, sigma: float) -> float:
    return max(day_open, prev_close) * (1 + sigma)


def _exit_level(today: list[Bar], prior: list[list[Bar]]) -> float | None:
    """max(band top, VWAP) now; None without enough history."""
    if not today or not prior or not prior[-1]:
        return None
    minute = int((today[-1].ts - today[0].ts) / timedelta(minutes=1)) + 1
    sigma = noise_sigma(prior, minute)
    if sigma is None:
        return None
    top = band_top(today[0].open, prior[-1][-1].close, sigma)
    return max(top, vwap(today) or top)


def momentum_decision(symbol: str, universe: tuple[str, ...], held: Position | None,
                      price: float | None, today: list[Bar], prior: list[list[Bar]], cash: float,
                      slippage_bps: float, last_slot: bool) -> Decision:
    level = _exit_level(today, prior)
    d = SymbolDecision(symbol, "hold")
    if held:
        if price is not None and held.exit_plan.stop_loss is not None and price <= held.exit_plan.stop_loss:
            pass  # broker's due_exits already sold it this cycle
        elif last_slot:
            d = SymbolDecision(symbol, "close_position", justification="momentum: flat at end of day")
        elif price is not None and level is not None:
            if price < level:
                d = SymbolDecision(symbol, "close_position", justification=f"momentum: below {level:.2f}")
            else:
                d = SymbolDecision(symbol, "hold", stop_loss=round(level, 4))
    elif price is not None and level is not None and price > level and not last_slot:
        qty = floor3(cash / buy_price(price, slippage_bps))
        if qty > 0:
            d = SymbolDecision(symbol, "buy_to_enter", quantity=qty, stop_loss=round(level, 4),
                               invalidation_condition="price below max(noise band top, VWAP)",
                               justification=f"momentum: {price} above noise band, exit level {level:.2f}")
    return Decision({s: d if s == symbol else SymbolDecision(s, "hold") for s in universe})


async def momentum_bars(data: MarketData, symbol: str,
                        now: datetime) -> tuple[list[Bar], list[list[Bar]], Session | None]:
    """Today's 1-min bars so far, up to NOISE_DAYS prior sessions' (oldest first), and today's
    session. ([], [], None) when today is not a session."""
    today = et_date(now)
    sessions = await data.calendar(today - CALENDAR_LOOKBACK, today)
    if not sessions or sessions[-1].day != today:
        return [], [], None
    prior_s = sessions[:-1][-NOISE_DAYS:]
    first = prior_s[0].open_utc if prior_s else sessions[-1].open_utc
    bars = await data.bars(symbol, "1Min", first, now)
    split = lambda s: [b for b in bars if s.open_utc <= b.ts < s.close_utc]  # noqa: E731
    return split(sessions[-1]), [split(s) for s in prior_s], sessions[-1]
