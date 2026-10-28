"""Bot tab order, dragged on the dashboard. DB kv table, so it outlives daemon restarts.
Display only: colors still follow config order (window.SERIES)."""
from __future__ import annotations

import json

from stockbot.db import DB

KEY = "tab_order"


def get(db: DB, arms: list[str]) -> list[str]:
    """Saved order; arms dropped from config vanish, new ones go last in config order."""
    try:
        saved = json.loads(db.kv_get(KEY) or "[]")
    except ValueError:
        saved = []
    if not isinstance(saved, list):  # bad kv value must not 500 every page: fall back to config order
        saved = []
    saved = [a for a in saved if a in arms]
    return saved + [a for a in arms if a not in saved]


def set(db: DB, order: list[str], arms: list[str]) -> None:
    if (not isinstance(order, list) or not all(isinstance(a, str) for a in order)
            or sorted(order) != sorted(arms)):
        raise ValueError("order must list every enabled arm once")
    db.kv_set(KEY, json.dumps(order))
