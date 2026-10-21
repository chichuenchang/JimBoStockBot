"""Shared market report: computed features + analyst LLM interpretation (AA sections 0-5)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from stockbot.clock import ET
from stockbot.config import AppConfig
from stockbot.data.base import MarketData
from stockbot.db import DB
from stockbot.llm.base import LLMClient, LLMError
from stockbot.llm.budget import Budget
from stockbot.report.features import render_computed_table, ticker_features

ANALYST_SYSTEM = """You are a markets analyst writing a briefing for swing traders of US stocks.
Use ONLY the numbers in the provided data; never invent prices or levels.
Write markdown with exactly these sections:
**0. Context Snapshot (Risk Regime)** - regime, macro, news freshness.
## 1. Raw Data Dashboard - per ticker: 4h structure, momentum, relative volume.
## 2. Narrative vs. Reality Check - each news theme, its age, price reaction, state (PRICED IN / ABSORPTION / DIVERGENCE RISK).
## 3. FOMO Map & Catalyst Horizon - scheduled events, per-ticker upside-chase and downside-flush levels taken from the swing levels.
## 4. Alpha Setups: Menu of Hypotheses - per ticker 2-3 hypotheses: view, timeframe, edge depth (SHALLOW/MODERATE/DEEP), invalidation, steel-man risk.
## 5. Edge Quality Matrix - high conviction, tactical, no edge.
Long-only accounts: frame bearish views as reasons to avoid or exit, not to short."""

HISTORY = {"1Hour": timedelta(days=15), "4Hour": timedelta(days=45), "1Day": timedelta(days=45)}
NEWS_WINDOW = timedelta(hours=72)
EVENT_WINDOW_DAYS = 10


def _events(cfg: AppConfig, now: datetime) -> list[str]:
    today = now.astimezone(ET).date()
    return [f"{e.day} {e.time_et or ''} ET: {e.name}" + (f" ({', '.join(e.symbols)})" if e.symbols else "")
            for e in cfg.events if 0 <= (e.day - today).days <= EVENT_WINDOW_DAYS]


async def build_report(data: MarketData, llm: LLMClient | None, budget: Budget, cfg: AppConfig,
                       db: DB, now: datetime) -> tuple[int, str]:
    universe = list(cfg.settings.universe)
    features = {}
    for s in universe:
        b = {tf: await data.bars(s, tf, now - back, now) for tf, back in HISTORY.items()}
        features[s] = ticker_features(s, b["1Hour"], b["4Hour"], b["1Day"])
    news = await data.news(universe, now - NEWS_WINDOW)
    headlines = [f"{n.ts.astimezone(ET):%m-%d %H:%M} [{', '.join(n.symbols)}] {n.headline}"
                 for n in sorted(news, key=lambda n: n.ts, reverse=True)[:40]]
    events = _events(cfg, now)
    table = render_computed_table(features)

    analyst_model = cfg.settings.analyst_model
    analysis, reason = None, None
    if llm is None:
        reason = "no client"
    elif budget.state(analyst_model) == "exhausted":
        reason = "budget"
    else:
        user = (f"Time: {now.astimezone(ET):%A %Y-%m-%d %H:%M} ET\n\n"
                f"Features JSON:\n{json.dumps(features)}\n\n"
                f"Upcoming events:\n" + ("\n".join(events) or "(none)") + "\n\n"
                f"Headlines (newest first):\n" + ("\n".join(headlines) or "(none)"))
        try:
            res = await llm.complete(ANALYST_SYSTEM, user, json_mode=False)
            budget.record(analyst_model, None, "analyst", res.usage)
            analysis = res.text.strip()
        except LLMError as e:
            reason = str(e)

    parts = [f"# CURRENT STATE OF MARKETS\nIt is {now.astimezone(ET):%A, %B %d, %Y %H:%M} ET.\n"]
    parts.append(analysis if analysis else f"Analyst unavailable: {reason}")
    parts.append("## Computed Data (harness, authoritative)\n" + table)
    parts.append("## Upcoming Events\n" + ("\n".join(events) or "(none)"))
    if not analysis:
        parts.append("## Headlines\n" + ("\n".join(headlines) or "(none)"))
    text = "\n\n".join(parts)
    rid = db.insert_report(now, text, json.dumps(features))
    return rid, text
