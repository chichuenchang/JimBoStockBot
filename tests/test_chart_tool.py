from datetime import UTC, date, datetime

import pytest

from stockbot.agent import chart_tool
from stockbot.agent.schema import ParseError
from stockbot.charts import ChartSpec
from stockbot.db import DB
from tests.snapshot import CFG
from tests.test_history import day_bar, days

NOW = datetime(2026, 1, 30, 15, tzinfo=UTC)  # Fri 10:00 ET, session open


def test_request_list_and_single():
    one = {"symbol": "MSFT", "tf": "1Week", "start": "2020-01-01"}
    assert chart_tool.request('{"chart": [%s]}' % str(one).replace("'", '"')) == [one]
    assert chart_tool.request('{"chart": %s}' % str(one).replace("'", '"')) == [one]


@pytest.mark.parametrize("reply", ['{"MSFT": {"signal": "hold", "justification": "x"}}', '{"search": "q"}',
                                   '{"decisions": {}, "chart": []}'])
def test_request_not_chart_is_none(reply):
    assert chart_tool.request(reply) is None


def test_request_bad_shape_raises():
    with pytest.raises(ParseError, match="chart"):
        chart_tool.request('{"chart": "NVDA"}')


@pytest.fixture
def session(tmp_path):
    db = DB.open(tmp_path / "t.db")
    db.upsert_daily(days("MSFT", date(2026, 1, 5), [float(i) for i in range(1, 20)]))  # through Fri 01-30
    return chart_tool.Session(db, CFG.settings, NOW)


async def test_run_draws_and_clamps(session):
    text, pngs = await session.run([{"symbol": "msft", "tf": "1Day", "start": "2010-01-01", "end": "2030-01-01",
                                     "log": True}])
    assert len(pngs) == 1 and pngs[0][:4] == b"\x89PNG"
    # start clamped to history_start, end to the last complete day (today's bar would leak the close)
    assert "# CHART image 1: MSFT 1Day 2016-01-01..2026-01-29 log" in text
    assert text.endswith("Reply with the decision JSON.")


async def test_run_excludes_today(session):
    session.db.upsert_daily([day_bar("NVDA", date(2026, 1, 30), 1, 2, 0.5, 1.5)])  # only today's bar
    text, pngs = await session.run([{"symbol": "NVDA", "tf": "1Day", "start": "2026-01-01"}])
    assert pngs == () and "# CHART 1 skipped: no price history for NVDA" in text


async def test_run_skips_bad_and_caps(session):
    good = {"symbol": "MSFT", "tf": "1Week", "start": "2026-01-01"}
    text, pngs = await session.run([{"symbol": "ZZZZ", "tf": "1Day", "start": "2026-01-01"},
                                    {"symbol": "MSFT", "tf": "1Min", "start": "2026-01-01"},
                                    good, good, good])
    assert "# CHART 1 skipped: ZZZZ not in universe" in text
    assert "# CHART 2 skipped: tf must be one of 1Day, 1Week, 1Month" in text
    assert len(pngs) == 1 and "# CHART image 1: MSFT 1Week" in text  # cap 3: items 4-5 dropped
    assert "only the first 3 charts" in text


async def test_run_bad_dates(session):
    text, pngs = await session.run([{"symbol": "MSFT", "tf": "1Day", "start": "2026-01-20", "end": "2026-01-10"},
                                    {"symbol": "MSFT", "tf": "1Day", "start": "soon"},
                                    {"symbol": "MSFT", "tf": "1Day", "log": "false"}])
    assert pngs == () and "# CHART 1 skipped: start after end" in text and "# CHART 2 skipped:" in text
    assert '# CHART 3 skipped: "log" must be true or false' in text


async def test_seen_reads_back_run(session):
    text, _ = await session.run([{"symbol": "MSFT", "tf": "1Week", "start": "2026-01-05", "log": True},
                                 {"symbol": "ZZZZ"}, {"symbol": "MSFT", "end": "2026-01-20"}])
    assert chart_tool.seen("PROMPT" + text) == [ChartSpec("MSFT", "1Week", date(2026, 1, 5), date(2026, 1, 29), True),
                                                ChartSpec("MSFT", "1Day", date(2025, 1, 20), date(2026, 1, 20))]


async def test_render_crash_is_a_note(session, monkeypatch, caplog):
    def boom(*a):
        raise RuntimeError("matplotlib exploded")
    monkeypatch.setattr(chart_tool.charts, "history_chart", boom)
    text, pngs = await session.run([{"symbol": "MSFT", "tf": "1Day"}])
    assert pngs == () and "# CHART 1 skipped: chart failed" in text and "matplotlib exploded" in caplog.text


async def test_default_start_one_year(session):
    text, _ = await session.run([{"symbol": "MSFT", "tf": "1Day"}])
    assert "# CHART image 1: MSFT 1Day 2025-01-29..2026-01-29 linear" in text


async def test_second_chart_step_raises(session):
    await session.run([])
    with pytest.raises(ParseError, match="chart not allowed now"):
        session.request('{"chart": []}')
