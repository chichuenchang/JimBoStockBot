from datetime import UTC, datetime, timedelta

import pytest

from stockbot.indicators import atr, ema, macd, rel_volume, rsi, sma, swing_levels
from stockbot.models import Bar

T0 = datetime(2026, 10, 1, 13, 30, tzinfo=UTC)


def bars_from(highs, lows=None, closes=None, vols=None):
    lows = lows or [h - 2 for h in highs]
    closes = closes or [h - 1 for h in highs]
    vols = vols or [100] * len(highs)
    return [Bar("X", T0 + timedelta(hours=i), c, h, lo, c, v)
            for i, (h, lo, c, v) in enumerate(zip(highs, lows, closes, vols))]


def test_ema_seed_is_sma():
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_short_input():
    assert ema([1, 2], 3) == [None, None]


def test_rsi_all_up_is_100():
    assert rsi(list(range(1, 20)), 14)[-1] == 100.0


def test_rsi_none_until_enough_data():
    r = rsi(list(range(1, 20)), 14)
    assert r[13] is None and r[14] is not None


def test_macd_lengths_and_sign():
    closes = [float(i) for i in range(1, 60)]
    line, signal, hist = macd(closes)
    assert len(line) == len(signal) == len(hist) == 59
    assert line[-1] > 0


def test_atr_constant_range():
    b = bars_from([100.0 + 0 * i for i in range(20)])
    assert atr(b, 14)[-1] == pytest.approx(2.0)


def test_swing_levels_finds_peak():
    highs = [1, 2, 3, 9, 3, 2, 1]
    lows = [0.5, 1.5, 2.5, 8, 2.5, 1.5, 0.5]
    sh, sl = swing_levels(bars_from(highs, lows), 3)
    assert 9 in sh


def test_swing_levels_finds_trough():
    highs = [9, 8, 7, 3, 7, 8, 9]
    lows = [8, 7, 6, 1, 6, 7, 8]
    _, sl = swing_levels(bars_from(highs, lows), 3)
    assert 1 in sl


def test_rel_volume():
    b = bars_from([10.0] * 21, vols=[100] * 20 + [250])
    assert rel_volume(b, 20) == pytest.approx(2.5)
    assert rel_volume(b[:5], 20) is None


def test_sma():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert sma([1.0], 2) == [None]
