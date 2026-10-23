"""Decision interval in minutes. Runtime override in the DB kv table (shared by CLI, dashboard and
daemon, like pause flags) beats settings.cycle_every_min; the daemon re-reads it every pass."""
from __future__ import annotations

from typing import Literal

from stockbot.config import Settings
from stockbot.db import DB

KEY = "cycle_every_min"
MIN, MAX = 1, 60


def get(db: DB, s: Settings) -> int:
    raw = db.kv_get(KEY)
    return int(raw) if raw else s.cycle_every_min


def source(db: DB) -> Literal["default", "override"]:
    return "override" if db.kv_get(KEY) else "default"


def set(db: DB, minutes: int | None) -> None:
    """None clears the override."""
    if minutes is None:
        db.kv_delete(KEY)
        return
    if not MIN <= minutes <= MAX:
        raise ValueError(f"interval must be {MIN}..{MAX} minutes, got {minutes}")
    db.kv_set(KEY, str(minutes))
