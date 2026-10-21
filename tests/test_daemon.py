import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stockbot.agent.cycle import AppContext
from stockbot.broker.sim import SimBroker
from stockbot.cli import fake_trader, main
from stockbot.clock import ReplayClock
from stockbot.config import load_config
from stockbot.daemon import BENCHMARK, Daemon
from stockbot.data.base import DataError
from stockbot.data.recorded import RecordedData
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.llm.fake import FakeLLM
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
    assert DB.open(db_path).kv_get("pause:kimi-improved") == "1"
    assert main(["--db", str(db_path), "resume", "kimi-improved"]) == 0
    assert DB.open(db_path).kv_get("pause:kimi-improved") is None
    assert main(["--db", str(db_path), "pause", "--all"]) == 0
    assert DB.open(db_path).kv_get("pause:_all") == "1"


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
