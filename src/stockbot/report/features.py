"""Deterministic per-ticker features. The only source of numbers in the market report."""
from __future__ import annotations

from stockbot.indicators import atr, ema, macd, rel_volume, rsi, sma, swing_levels
from stockbot.models import Bar


def _last(xs):
    return next((x for x in reversed(xs) if x is not None), None)


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def ticker_features(symbol: str, bars_1h: list[Bar], bars_4h: list[Bar],
                    bars_1d: list[Bar]) -> dict:
    closes_1h = [b.close for b in bars_1h]
    closes_4h = [b.close for b in bars_4h]
    last = closes_1h[-1] if closes_1h else (closes_4h[-1] if closes_4h else None)
    e20, e50 = _last(ema(closes_4h, 20)), _last(ema(closes_4h, 50))
    if last is None or e20 is None or e50 is None:
        trend = "unknown"
    elif last > e20 > e50:
        trend = "up"
    elif last < e20 < e50:
        trend = "down"
    else:
        trend = "sideways"
    prev_close = bars_1d[-1].close if bars_1d else None  # finished sessions only: last = previous close
    chg = (last / prev_close - 1) * 100 if last is not None and prev_close else None
    highs, lows = swing_levels(bars_4h, 3)
    return {
        "last": _r(last, 4),
        "chg_1d_pct": _r(chg),
        "ema20_4h": _r(e20),
        "ema50_4h": _r(e50),
        "rsi14_1h": _r(_last(rsi(closes_1h, 14)), 1),
        "macd_hist_1h": _r(_last(macd(closes_1h)[2]), 3),
        "atr14_1h": _r(_last(atr(bars_1h, 14)), 3),
        "swing_highs": [_r(x) for x in highs],
        "swing_lows": [_r(x) for x in lows],
        "rel_volume": _r(rel_volume(bars_1d, 20)),
        "trend_4h": trend,
        "sma50_1d": _r(_last(sma([b.close for b in bars_1d], 50))),
    }


def regime(price: float | None, sma50: float | None) -> str | None:
    """Benchmark above its 50-day average = risk_on. None when either is unknown."""
    if price is None or sma50 is None:
        return None
    return "risk_on" if price >= sma50 else "risk_off"


def regime_line(features: dict[str, dict], benchmark: str) -> str:
    f = features.get(benchmark) or {}
    r = regime(f.get("last"), f.get("sma50_1d"))
    if r is None:
        return f"{benchmark} vs 50-day average: unknown (not enough history)"
    return f"{benchmark} {f['last']} vs 50-day average {f['sma50_1d']}: {r.upper().replace('_', '-')}"


COLUMNS = ["last", "chg_1d_pct", "trend_4h", "ema20_4h", "ema50_4h", "rsi14_1h", "macd_hist_1h",
           "atr14_1h", "swing_highs", "swing_lows", "rel_volume", "sma50_1d"]


FUNDAMENTAL_COLUMNS = ["next_earnings", "pe_ttm", "fwd_pe", "rev_growth_yoy_pct", "eps_growth_yoy_pct"]


def render_computed_table(features: dict[str, dict], columns: list[str] = COLUMNS) -> str:
    def cell(v):
        if v is None:
            return "n/a"
        if isinstance(v, list):
            return ", ".join(str(x) for x in v) or "-"
        return str(v)
    head = "| symbol | " + " | ".join(columns) + " |"
    sep = "|" + "---|" * (len(columns) + 1)
    rows = [f"| {s} | " + " | ".join(cell(f.get(c)) for c in columns) + " |"
            for s, f in features.items()]
    return "\n".join([head, sep, *rows])
