import dataclasses
from datetime import date

from stockbot import cli as cli_mod
from stockbot.daemon import Daemon
from stockbot.db import DB
from stockbot.search.anysearch import AnySearch, NoSearch
from tests.snapshot import CFG, cli
from tests.synth import make_day


def test_e2e_replay_one_day(tmp_path):
    data = tmp_path / "rec"
    make_day(data, date(2026, 10, 20))
    db_path = tmp_path / "replay.db"
    rc = cli(db_path, "replay", "--data", str(data), "--from", "2026-10-20",
             "--to", "2026-10-20", "--fake-llm")
    assert rc == 0
    db = DB.open(db_path)
    for arm in CFG.arms:
        assert db.equity_series(arm.name), arm.name
        n = db._q("SELECT COUNT(*) n FROM decisions WHERE arm=? AND reason='scheduled'", arm.name)[0]["n"]
        assert n == 9
    assert db.spent(None, "2026-10") > 0
    assert len(db._q("SELECT id FROM fills")) > 0


def test_replay_uses_no_search(tmp_path, monkeypatch):
    seen = []

    class Spy(Daemon):
        def __init__(self, ctx, **kw):
            seen.append(ctx)
            super().__init__(ctx, **kw)

    monkeypatch.setattr(cli_mod, "Daemon", Spy)
    data = tmp_path / "rec"
    make_day(data, date(2026, 10, 20))
    assert cli(tmp_path / "r.db", "replay", "--data", str(data), "--from", "2026-10-20",
               "--to", "2026-10-20", "--fake-llm", "--max-cycles", "1") == 0
    assert isinstance(seen[0].search, NoSearch)


def test_live_search_client(monkeypatch, caplog):
    monkeypatch.setattr(cli_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "sk")
    s = cli_mod._search(CFG)
    assert isinstance(s, AnySearch) and s.api_key == "sk"
    monkeypatch.delenv("ANYSEARCH_API_KEY")
    assert cli_mod._search(CFG).api_key is None
    assert "ANYSEARCH_API_KEY not set" in caplog.text
    no_search = dataclasses.replace(CFG, arms=tuple(dataclasses.replace(a, search=False) for a in CFG.arms))
    assert cli_mod._search(no_search) is None
