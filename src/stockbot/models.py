"""Shared value types. All datetimes are timezone-aware UTC."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Side = Literal["buy", "sell"]
FillReason = Literal["entry", "add", "close", "stop", "target"]
BENCHMARK = "benchmark-QQQ"  # account of the buy-and-hold benchmark arm


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: datetime  # bar start
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Session:
    day: date
    open_utc: datetime
    close_utc: datetime


@dataclass(frozen=True)
class NewsItem:
    ts: datetime
    headline: str
    symbols: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class ExitPlan:
    stop_loss: float | None
    profit_target: float | None
    invalidation: str = ""


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_ts: datetime
    exit_plan: ExitPlan
    risk_per_share: float | None = None
    peak: float | None = None  # highest high since entry, for trailing stops


@dataclass(frozen=True)
class Fill:
    account: str
    symbol: str
    side: Side
    qty: float
    price: float
    ts: datetime
    reason: FillReason
    replayed: bool = False


@dataclass
class AccountState:
    account: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosedTrade:
    account: str
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    r_multiple: float | None
    exit_reason: str
    justification: str
    replayed: bool = False  # exit filled during restart catch-up
    exit_justification: str = ""  # empty for stop/target fills
