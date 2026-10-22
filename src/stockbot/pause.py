"""Arm pause flags. Stored in the DB kv table so CLI, dashboard and daemon share them."""
from __future__ import annotations

from stockbot.db import DB

ALL = "_all"  # pseudo-arm: pauses every arm


def _key(arm: str) -> str:
    return f"pause:{arm}"


def set_paused(db: DB, arm: str, on: bool) -> None:
    if on:
        db.kv_set(_key(arm), "1")
    else:
        db.kv_delete(_key(arm))


def flagged(db: DB, arm: str) -> bool:
    """This flag alone; ignores the global one."""
    return db.kv_get(_key(arm)) is not None


def is_paused(db: DB, arm: str) -> bool:
    return flagged(db, arm) or flagged(db, ALL)
