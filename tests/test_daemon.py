import logging
from datetime import date, timedelta
from pathlib import Path

import pytest

from stockbot.agent.cycle import AppContext
from stockbot.broker.sim import SimBroker
from stockbot.cli import fake_trader, main
from stockbot.clock import ReplayClock
from stockbot.config import load_config
from stockbot.daemon import Daemon
from stockbot.data.base import DataError
from stockbot.data.recorded import RecordedData
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.llm.fake import FakeLLM
from stockbot.models import BENCHMARK
from stockbot.pause import ALL, flagged
from tests.synth import make_day

CFG = load_config(Path(__file__).parent.parent / "config")
DAY = date(2026, 10, 20)


def build(tmp_path, data=None):
    d = tmp_path / "rec"
    session = make_day(d, DAY)
    clock = ReplayClock(session.open_utc - timedelta(minutes=30))
    db = DB.open(tmp_path / "t.db")
    llm = FakeLLM(fake_trader(seed=3))
    ctx = AppContext(CFG, db, data or RecordedData(d, clock), SimBroker(db, 5, 0), Budget(db, CFG, clock),
                     {"kimi-k3": llm, "deepseek-v4.1-flash": llm}, clock)
    return Daemon(ctx, serve_dashboard=False), session, clock, llm


async def run_day(daemon, session, clock, until=None):
    t = session.open_utc - timedelta(minutes=30)
    end = until or session.close_utc + timedelta(minutes=10)
    while t <= end:
        clock.set(t)
        await daemon.step(t)
        t += timedelta(minutes=1)


async def test_reports_and_cycles_fire_once(tmp_path):
    daemon, session, clock, _ = build(tmp_path)
    await run_day(daemon, session, clock)
    db = daemon.ctx.db
    assert len(db._q("SELECT id FROM reports")) == 2
    for arm in CFG.arms:
        n = db._q("SELECT COUNT(*) n FROM decisions WHERE arm=? AND reason='scheduled'", arm.name)[0]["n"]
        assert n == 9, arm.name


async def test_benchmark_buys_at_open(tmp_path):
    daemon, session, clock, _ = build(tmp_path)
    await run_day(daemon, session, clock, until=session.open_utc + timedelta(minutes=5))
    st = daemon.ctx.db.load_account(BENCHMARK)
    assert "QQQ" in st.positions and st.cash < 1


async def test_restart_does_not_redecide(tmp_path):
    daemon, session, clock, llm = build(tmp_path)
    await run_day(daemon, session, clock, until=session.open_utc + timedelta(minutes=20))
    calls = len(llm.calls)
    again = Daemon(daemon.ctx, serve_dashboard=False)  # fresh daemon, same DB
    await run_day(again, session, clock, until=session.open_utc + timedelta(minutes=20))
    assert len(llm.calls) == calls


async def test_late_start_runs_latest_slot_only(tmp_path):
    daemon, session, clock, _ = build(tmp_path)
    t = session.open_utc + timedelta(hours=2, minutes=5)  # 11:35 ET: slots 9:45..11:15 passed
    clock.set(t)
    await daemon.step(t)
    rows = daemon.ctx.db._q("SELECT DISTINCT slot_ts FROM decisions")
    assert [r["slot_ts"] for r in rows] == [(session.open_utc + timedelta(minutes=105)).isoformat()]


class FlakyData(RecordedData):
    fail = True

    async def latest_prices(self, symbols):
        if self.fail:
            raise DataError("HTTP 503 after 4 attempts")
        return await super().latest_prices(symbols)


async def test_step_survives_data_error(tmp_path, caplog):
    daemon, session, clock, _ = build(tmp_path)
    flaky = FlakyData(tmp_path / "rec", clock)
    daemon.ctx.data = flaky
    slot = session.open_utc + timedelta(minutes=15)
    clock.set(slot)
    with caplog.at_level(logging.ERROR):
        await daemon.step(slot)
    assert "HTTP 503" in caplog.text
    assert daemon.ctx.db.kv_get("data_ok") == "0"
    flaky.fail = False
    clock.set(slot + timedelta(minutes=1))
    await daemon.step(slot + timedelta(minutes=1))
    assert daemon.ctx.db.decision_exists("kimi-improved", slot)
    assert daemon.ctx.db.kv_get("data_ok") == "1"


def test_cli_pause_resume(tmp_path):
    db_path = tmp_path / "x.db"
    assert main(["--db", str(db_path), "pause", "kimi-improved"]) == 0
    assert flagged(DB.open(db_path), "kimi-improved")
    assert main(["--db", str(db_path), "resume", "kimi-improved"]) == 0
    assert not flagged(DB.open(db_path), "kimi-improved")
    assert main(["--db", str(db_path), "pause", "kimi-improvd"]) == 2  # typo must not look paused
    assert main(["--db", str(db_path), "pause", "--all"]) == 0
    assert flagged(DB.open(db_path), ALL)


def test_cli_status(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "x.db"), "status"]) == 0
    assert "kimi-k3" in capsys.readouterr().out


async def test_market_open_flag(tmp_path):
    daemon, session, clock, _ = build(tmp_path)
    for t, want in ((session.open_utc - timedelta(minutes=10), "0"), (session.open_utc + timedelta(minutes=1), "1")):
        clock.set(t)
        await daemon.step(t)
        assert daemon.ctx.db.kv_get("market_open") == want


def test_max_cycles(tmp_path):
    data = tmp_path / "rec"
    make_day(data, DAY)
    db_path = tmp_path / "r.db"
    assert main(["--db", str(db_path), "replay", "--data", str(data), "--from", "2026-10-20",
                 "--to", "2026-10-20", "--fake-llm", "--max-cycles", "1"]) == 0
    db = DB.open(db_path)
    for arm in CFG.arms:
        assert db._q("SELECT COUNT(*) n FROM decisions WHERE arm=?", arm.name)[0]["n"] == 1


def test_run_fails_fast_without_model_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("stockbot.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("stockbot.cli.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    assert main(["--db", str(tmp_path / "x.db"), "run"]) == 2
    assert "MOONSHOT_API_KEY" in capsys.readouterr().err


class DriftingData(RecordedData):
    """Every price call returns different numbers, like a live feed between slow LLM calls."""
    calls = 0

    async def latest_prices(self, symbols):
        self.calls += 1
        return {s: p + self.calls for s, p in (await super().latest_prices(symbols)).items()}


async def test_all_arms_see_same_prices(tmp_path):
    daemon, session, clock, _ = build(tmp_path)
    daemon.ctx.data = DriftingData(tmp_path / "rec", clock)
    slot = session.open_utc + timedelta(minutes=15)
    clock.set(slot)
    await daemon.step(slot)
    rows = daemon.ctx.db._q("SELECT prompt FROM decisions WHERE slot_ts=? AND prompt != ''", slot.isoformat())
    price_lines = {r["prompt"].split("# CURRENT PRICES OF TRADEABLE COINS\n")[1].split("\n")[0] for r in rows}
    assert len(rows) == 4 and len(price_lines) == 1


async def test_run_forever_survives_step_crash(tmp_path, caplog):
    daemon, *_ = build(tmp_path)

    class Stop(BaseException):
        pass
    calls = []

    async def step(now):
        calls.append(now)
        raise RuntimeError("database is locked") if len(calls) == 1 else Stop()
    daemon.step = step
    with caplog.at_level(logging.ERROR), pytest.raises(Stop):
        await daemon.run_forever(poll_s=0)
    assert len(calls) == 2 and "database is locked" in caplog.text


class OneEarnings:
    async def snapshot(self, symbols, today):
        return {"MSFT": {"next_earnings": "2026-11-30"}}


async def test_reports_use_fundamentals_source(tmp_path):
    daemon, session, clock, _ = build(tmp_path)
    daemon.ctx.fundamentals = OneEarnings()
    await run_day(daemon, session, clock, until=session.open_utc)
    assert daemon.ctx.db.latest_report_features()["MSFT"]["next_earnings"] == "2026-11-30"


def test_fundamentals_source_from_env(monkeypatch):
    from stockbot.cli import _fundamentals
    from stockbot.data.fundamentals import AlphaVantage
    monkeypatch.setattr("stockbot.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert _fundamentals() is None
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "k")
    assert isinstance(_fundamentals(), AlphaVantage)
