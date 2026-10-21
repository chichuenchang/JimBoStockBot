"""Clock abstraction and ET schedule helpers."""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from stockbot.config import Settings
from stockbot.models import Session

ET = ZoneInfo("America/New_York")


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ReplayClock:
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, td: timedelta) -> None:
        self._now += td

    def set(self, dt: datetime) -> None:
        self._now = dt


def _et_on(session: Session, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(session.day, time(h, m), tzinfo=ET).astimezone(UTC)


def cycle_slots(session: Session, s: Settings) -> list[datetime]:
    """Decision times from cycle_start to cycle_end every cycle_every_min, inside the session."""
    t = _et_on(session, s.cycle_start)
    end = _et_on(session, s.cycle_end)
    out = []
    while t <= end:
        if in_session(t, session):
            out.append(t)
        t += timedelta(minutes=s.cycle_every_min)
    return out


def report_slots(session: Session, s: Settings) -> list[datetime]:
    """Report times for the session day; ones at or after close are dropped."""
    return [t for t in (_et_on(session, hm) for hm in s.report_times) if t < session.close_utc]


def in_session(ts: datetime, session: Session) -> bool:
    return session.open_utc <= ts < session.close_utc


def month_key(ts: datetime) -> str:
    return ts.astimezone(ET).strftime("%Y-%m")


def et_date(ts: datetime):
    return ts.astimezone(ET).date()
