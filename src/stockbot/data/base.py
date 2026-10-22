"""Market data interface."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from stockbot.models import Bar, NewsItem, Session

Timeframe = Literal["1Min", "1Hour", "4Hour", "1Day"]
TF_DURATION = {"1Min": timedelta(minutes=1), "1Hour": timedelta(hours=1),
               "4Hour": timedelta(hours=4), "1Day": timedelta(days=1)}


class DataError(Exception):
    pass


class MarketData(Protocol):
    async def bars(self, symbol: str, timeframe: Timeframe, start: datetime,
                   end: datetime) -> list[Bar]:
        """Bars with start <= ts < end, oldest first."""
        ...

    async def latest_prices(self, symbols: list[str]) -> dict[str, float]: ...

    async def news(self, symbols: list[str], since: datetime) -> list[NewsItem]: ...

    async def calendar(self, start: date, end: date) -> list[Session]: ...
