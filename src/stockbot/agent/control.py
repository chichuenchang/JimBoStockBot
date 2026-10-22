"""Control arms: decisions without an LLM, under the same harness rules as improved arms.
improved minus rules = what the LLM adds over a plain trend system;
improved minus random = what the LLM's picks add over the harness rules alone."""
from __future__ import annotations

import random

from stockbot.agent.schema import Decision, SymbolDecision
from stockbot.models import Position

STOP_ATR = 2.0
TARGET_R = 2.0
RSI_MAX = 70.0
RANDOM_ENTRY_P = 0.10
RANDOM_EXIT_P = 0.10


def _buy(sym: str, price: float, atr: float, invalidation: str, why: str) -> SymbolDecision:
    risk = STOP_ATR * atr
    return SymbolDecision(sym, "buy_to_enter", stop_loss=price - risk, profit_target=price + TARGET_R * risk,
                          invalidation_condition=invalidation, justification=why)


def _decision(out: dict[str, SymbolDecision], universe: tuple[str, ...]) -> Decision:
    return Decision({s: out.get(s, SymbolDecision(s, "hold")) for s in universe})


def rules_decision(positions: dict[str, Position], prices: dict[str, float], feats: dict[str, dict],
                   universe: tuple[str, ...]) -> Decision:
    """Trend following: enter when the 4h trend is up, price is above EMA20 (4h) and RSI14 (1h)
    is not overbought. Exit when the 4h trend turns down; stops and targets do the rest."""
    out = {}
    for sym in universe:
        f, px = feats.get(sym) or {}, prices.get(sym)
        if sym in positions:
            if f.get("trend_4h") == "down":
                out[sym] = SymbolDecision(sym, "close_position", justification="rules: 4h trend down")
            continue
        atr, ema20, rsi = f.get("atr14_1h"), f.get("ema20_4h"), f.get("rsi14_1h")
        if (px and atr and ema20 and rsi is not None and f.get("trend_4h") == "up"
                and px > ema20 and rsi < RSI_MAX):
            out[sym] = _buy(sym, px, atr, "4h trend turns down", f"rules: 4h uptrend, RSI {rsi}")
    return _decision(out, universe)


def random_decision(rng: random.Random, positions: dict[str, Position], prices: dict[str, float],
                    feats: dict[str, dict], universe: tuple[str, ...]) -> Decision:
    out = {}
    for sym in universe:
        if sym in positions:
            if rng.random() < RANDOM_EXIT_P:
                out[sym] = SymbolDecision(sym, "close_position", justification="random exit")
            continue
        px, atr = prices.get(sym), (feats.get(sym) or {}).get("atr14_1h")
        if rng.random() < RANDOM_ENTRY_P and px and atr:
            out[sym] = _buy(sym, px, atr, "none: random control", "random entry")
    return _decision(out, universe)
