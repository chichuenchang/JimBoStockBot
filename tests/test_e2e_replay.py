from datetime import date
from pathlib import Path

from stockbot.cli import main
from stockbot.config import load_config
from stockbot.db import DB
from tests.synth import make_day

CONFIG = Path(__file__).parent / "config"
CFG = load_config(CONFIG)


def test_e2e_replay_one_day(tmp_path):
    data = tmp_path / "rec"
    make_day(data, date(2026, 10, 20))
    db_path = tmp_path / "replay.db"
    rc = main(["--config", str(CONFIG), "--db",str(db_path), "replay", "--data", str(data), "--from", "2026-10-20",
               "--to", "2026-10-20", "--fake-llm"])
    assert rc == 0
    db = DB.open(db_path)
    for arm in CFG.arms:
        assert db.equity_series(arm.name), arm.name
        n = db._q("SELECT COUNT(*) n FROM decisions WHERE arm=? AND reason='scheduled'", arm.name)[0]["n"]
        assert n == 9
    assert db.spent(None, "2026-10") > 0
    assert len(db._q("SELECT id FROM fills")) > 0
