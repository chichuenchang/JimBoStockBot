"""Read-only views over the DB for the dashboard."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from stockbot import interval, pause
from stockbot.broker.sim import SimBroker
from stockbot.clock import et_at, et_date, month_key
from stockbot.config import AppConfig
from stockbot.db import DB, iso, parse_ts
from stockbot.llm.budget import level
from stockbot.metrics import (aligned_daily_returns, alpha_beta, conviction_buckets, daily_closes, daily_returns,
                              max_drawdown, sharpe, sharpe_ci, trade_stats, with_start)
from stockbot.models import BENCHMARK

LEADERBOARD_KEYS = ["arm", "model", "variant", "nav", "return_pct", "vs_benchmark_pct", "pnl", "costs",
                    "llm_spend", "net_after_llm", "win_rate", "avg_win", "avg_loss", "profit_factor",
                    "expectancy", "pnl_ex_best",
                    "sharpe", "sharpe_ci", "alpha_pct", "beta", "max_drawdown", "trades", "invested_pct"]
MAX_POINTS = 2000


def _broker(db: DB, cfg: AppConfig) -> SimBroker:
    return SimBroker(db, cfg.settings.slippage_bps, cfg.settings.commission_per_order)


def _costs(db: DB, account: str, cfg: AppConfig) -> float:
    """Estimated slippage + commission. Target fills are limit fills: no slippage."""
    slip = cfg.settings.slippage_bps / 1e4
    total = 0.0
    for f in db.fills(account):
        if f.side == "buy":
            total += f.qty * (f.price - f.price / (1 + slip))
        elif f.reason != "target":
            total += f.qty * (f.price / (1 - slip) - f.price)
        total += cfg.settings.commission_per_order
    return total


def leaderboard(db: DB, cfg: AppConfig, only: str | None = None) -> list[dict]:
    arms = {a.name: a for a in cfg.arms}
    broker = _broker(db, cfg)
    navs = {a: broker.nav(a, {}) for a in db.accounts() if not only or a in (only, BENCHMARK)}
    bench_ret = (navs[BENCHMARK] / db.start_cash(BENCHMARK) - 1) * 100 if BENCHMARK in navs else None
    bench_closes = daily_closes(with_start(db.equity_series(BENCHMARK), db.start_cash(BENCHMARK))) \
        if BENCHMARK in navs else {}
    rows = []
    for account, nav in navs.items():
        if only and account != only:
            continue
        start = db.start_cash(account)
        arm = arms.get(account)
        series = with_start(db.equity_series(account), start)
        stats = trade_stats(db.closed_trades(account))
        cash = db.load_account(account).cash
        pnl = nav - start
        spend = db.spent_by_arm(account)
        ret = (nav / start - 1) * 100
        daily = daily_returns(series)
        alpha, beta = (None, None) if account == BENCHMARK else \
            alpha_beta(*aligned_daily_returns(daily_closes(series), bench_closes))
        rows.append(dict(zip(LEADERBOARD_KEYS, [
            account,
            (arm.model or "-") if arm else "-",
            arm.variant if arm else "benchmark",
            nav, ret,
            None if bench_ret is None or account == BENCHMARK else ret - bench_ret,
            pnl, _costs(db, account, cfg), spend, pnl - spend,
            None if stats.win_rate is None else stats.win_rate * 100,
            stats.avg_win, stats.avg_loss, stats.profit_factor, stats.expectancy, stats.pnl_ex_best,
            sharpe(daily), sharpe_ci(daily),
            None if alpha is None else alpha * 100, beta,
            max_drawdown([v for _, v in series] + [nav]) * 100,
            stats.n,
            (nav - cash) / nav * 100 if nav else 0.0,
        ])))
    rows.sort(key=lambda r: r["nav"], reverse=True)
    return rows


def _thin(points: list) -> list:
    step = max(1, len(points) // MAX_POINTS)
    out = points[::step]
    if points and out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def equity(db: DB, mode: str = "usd") -> dict[str, list]:
    out = {}
    for a in db.accounts():
        start = db.start_cash(a)
        pts = [[int(ts.timestamp() * 1000), nav if mode == "usd" else (nav / start - 1) * 100]
               for ts, nav in db.equity_series(a)]
        out[a] = _thin(pts)
    return out


def drawdown(db: DB) -> dict[str, list]:
    out = {}
    for a in db.accounts():
        peak, pts = db.start_cash(a), []  # same base as the leaderboard's worst drop
        for ts, nav in db.equity_series(a):
            peak = max(peak, nav)
            pts.append([int(ts.timestamp() * 1000), (nav / peak - 1) * 100 if peak > 0 else 0.0])
        out[a] = _thin(pts)
    return out


def daily_pnl(db: DB) -> dict[str, list]:
    out = {}
    for a in db.accounts():
        closes = list(daily_closes(with_start(db.equity_series(a), db.start_cash(a))).items())
        out[a] = [[d.isoformat(), nav - prev] for (_, prev), (d, nav) in zip(closes, closes[1:])]
    return out


def heatmap(db: DB) -> dict[str, dict[str, float]]:
    out = {}
    for a in db.accounts():
        cells: dict[str, float] = defaultdict(float)
        for t in db.closed_trades(a):
            cells[t.symbol] += t.pnl
        out[a] = dict(cells)
    return out


def positions(db: DB, cfg: AppConfig, account: str) -> list[dict]:
    broker = _broker(db, cfg)
    rows = []
    for p in db.load_account(account).positions.values():
        px = broker.last_close(p.symbol) or p.entry_price
        rows.append({"symbol": p.symbol, "qty": p.qty, "entry_price": p.entry_price,
                     "entry_ts": p.entry_ts.isoformat(), "price": px,
                     "unrealized": (px - p.entry_price) * p.qty, "stop_loss": p.exit_plan.stop_loss,
                     "profit_target": p.exit_plan.profit_target, "invalidation": p.exit_plan.invalidation})
    return rows


def trades(db: DB, account: str) -> list[dict]:
    return [{"symbol": t.symbol, "entry_ts": t.entry_ts.isoformat(), "exit_ts": t.exit_ts.isoformat(),
             "entry_price": t.entry_price, "exit_price": t.exit_price, "qty": t.qty,
             "hold_hours": (t.exit_ts - t.entry_ts).total_seconds() / 3600, "pnl": t.pnl,
             "r_multiple": t.r_multiple, "exit_reason": t.exit_reason, "justification": t.justification,
             "replayed": t.replayed, "conviction": t.conviction}
            for t in reversed(db.closed_trades(account))]


def conviction(db: DB, account: str) -> list[dict]:
    return [{**r, "win_rate": r["win_rate"] * 100} for r in conviction_buckets(db.closed_trades(account))]


def decisions(db: DB, account: str, limit: int = 50) -> list[dict]:
    out = []
    for r in db.recent_decisions(account, limit):
        parsed = json.loads(r["parsed_json"] or "{}").get("per_symbol", {})
        acts = [f"{s} {d['signal']}: {d.get('justification', '')}".strip(": ")
                for s, d in parsed.items() if d["signal"] != "hold" or d.get("justification")]
        out.append({"id": r["id"], "slot_ts": r["slot_ts"], "reason": r["reason"], "status": r["status"],
                    "cost": r["cost"], "summary": " | ".join(acts) or "hold all"})
    return out


def fill_markers(db: DB, account: str, symbol: str) -> list[dict]:
    return [{"time": int(f.ts.timestamp()), "side": f.side, "price": f.price, "reason": f.reason,
             "replayed": f.replayed}
            for f in db.fills(account) if f.symbol == symbol]


def quota(db: DB, cfg: AppConfig) -> dict[str, dict]:
    """Latest flat-plan quota poll per enabled model that has one (llm/quota.py); state as budget's."""
    out = {}
    for name, m in cfg.models.items():
        if m.enabled and (rows := db.latest_quota(name)):
            used = max(r["used_ratio"] for r in rows)
            out[name] = {"windows": rows, "state": level(used, 1, cfg.settings.budget_warn_frac)}
    return out


ANALYST = "analyst"  # usage rows with no arm: the market report (report/analyst.py)
WINDOW_UNIT = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def _totals(rows: list[dict]) -> dict:
    return {"calls": len(rows), "input": sum(r["input_tokens"] for r in rows),
            "cached": sum(r["cached_tokens"] for r in rows), "output": sum(r["output_tokens"] for r in rows),
            "cost": sum(r["cost"] for r in rows)}


def _window_start(window: dict, now: datetime) -> datetime | None:
    """Quota window "5h" / "7d" ends at its reset (else now); None when the name has no length."""
    try:
        length = int(window["window"][:-1]) * WINDOW_UNIT[window["window"][-1:]]
    except (ValueError, KeyError):
        return None
    return (parse_ts(window["reset_ts"]) if window["reset_ts"] else now) - length


def _plan(db: DB, model: str, info: dict, bots: list[str], now: datetime, start: datetime) -> dict:
    """Plan quota plus each bot's estimated slice: window used x its share of the model's tokens there."""
    windows = []
    for w in info["windows"]:
        ws = _window_start(w, now)
        tokens, calls = dict.fromkeys(bots, 0), dict.fromkeys(bots, 0)
        for r in db.usage_since(ws) if ws else []:
            if r["model"] == model:
                b = r["arm"] or ANALYST
                tokens[b] = tokens.get(b, 0) + r["input_tokens"] + r["output_tokens"]
                calls[b] = calls.get(b, 0) + 1
        total = sum(tokens.values())
        windows.append({**w, "start": ws and iso(ws), "bots": {
            b: {"calls": calls[b], "tokens": t, "used_ratio": w["used_ratio"] * t / total if total else 0.0}
            for b, t in tokens.items()}})
    history: dict[str, list] = {}
    for r in db.quota_since(model, start):
        history.setdefault(r["window"], []).append([int(parse_ts(r["ts"]).timestamp() * 1000), r["used_ratio"] * 100])
    return {"state": info["state"], "bots": bots, "windows": windows,
            "history": {k: _thin(v) for k, v in history.items()}}


def usage(db: DB, cfg: AppConfig, now: datetime, days: int | None = None) -> dict:
    """Token use per bot: today (ET) and this month, a running total since today's start or `days`
    back, and quota per flat-plan model."""
    today = et_at(et_date(now), "00:00")
    start = today if days is None else min(today, now - timedelta(days=days))
    model_of = {a.name: a.model for a in cfg.arms if a.enabled and a.model}
    model_of[ANALYST] = cfg.settings.analyst_model
    rows = db.usage_since(start)
    month = {r["arm"] or ANALYST: r for r in db.usage_by_arm(month_key(now))}
    bots = [b for b in model_of if b != ANALYST or ANALYST in month]
    bots += [b for b in month if b not in bots]  # arms gone from config still billed this month
    by_bot: dict[str, list] = defaultdict(list)
    for r in rows:
        by_bot[r["arm"] or ANALYST].append(r)
    plans = quota(db, cfg)
    series = {}
    for b, rs in by_bot.items():
        run, pts = 0, []
        for r in rs:
            run += r["input_tokens"] + r["output_tokens"]
            pts.append([int(parse_ts(r["ts"]).timestamp() * 1000), run])
        series[b] = _thin(pts)
    today_iso = iso(today)
    return {
        "start": iso(start),
        "bots": [{"bot": b, "model": model_of.get(b) or month[b]["model"], "flat": model_of.get(b) in plans,
                  "today": _totals([r for r in by_bot[b] if r["ts"] >= today_iso]),
                  "month": {k: month.get(b, {}).get(k) or 0 for k in ("calls", "input", "cached", "output", "cost")}}
                 for b in bots],
        "series": series,
        "quota": {m: _plan(db, m, info, [b for b in bots if model_of.get(b) == m], now, start)
                  for m, info in plans.items()},
    }


def status(db: DB, cfg: AppConfig, budget) -> dict:
    hb = db.kv_get("heartbeat")
    return {
        "heartbeat": hb,
        "market_open": db.kv_get("market_open") == "1",
        "data_ok": db.kv_get("data_ok") != "0",
        "paused_all": pause.flagged(db, pause.ALL),
        "interval": {"minutes": interval.get(db, cfg.settings), "source": interval.source(db)},
        "budget": budget.summary(),
        "quota": quota(db, cfg),
        "arms": [{"arm": a.name, "model": a.model or "-", "variant": a.variant,
                  "last_decision": (lambda t: t.isoformat() if t else None)(db.last_decision_ts(a.name)),
                  "paused": pause.flagged(db, a.name)} for a in cfg.arms if a.enabled],
    }
