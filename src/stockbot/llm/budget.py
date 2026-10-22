"""Monthly LLM spend guard (calendar month, ET)."""
from __future__ import annotations

from typing import Literal

from stockbot.clock import Clock, month_key
from stockbot.config import AppConfig
from stockbot.db import DB
from stockbot.llm.base import Usage, cost_usd

State = Literal["ok", "warn", "exhausted"]
SEVERITY: tuple[State, ...] = ("ok", "warn", "exhausted")


class Budget:
    def __init__(self, db: DB, cfg: AppConfig, clock: Clock):
        self.db, self.cfg, self.clock = db, cfg, clock

    def _month(self) -> str:
        return month_key(self.clock.now())

    def _level(self, spent: float, cap: float) -> State:
        if spent >= cap:
            return "exhausted"
        if spent >= self.cfg.settings.budget_warn_frac * cap:
            return "warn"
        return "ok"

    def state(self, model: str) -> State:
        """Worse of the model's own cap and the global cap."""
        m = self.cfg.models[model]
        if not m.enabled or m.monthly_cap_usd <= 0:
            return "exhausted"
        month = self._month()
        own = self._level(self.db.spent(model, month), m.monthly_cap_usd)
        total = self._level(self.db.spent(None, month), self.cfg.settings.global_cap_usd)
        return max(own, total, key=SEVERITY.index)

    def record(self, model: str, arm: str | None, purpose: str, usage: Usage) -> float:
        cost = cost_usd(self.cfg.models[model], usage)
        self.db.insert_usage(model, arm, purpose, self.clock.now(), usage.input_tokens,
                             usage.cached_input_tokens, usage.output_tokens, cost)
        return cost

    def summary(self) -> dict:
        month = self._month()
        out = {name: {"spent": self.db.spent(name, month), "cap": m.monthly_cap_usd,
                      "state": self.state(name)}
               for name, m in self.cfg.models.items() if m.enabled}
        total = self.db.spent(None, month)
        cap = self.cfg.settings.global_cap_usd
        out["_total"] = {"spent": total, "cap": cap, "state": self._level(total, cap)}
        return out
