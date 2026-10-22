"""Technical indicators on plain lists. Output aligned to input; None until enough data."""
from __future__ import annotations

from stockbot.models import Bar


def ema(xs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    if len(xs) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(xs[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(xs)):
        prev = xs[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder(xs: list[float], n: int, start: int) -> list[float | None]:
    """Wilder smoothing of xs, seeded with the mean of xs[start:start+n]."""
    out: list[float | None] = [None] * len(xs)
    if len(xs) < start + n:
        return out
    prev = sum(xs[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(xs)):
        prev = (prev * (n - 1) + xs[i]) / n
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    if len(closes) < 2:
        return [None] * len(closes)
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = _wilder(gains, n, 1), _wilder(losses, n, 1)
    out: list[float | None] = []
    for g, l in zip(ag, al):
        if g is None:
            out.append(None)
        elif l == 0:
            out.append(100.0 if g > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + g / l))
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [f - s if f is not None and s is not None else None for f, s in zip(ef, es)]
    first = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_tail = ema([v for v in line[first:]], signal)
    sig = [None] * first + sig_tail
    hist = [l - s if l is not None and s is not None else None for l, s in zip(line, sig)]
    return line, sig, hist


def atr(bars: list[Bar], n: int = 14) -> list[float | None]:
    if not bars:
        return []
    tr = [bars[0].high - bars[0].low]
    for prev, b in zip(bars, bars[1:]):
        tr.append(max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close)))
    return _wilder(tr, n, 0)


def swing_levels(bars: list[Bar], lookback: int = 3) -> tuple[list[float], list[float]]:
    """Pivot highs/lows: bar extreme beyond `lookback` bars each side. Newest first, max 3 each."""
    highs, lows = [], []
    for i in range(lookback, len(bars) - lookback):
        others = bars[i - lookback:i] + bars[i + 1:i + lookback + 1]
        b = bars[i]
        if b.high > max(x.high for x in others):
            highs.append(b.high)
        if b.low < min(x.low for x in others):
            lows.append(b.low)
    return highs[::-1][:3], lows[::-1][:3]


def rel_volume(day_bars: list[Bar], n: int = 20) -> float | None:
    """Last bar volume / mean of the previous n bars."""
    if len(day_bars) < n + 1:
        return None
    avg = sum(b.volume for b in day_bars[-n - 1:-1]) / n
    return day_bars[-1].volume / avg if avg else None
