"""Flat-plan quota for models with quota_url. Kimi coding plan GET /usages answers
{"usages": {"limit_5h": {"used_ratio": 0.12, "reset_time": "...Z"}, "limit_7d": {...}}}."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from stockbot.config import ModelCfg, api_key


@dataclass(frozen=True)
class Window:
    name: str  # "5h", "7d"
    used: float  # 0..1
    reset: datetime | None


class QuotaError(Exception):
    pass


def parse(data: dict) -> list[Window]:
    try:
        return [Window(k.removeprefix("limit_"), float(v["used_ratio"]),
                       datetime.fromisoformat(v["reset_time"]) if v.get("reset_time") else None)
                for k, v in data["usages"].items()]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise QuotaError(f"unexpected quota response: {e!r}") from e


async def fetch(cfg: ModelCfg, timeout_s: float = 20) -> list[Window]:
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.get(cfg.quota_url, headers={"Authorization": f"Bearer {api_key(cfg)}"},
                                  timeout=timeout_s)
    except httpx.HTTPError as e:
        raise QuotaError(f"transport error {type(e).__name__}") from e
    if resp.status_code != 200:
        raise QuotaError(f"HTTP {resp.status_code} {resp.text[:200]}")
    try:
        return parse(resp.json())
    except ValueError as e:
        raise QuotaError("non-JSON quota response") from e
