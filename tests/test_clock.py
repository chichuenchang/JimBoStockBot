from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from stockbot.clock import ET, ReplayClock, cycle_slots, in_session, month_key, report_slots
from stockbot.config import load_config
from stockbot.models import Session

S = load_config(Path(__file__).parent / "config").settings


def sess(day: date, open_h: int, open_m: int, close_h: int) -> Session:
    return Session(day, datetime(day.year, day.month, day.day, open_h, open_m, tzinfo=UTC),
                   datetime(day.year, day.month, day.day, close_h, 0, tzinfo=UTC))


def et_hm(ts: datetime) -> str:
    return ts.astimezone(ET).strftime("%H:%M")


def test_full_day_has_9_slots():
    slots = cycle_slots(sess(date(2026, 10, 21), 13, 30, 20), S)
    assert [et_hm(s) for s in slots] == [
        "09:45", "10:30", "11:15", "12:00", "12:45", "13:30", "14:15", "15:00", "15:45"]
    assert all(s.tzinfo is not None for s in slots)


def test_half_day_drops_slots_after_close():
    slots = cycle_slots(sess(date(2026, 11, 27), 14, 30, 18), S)
    assert len(slots) == 5
    assert et_hm(slots[-1]) == "12:45"


def test_dst_switch():
    slots = cycle_slots(sess(date(2026, 11, 2), 14, 30, 21), S)
    assert slots[0] == datetime(2026, 11, 2, 14, 45, tzinfo=UTC)


def test_report_slots():
    assert [et_hm(s) for s in report_slots(sess(date(2026, 10, 21), 13, 30, 20), S)] == ["09:15", "12:30"]
    assert [et_hm(s) for s in report_slots(sess(date(2026, 11, 27), 14, 30, 18), S)] == ["09:15", "12:30"]


def test_month_key_uses_et():
    assert month_key(datetime(2026, 11, 1, 3, 0, tzinfo=UTC)) == "2026-10"


def test_in_session_bounds():
    s = sess(date(2026, 10, 21), 13, 30, 20)
    assert in_session(s.open_utc, s)
    assert not in_session(s.close_utc, s)
    assert not in_session(s.open_utc - timedelta(minutes=1), s)


def test_replay_clock():
    c = ReplayClock(datetime(2026, 10, 21, 13, 30, tzinfo=UTC))
    c.advance(timedelta(minutes=5))
    assert c.now() == datetime(2026, 10, 21, 13, 35, tzinfo=UTC)
