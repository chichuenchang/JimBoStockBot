"""Chart step for arms with charts: true. Before its decision the model may reply
{"chart": [{"symbol", "tf", "start", "end", "log"}, ...]} once; the harness draws each from
daily_bars and sends the PNGs as images with the next call (protocol as agent/research.py)."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from stockbot import charts
from stockbot.agent.schema import ParseError, first_json_object
from stockbot.charts import ChartSpec
from stockbot.clock import et_date
from stockbot.config import Settings
from stockbot.db import DB
from stockbot.history import HISTORY_TFS

log = logging.getLogger("stockbot")
DEFAULT_SPAN = timedelta(days=365)
_SEEN = re.compile(r"^# CHART image \d+: (\S+) (\S+) (\S+)\.\.(\S+) (linear|log)$", re.MULTILINE)  # parses _line


def _line(n: int, c: ChartSpec) -> str:
    return f"# CHART image {n}: {c.symbol} {c.tf} {c.start}..{c.end} {'log' if c.log else 'linear'}"


def seen(prompt: str) -> list[ChartSpec]:
    """Charts a stored decision prompt showed the bot, in image order."""
    return [ChartSpec(sym, tf, date.fromisoformat(a), date.fromisoformat(b), scale == "log")
            for sym, tf, a, b, scale in _SEEN.findall(prompt)]


def request(reply: str) -> list | None:
    """The chart list, or None when the reply is not a chart step. A single object counts as a list of one."""
    obj = first_json_object(reply)
    if "chart" not in obj or "decisions" in obj or any(isinstance(v, dict) for k, v in obj.items() if k != "chart"):
        return None
    raw = obj["chart"]
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        raise ParseError('"chart" needs a list of chart objects, e.g. {"chart": [{"symbol": "NVDA", "tf": "1Week"}]}')
    return raw


class Session:
    """One decision's chart state. Charts end on the last complete day: today's bar holds its close."""

    def __init__(self, db: DB, s: Settings, now: datetime):
        self.db, self.s = db, s
        self.last = et_date(now) - timedelta(days=1)
        self.done = False

    def request(self, reply: str) -> list | None:
        raw = request(reply)
        if raw is not None and self.done:
            raise ParseError("chart not allowed now; reply with the decision JSON")
        return raw

    def _spec(self, item) -> ChartSpec:
        if not isinstance(item, dict):
            raise ValueError("not a chart object")
        sym = str(item.get("symbol", "")).upper()
        if sym not in self.s.universe:
            raise ValueError(f"{sym or 'symbol'} not in universe")
        tf = item.get("tf", "1Day")
        if tf not in HISTORY_TFS:
            raise ValueError(f"tf must be one of {', '.join(HISTORY_TFS)}")
        end = min(date.fromisoformat(item["end"]), self.last) if item.get("end") else self.last
        start = date.fromisoformat(item["start"]) if item.get("start") else end - DEFAULT_SPAN
        start = max(start, self.s.history_start)
        if start > end:
            raise ValueError("start after end")
        log_scale = item.get("log", False)
        if not isinstance(log_scale, bool):  # bool("false") is True
            raise TypeError('"log" must be true or false')
        return ChartSpec(sym, tf, start, end, log_scale)

    async def run(self, raw: list) -> tuple[str, tuple[bytes, ...]]:
        """Text block to append (one line per chart) + the PNGs, in image order."""
        self.done = True
        cap = self.s.chart_max_per_decision
        lines, pngs = [], []
        for n, item in enumerate(raw[:cap], 1):
            try:
                c = self._spec(item)
            except (ValueError, TypeError) as e:  # bad date strings raise ValueError too
                lines.append(f"# CHART {n} skipped: {e}")
                continue
            try:
                pngs.append(await asyncio.to_thread(charts.history_chart, self.db, c))
            except ValueError:
                lines.append(f"# CHART {n} skipped: no price history for {c.symbol} in {c.start}..{c.end}")
                continue
            except Exception:  # a chart is optional: never lose the decision over it
                log.exception("chart %s failed", c)
                lines.append(f"# CHART {n} skipped: chart failed")
                continue
            lines.append(_line(len(pngs), c))
        if len(raw) > cap:
            lines.append(f"(only the first {cap} charts drawn)")
        if not raw:
            lines.append("(no charts requested)")
        return ("\n\n# PRICE HISTORY CHARTS (images attached, in order)\n" + "\n".join(lines)
                + "\n\nReply with the decision JSON."), tuple(pngs)
