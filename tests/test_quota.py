import dataclasses
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from stockbot import daemon as daemon_mod
from stockbot.db import DB
from stockbot.llm import quota
from tests.snapshot import CFG
from tests.test_daemon import build, run_day

URL = "https://api.kimi.com/coding/v1/usages"
KIMI = dataclasses.replace(CFG.models["kimi-k3"], quota_url=URL)
# trimmed live reply, 2026-10-27
BODY = {"usage": {"limit": "100", "remaining": "100"},
        "usages": {"limit_5h": {"used_ratio": 0.0004, "reset_time": "2026-10-28T00:45:53Z"},
                   "limit_7d": {"used_ratio": 0.0001, "reset_time": "2026-10-31T18:45:54Z"}}}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")


def test_parse_kimi_usages():
    assert quota.parse(BODY) == [
        quota.Window("5h", 0.0004, datetime(2026, 10, 28, 0, 45, 53, tzinfo=UTC)),
        quota.Window("7d", 0.0001, datetime(2026, 10, 31, 18, 45, 54, tzinfo=UTC))]


@pytest.mark.parametrize("bad", [{}, {"usages": {"limit_5h": {}}}, {"usages": []}])
def test_parse_rejects_unknown_shape(bad):
    with pytest.raises(quota.QuotaError):
        quota.parse(bad)


@respx.mock
async def test_fetch_sends_bearer():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=BODY))
    assert [w.name for w in await quota.fetch(KIMI)] == ["5h", "7d"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer k"


@respx.mock
async def test_fetch_http_error():
    respx.get(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(quota.QuotaError, match="401"):
        await quota.fetch(KIMI)


async def test_fetch_missing_key():
    with pytest.raises(quota.QuotaError, match="no api_key_env"):
        await quota.fetch(dataclasses.replace(KIMI, api_key_env=None))


def test_db_latest_quota(tmp_path):
    db = DB.open(tmp_path / "t.db")
    t = datetime(2026, 10, 27, 15, tzinfo=UTC)
    db.insert_quota("kimi", t, quota.parse(BODY))
    db.insert_quota("kimi", t + timedelta(minutes=10), [quota.Window("5h", 0.5, None)])
    assert db.latest_quota("kimi") == [
        {"ts": (t + timedelta(minutes=10)).isoformat(), "window": "5h", "used_ratio": 0.5, "reset_ts": None}]
    assert db.latest_quota("other") == []


async def test_daemon_polls_every_ten_minutes(tmp_path, monkeypatch, caplog):
    daemon, session, clock, _ = build(tmp_path)
    daemon.poll_quota = True
    daemon.ctx.cfg = dataclasses.replace(CFG, models={**CFG.models, "kimi-k3": KIMI})
    polls = []

    async def fake(cfg):
        polls.append(cfg.name)
        return [quota.Window("5h", 0.9, None)]
    monkeypatch.setattr(daemon_mod.quota, "fetch", fake)
    with caplog.at_level(logging.WARNING, logger="stockbot"):
        await run_day(daemon, session, clock, until=session.open_utc - timedelta(minutes=5))  # 26 passes
    assert polls == ["kimi-k3"] * 3
    assert len(daemon.ctx.db._q("SELECT id FROM llm_quota")) == 3
    assert "kimi-k3 quota 5h window 90% used" in caplog.text


async def test_daemon_quota_off_by_default(tmp_path, monkeypatch):
    daemon, session, clock, _ = build(tmp_path)
    daemon.ctx.cfg = dataclasses.replace(CFG, models={**CFG.models, "kimi-k3": KIMI})

    async def boom(cfg):
        raise AssertionError("replay polled quota")
    monkeypatch.setattr(daemon_mod.quota, "fetch", boom)
    await run_day(daemon, session, clock, until=session.open_utc - timedelta(minutes=20))


async def test_daemon_quota_error_logged(tmp_path, monkeypatch, caplog):
    daemon, _, clock, _ = build(tmp_path)
    daemon.poll_quota = True
    daemon.ctx.cfg = dataclasses.replace(CFG, models={**CFG.models, "kimi-k3": KIMI})

    async def fail(cfg):
        raise quota.QuotaError("HTTP 500")
    monkeypatch.setattr(daemon_mod.quota, "fetch", fail)
    await daemon.step(clock.now())
    assert "quota kimi-k3: HTTP 500" in caplog.text
