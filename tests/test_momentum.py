from datetime import UTC, date, datetime, timedelta

import pytest

from stockbot.agent.momentum import band_top, momentum_bars, momentum_decision, noise_sigma, vwap
from stockbot.models import Bar, ExitPlan, Position, Session

OPEN = datetime(2026, 10, 21, 13, 30, tzinfo=UTC)  # 09:30 ET
UNIVERSE = ("QQQ", "MSFT")


def day(open_ts, closes, o=100.0, v=1000):
    """One session of 1-min bars: first opens at o, then one bar per close."""
    return [Bar("QQQ", open_ts + timedelta(minutes=i), o if i == 0 else closes[i - 1], c, c, c, v)
            for i, c in enumerate(closes)]


def prior_days(moves, n_bars=60):
    """Sessions before OPEN whose close after every minute sits `move` above the 100 open."""
    return [day(OPEN - timedelta(days=len(moves) - i), [100 * (1 + m)] * n_bars) for i, m in enumerate(moves)]


def test_noise_sigma_mean_abs_move_at_minute():
    prior = prior_days([0.01, -0.03, 0.02, 0.01, -0.01])
    assert noise_sigma(prior, 15) == pytest.approx(0.016)


def test_noise_sigma_needs_enough_days():
    assert noise_sigma(prior_days([0.01] * 4), 15) is None


def test_vwap_volume_weighted():
    bars = [Bar("QQQ", OPEN, 100, 101, 99, 100, 100), Bar("QQQ", OPEN, 100, 104, 102, 103, 300)]
    assert vwap(bars) == pytest.approx((100 * 100 + 103 * 300) / 400)
    assert vwap([]) is None


def test_band_top_uses_higher_of_open_and_prev_close():
    assert band_top(100, 102, 0.01) == pytest.approx(103.02)
    assert band_top(102, 100, 0.01) == pytest.approx(103.02)


def decide(price, today, held=None, last_slot=False, cash=10000.0, prior=None):
    prior = prior_days([0.01] * 14) if prior is None else prior
    return momentum_decision("QQQ", UNIVERSE, held, price, today, prior, cash, 5, last_slot).per_symbol


def test_enters_above_band_with_all_cash():
    today = day(OPEN, [100.5, 101.0, 101.6])  # prev close 101, band top 101 x 1.01 = 102.01
    d = decide(102.5, today, cash=10250)["QQQ"]
    assert d.signal == "buy_to_enter"
    assert d.quantity == pytest.approx(99.95, abs=0.01)  # cash / price after slippage, floored
    assert d.stop_loss == pytest.approx(102.01)  # max(band top, vwap)
    assert decide(102.5, today)["MSFT"].signal == "hold"


def test_no_entry_inside_band_or_on_last_slot():
    today = day(OPEN, [100.5, 101.0, 101.6])
    assert decide(101.9, today)["QQQ"].signal == "hold"
    assert decide(102.5, today, last_slot=True)["QQQ"].signal == "hold"


def test_no_entry_without_history():
    today = day(OPEN, [100.5, 101.0, 101.6])
    assert decide(105, today, prior=prior_days([0.01] * 3))["QQQ"].signal == "hold"


def pos(stop):
    return Position("QQQ", 10, 102.5, OPEN, ExitPlan(stop, None, "x"), 0.5)


def test_held_trails_stop_to_band_or_vwap():
    today = day(OPEN, [101.0, 103.0, 104.0, 105.0], v=1000)
    d = decide(105, today, held=pos(102.0))["QQQ"]
    assert d.signal == "hold" and d.stop_loss == pytest.approx(vwap(today))


def test_held_closes_below_level():
    today = day(OPEN, [101.0, 103.0, 104.0, 105.0])
    assert decide(103.0, today, held=pos(102.0))["QQQ"].signal == "close_position"


def test_held_below_stored_stop_left_to_broker():
    # due_exits already sold it this cycle; a second close would only be cancelled
    today = day(OPEN, [101.0, 103.0, 104.0, 105.0])
    d = decide(101.5, today, held=pos(102.0))["QQQ"]
    assert d.signal == "hold" and d.stop_loss is None


def test_flat_at_last_slot():
    today = day(OPEN, [101.0, 103.0, 104.0, 105.0])
    assert decide(110, today, held=pos(102.0), last_slot=True)["QQQ"].signal == "close_position"


class Data:
    def __init__(self, sessions, bars):
        self.sessions, self._bars = sessions, bars

    async def calendar(self, start, end):
        return [s for s in self.sessions if start <= s.day <= end]

    async def bars(self, symbol, timeframe, start, end):
        assert timeframe == "1Min"
        return [b for b in self._bars if start <= b.ts < end]


async def test_momentum_bars_split_by_session():
    sessions = [Session(date(2026, 10, d), datetime(2026, 10, d, 13, 30, tzinfo=UTC),
                        datetime(2026, 10, d, 20, tzinfo=UTC)) for d in (16, 19, 20, 21)]
    bars = [b for s in sessions for b in day(s.open_utc, [100.0] * 5)]
    now = sessions[-1].open_utc + timedelta(minutes=3)
    today, prior, session = await momentum_bars(Data(sessions, bars), "QQQ", now)
    assert len(today) == 3 and today[0].ts == sessions[-1].open_utc
    assert [p[0].ts for p in prior] == [s.open_utc for s in sessions[:-1]]
    assert session == sessions[-1]


async def test_momentum_bars_empty_when_market_closed():
    assert await momentum_bars(Data([], []), "QQQ", OPEN) == ([], [], None)
