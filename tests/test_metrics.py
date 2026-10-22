import random
from datetime import UTC, datetime, timedelta

import pytest

from stockbot.metrics import (aligned_daily_returns, alpha_beta, daily_returns, max_drawdown, sharpe,
                              sharpe_ci, trade_stats)
from stockbot.models import ClosedTrade

T = datetime(2026, 10, 21, 15, tzinfo=UTC)


def trade(pnl):
    return ClosedTrade("a", "X", T, T, 100, 100, 1, pnl, None, "close", "")


def test_sharpe():
    assert sharpe([0.01, -0.005, 0.02]) == pytest.approx(10.513, rel=1e-3)


def test_sharpe_none_when_flat():
    assert sharpe([0.0, 0.0]) is None
    assert sharpe([0.01]) is None


def test_max_drawdown():
    assert max_drawdown([100, 120, 90, 130, 117]) == pytest.approx(0.25)
    assert max_drawdown([]) == 0.0


def test_trade_stats():
    s = trade_stats([trade(p) for p in (100, 50, -30, -20)])
    assert (s.n, s.win_rate, s.avg_win, s.avg_loss, s.profit_factor) == (4, 0.5, 75, -25, 3.0)


def test_profit_factor_no_losses():
    assert trade_stats([trade(10)]).profit_factor is None


def test_trade_stats_empty():
    s = trade_stats([])
    assert s.n == 0 and s.win_rate is None


def test_daily_returns_uses_last_nav_per_et_day():
    series = [
        (datetime(2026, 10, 20, 14, tzinfo=UTC), 100.0),
        (datetime(2026, 10, 20, 19, tzinfo=UTC), 110.0),
        (datetime(2026, 10, 21, 3, tzinfo=UTC), 999.0),    # 23:00 ET Oct 20: last of that day
        (datetime(2026, 10, 21, 19, tzinfo=UTC), 1098.9),
    ]
    assert daily_returns(series) == [pytest.approx(0.1)]


def series(navs):
    return [(T + timedelta(days=i), v) for i, v in enumerate(navs)]


def test_alpha_beta_of_leveraged_copy():
    bench = [100, 101, 99.5, 102, 101, 103]
    rb = [b / a - 1 for a, b in zip(bench, bench[1:])]
    arm = [100]
    for r in rb:
        arm.append(arm[-1] * (1 + 2 * r + 0.001))
    ra, rb2 = aligned_daily_returns(series(arm), series(bench))
    alpha, beta = alpha_beta(ra, rb2)
    assert beta == pytest.approx(2.0)
    assert alpha == pytest.approx(0.001 * 252)


def test_aligned_returns_skip_days_missing_from_either():
    a = series([100, 110, 121])
    b = [(a[0][0], 50), (a[2][0], 50)]  # benchmark missing middle day
    ra, rb = aligned_daily_returns(a, b)
    assert ra == pytest.approx([0.21]) and rb == pytest.approx([0.0])


def test_alpha_beta_none_without_data_or_variance():
    assert alpha_beta([0.01], [0.02]) == (None, None)
    assert alpha_beta([0.01, 0.02], [0.0, 0.0]) == (None, None)


def test_sharpe_ci_brackets_point_estimate():
    rng = random.Random(1)
    daily = [rng.gauss(0.001, 0.01) for _ in range(60)]
    lo, hi = sharpe_ci(daily)
    assert lo < sharpe(daily) < hi


def test_sharpe_ci_needs_enough_days():
    assert sharpe_ci([0.01, -0.01] * 9) is None
