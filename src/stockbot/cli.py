"""Command line: run | record | replay | pause | resume | status."""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import logging
import os
import random
import re
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from stockbot.agent.cycle import AppContext
from stockbot.broker.sim import SimBroker
from stockbot.clock import RealClock, ReplayClock
from stockbot.config import AppConfig, ConfigError, load_config
from stockbot.daemon import Daemon
from stockbot.db import DB
from stockbot.llm.base import make_client
from stockbot.llm.budget import Budget
from stockbot.llm.fake import FakeLLM

log = logging.getLogger("stockbot")
REPLAY_LEAD = timedelta(minutes=30)
REPLAY_TAIL = timedelta(minutes=10)


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(log_dir / "stockbot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger("stockbot")
    root.setLevel(logging.INFO)
    root.handlers = [fh, sh]


def fake_trader(seed: int = 0):
    """Random but valid decisions, for $0 replay runs."""
    rng = random.Random(seed)

    def reply(system: str, user: str) -> str:
        if "Reply with ONE JSON object" not in system:
            return "## 0. Context Snapshot\nSynthetic analyst text (fake LLM)."
        m = re.search(r"# CURRENT PRICES OF TRADEABLE COINS\n(\{.*?\})", user)
        prices = ast.literal_eval(m.group(1)) if m else {}
        held = set(re.findall(r"'symbol': '(\w+)'", user))
        out = {}
        for sym, px in prices.items():
            r = rng.random()
            if sym in held and r < 0.15:
                out[sym] = {"signal": "close_position", "justification": "fake exit"}
            elif sym not in held and r < 0.10:
                out[sym] = {"signal": "buy_to_enter", "quantity": 5, "conviction": 0.5,
                            "stop_loss": round(px * 0.97, 2), "profit_target": round(px * 1.06, 2),
                            "invalidation_condition": "fake: close below stop",
                            "justification": "fake entry"}
        return json.dumps(out)
    return reply


def _clients(cfg: AppConfig, fake: bool, seed: int = 0) -> dict:
    if fake:
        llm = FakeLLM(fake_trader(seed))
        return {name: llm for name, m in cfg.models.items() if m.enabled}
    return {name: make_client(m, cfg.settings.llm_timeout_s)
            for name, m in cfg.models.items() if m.enabled}


def _alpaca():
    from stockbot.data.alpaca import AlpacaData
    load_dotenv()
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise ConfigError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set (see .env.example)")
    return AlpacaData(key, secret)


def _ctx(cfg, db, data, clock, clients) -> AppContext:
    s = cfg.settings
    return AppContext(cfg, db, data, SimBroker(db, s.slippage_bps, s.commission_per_order),
                      Budget(db, cfg, clock), clients, clock)


async def _replay(cfg: AppConfig, db: DB, data_dir: Path, d0: date, d1: date, fake: bool,
                  max_cycles: int | None = None) -> int:
    from stockbot.data.recorded import RecordedData
    clock = ReplayClock(datetime.now(UTC))
    data = RecordedData(data_dir, clock)
    sessions = await data.calendar(d0, d1)
    if not sessions:
        log.error("no sessions in %s for %s..%s", data_dir, d0, d1)
        return 1
    clock.set(sessions[0].open_utc - REPLAY_LEAD)
    daemon = Daemon(_ctx(cfg, db, data, clock, _clients(cfg, fake)), serve_dashboard=False)
    for s in sessions:
        t = s.open_utc - REPLAY_LEAD
        while t <= s.close_utc + REPLAY_TAIL:
            clock.set(t)
            await daemon.step(t)
            if max_cycles is not None and daemon.cycles_run >= max_cycles:
                log.info("stopped after %d cycle(s)", daemon.cycles_run)
                return 0
            t += timedelta(minutes=1)
        log.info("replayed %s", s.day)
    return 0


async def _record(cfg: AppConfig, days: int, out: Path) -> int:
    from stockbot.data.recorded import write_bars, write_calendar, write_news
    data = _alpaca()
    now = datetime.now(UTC)
    today = now.date()
    sessions = [s for s in await data.calendar(today - timedelta(days=days * 2), today)
                if s.close_utc <= now][-days:]
    if not sessions:
        return 1
    start = sessions[0].open_utc
    history = {"1Min": timedelta(0), "1Hour": timedelta(days=15), "4Hour": timedelta(days=45),
               "1Day": timedelta(days=45)}
    for sym in cfg.settings.universe:
        for tf, back in history.items():
            write_bars(out, sym, tf, await data.bars(sym, tf, start - back, now))
    write_news(out, await data.news(list(cfg.settings.universe), start - timedelta(days=3)))
    write_calendar(out, sessions)
    print(f"recorded {len(sessions)} sessions to {out}")
    return 0


def _status(cfg: AppConfig, db: DB) -> int:
    budget = Budget(db, cfg, RealClock())
    for name, row in budget.summary().items():
        print(f"{name:24} ${row['spent']:.2f} / ${row['cap']:.2f}  {row['state']}")
    for arm in cfg.arms:
        last = db.last_decision_ts(arm.name)
        try:
            n = len(db.load_account(arm.name).positions)
        except KeyError:
            n = 0
        paused = " PAUSED" if db.kv_get(f"pause:{arm.name}") or db.kv_get("pause:_all") else ""
        print(f"{arm.name:24} last decision {last or '-'}  positions {n}{paused}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stockbot")
    p.add_argument("--config", default="config")
    p.add_argument("--db", help="override settings.db_path")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    r = sub.add_parser("record")
    r.add_argument("--days", type=int, default=30)
    r.add_argument("--out", required=True)
    rp = sub.add_parser("replay")
    rp.add_argument("--data", required=True)
    rp.add_argument("--from", dest="d0", required=True)
    rp.add_argument("--to", dest="d1", required=True)
    rp.add_argument("--fake-llm", action="store_true")
    rp.add_argument("--serve", action="store_true", help="keep dashboard up after replay")
    rp.add_argument("--max-cycles", type=int, help="stop after N decision cycles (cheap real-model smoke test)")
    for name in ("pause", "resume"):
        sp = sub.add_parser(name)
        sp.add_argument("arm", nargs="?")
        sp.add_argument("--all", action="store_true")
    sub.add_parser("status")
    a = p.parse_args(argv)

    try:
        cfg = load_config(Path(a.config))
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if a.cmd == "replay" and not a.db:
        db_path = Path(tempfile.mkdtemp(prefix="stockbot-replay-")) / "replay.db"
    else:
        db_path = Path(a.db) if a.db else cfg.settings.db_path
    db = DB.open(db_path)

    if a.cmd in ("pause", "resume"):
        if not a.all and not a.arm:
            print("name an arm or pass --all", file=sys.stderr)
            return 2
        key = "pause:_all" if a.all else f"pause:{a.arm}"
        db.kv_set(key, "1") if a.cmd == "pause" else db.kv_delete(key)
        return 0
    if a.cmd == "status":
        return _status(cfg, db)

    setup_logging(Path("logs"))
    try:
        if a.cmd == "record":
            return asyncio.run(_record(cfg, a.days, Path(a.out)))
        if a.cmd == "replay":
            rc = asyncio.run(_replay(cfg, db, Path(a.data), date.fromisoformat(a.d0),
                                     date.fromisoformat(a.d1), a.fake_llm, a.max_cycles))
            print(f"replay db: {db_path}")
            if rc == 0 and a.serve:
                from stockbot.dashboard.app import serve_forever
                from stockbot.data.recorded import RecordedData
                serve_forever(db, cfg, RecordedData(Path(a.data), ReplayClock(datetime.now(UTC))))
            return rc
        clock = RealClock()
        ctx = _ctx(cfg, db, _alpaca(), clock, _clients(cfg, fake=False))
        asyncio.run(Daemon(ctx).run_forever())
        return 0
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
