from datetime import UTC, datetime

import pytest

from stockbot.clock import ReplayClock
from stockbot.db import DB
from stockbot.llm.base import Usage
from stockbot.llm.budget import Budget
from tests.snapshot import CFG

OCT = datetime(2026, 10, 15, 15, tzinfo=UTC)
KIMI, DS = "kimi-k3", "deepseek-v4.1-flash"


@pytest.fixture
def setup(tmp_path):
    db = DB.open(tmp_path / "t.db")
    clock = ReplayClock(OCT)
    return db, clock, Budget(db, CFG, clock)


def spend(db, model, usd, ts=OCT):
    db.insert_usage(model, None, "decision", ts, 0, 0, 0, usd)


def test_ok_warn_exhausted(setup):
    db, _, b = setup
    spend(db, KIMI, 71)
    assert b.state(KIMI) == "ok"
    spend(db, KIMI, 1)
    assert b.state(KIMI) == "warn"
    spend(db, KIMI, 8)
    assert b.state(KIMI) == "exhausted"


def test_global_cap_exhausts_all(setup):
    db, _, b = setup
    spend(db, KIMI, 79)
    spend(db, DS, 21)
    assert b.state(KIMI) == "exhausted"
    assert b.state(DS) == "exhausted"


def test_global_warn(setup):
    db, _, b = setup
    spend(db, KIMI, 70)
    spend(db, DS, 15)  # deepseek 75% own cap; total 85 < 90
    assert b.state(DS) == "ok"
    spend(db, DS, 4)   # total 89 -> still ok; deepseek 95% -> warn
    assert b.state(DS) == "warn"


def test_new_month_resets(setup):
    db, clock, b = setup
    spend(db, KIMI, 80)
    clock.set(datetime(2026, 11, 2, 15, tzinfo=UTC))
    assert b.state(KIMI) == "ok"


def test_disabled_or_zero_cap_model_exhausted(setup):
    _, _, b = setup
    assert b.state("claude") == "exhausted"


def test_record_returns_cost_and_persists(setup):
    db, _, b = setup
    cost = b.record(KIMI, "kimi-improved", "decision", Usage(25000, 20000, 4000))
    assert cost == pytest.approx(0.081)
    assert db.spent(KIMI, "2026-10") == pytest.approx(0.081)


def test_summary(setup):
    db, _, b = setup
    spend(db, KIMI, 10)
    db.insert_usage(KIMI, "kimi-improved", "decision", OCT, 1000, 600, 50, 0)
    s = b.summary()
    assert s[KIMI] == {"spent": 10, "cap": 80, "state": "ok",
                       "tokens": {"calls": 2, "input": 1000, "cached": 600, "output": 50}}
    assert s["_total"]["spent"] == 10 and s["_total"]["cap"] == 100
