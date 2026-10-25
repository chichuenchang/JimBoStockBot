"""Parse an LLM decision reply (AA per-symbol JSON shape) tolerantly."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

SIGNALS = ("hold", "buy_to_enter", "close_position")
NOTES_MAX = 500


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class SymbolDecision:
    symbol: str
    signal: Literal["hold", "buy_to_enter", "close_position"]
    quantity: float | None = None
    conviction: float | None = None
    stop_loss: float | None = None
    profit_target: float | None = None
    invalidation_condition: str = ""
    is_add: bool = False
    justification: str = ""


@dataclass(frozen=True)
class Decision:
    per_symbol: dict[str, SymbolDecision]
    notes_to_self: str | None = None
    ignored_keys: list[str] = field(default_factory=list)


def first_json_object(text: str) -> dict:
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ParseError("no JSON object found in reply")


def _num(v, name: str, sym: str) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ParseError(f"{sym}.{name}: not a number")
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[\s$,]", "", str(v))
    try:
        return float(s)
    except ValueError:
        raise ParseError(f"{sym}.{name}: not a number: {v!r}") from None


def _price(v, name: str, sym: str) -> float | None:
    x = _num(v, name, sym)
    return None if x is None or x == 0 else x


def _strip(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def _entries(obj: dict) -> tuple[dict[str, dict], list[str], str | None]:
    notes = obj.get("notes_to_self")
    if isinstance(obj.get("decisions"), list):
        items = {_strip(str(d.get("coin") or d.get("symbol") or "")): d
                 for d in obj["decisions"] if isinstance(d, dict)}
        return items, [], notes
    items, other = {}, []
    for k, v in obj.items():
        if k == "notes_to_self":
            continue
        if isinstance(v, dict):
            items[_strip(k)] = v
        else:
            other.append(k)
    return items, other, notes


def parse_decision(text: str, variant: Literal["baseline", "improved"],
                   universe: tuple[str, ...]) -> Decision:
    obj = first_json_object(text)
    items, ignored, notes = _entries(obj)
    out: dict[str, SymbolDecision] = {}
    for sym, d in items.items():
        if sym not in universe:
            ignored.append(sym)
            continue
        signal = str(d.get("signal", "hold")).strip().lower()
        if signal not in SIGNALS:
            raise ParseError(f"{sym}: unknown signal {signal!r}; use one of {SIGNALS}")
        conviction = quantity = None
        if variant == "improved":
            c = _num(d.get("conviction"), "conviction", sym)
            conviction = None if c is None else min(max(c, 0.0), 1.0)
        else:
            quantity = _num(d.get("quantity"), "quantity", sym)
        out[sym] = SymbolDecision(
            symbol=sym, signal=signal, quantity=quantity, conviction=conviction,
            stop_loss=_price(d.get("stop_loss"), "stop_loss", sym),
            profit_target=_price(d.get("profit_target"), "profit_target", sym),
            invalidation_condition=str(d.get("invalidation_condition") or ""),
            is_add=str(d.get("is_add")).strip().lower() == "true",  # bool("false") is True
            justification=str(d.get("justification") or ""))
    for sym in universe:
        out.setdefault(sym, SymbolDecision(sym, "hold"))
    if notes is not None:
        notes = str(notes)[:NOTES_MAX]
    return Decision(out, notes, ignored)
