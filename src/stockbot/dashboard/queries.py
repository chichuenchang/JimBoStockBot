"""Read-only views over the DB for the dashboard."""
from __future__ import annotations

import json
from collections import defaultdict

from stockbot import pause
from stockbot.broker.sim import SimBroker
from stockbot.clock import et_date
from stockbot.config import AppConfig
from stockbot.db import DB, parse_ts
from stockbot.metrics import daily_returns, max_drawdown, sharpe, trade_stats
from stockbot.models import BENCHMARK

LEADERBOARD_KEYS = ["arm", "model", "variant", "nav", "return_pct", "vs_benchmark_pct", "pnl", "costs",
                    "llm_spend", "net_after_llm", "win_rate", "avg_win", "avg_loss", "profit_factor",
                    "sharpe", "max_drawdown", "trades", "invested_pct"]
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


def leaderboard(db: DB, cfg: AppConfig) -> list[dict]:
    arms = {a.name: a for a in cfg.arms}
    broker = _broker(db, cfg)
    navs = {a: broker.nav(a, {}) for a in db.accounts()}
    bench_ret = (navs[BENCHMARK] / db.start_cash(BENCHMARK) - 1) * 100 if BENCHMARK in navs else None
    rows = []
    for account, nav in navs.items():
        start = db.start_cash(account)
        arm = arms.get(account)
        series = db.equity_series(account)
        stats = trade_stats(db.closed_trades(account))
        cash = db.load_account(account).cash
        pnl = nav - start
        spend = db.spent_by_arm(account)
        ret = (nav / start - 1) * 100
        sh = sharpe(daily_returns(series))
        rows.append(dict(zip(LEADERBOARD_KEYS, [
            account,
            arm.model if arm else "-",
            arm.variant if arm else "benchmark",
            nav, ret,
            None if bench_ret is None or account == BENCHMARK else ret - bench_ret,
            pnl, _costs(db, account, cfg), spend, pnl - spend,
            None if stats.win_rate is None else stats.win_rate * 100,
            stats.avg_win, stats.avg_loss, stats.profit_factor, sh,
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
        peak, pts = float("-inf"), []
        for ts, nav in db.equity_series(a):
            peak = max(peak, nav)
            pts.append([int(ts.timestamp() * 1000), (nav / peak - 1) * 100 if peak > 0 else 0.0])
        out[a] = _thin(pts)
    return out


def daily_pnl(db: DB) -> dict[str, list]:
    out = {}
    for a in db.accounts():
        last = {}
        for ts, nav in db.equity_series(a):
            last[et_date(ts)] = nav
        prev, rows = db.start_cash(a), []
        for d in sorted(last):
            rows.append([d.isoformat(), last[d] - prev])
            prev = last[d]
        out[a] = rows
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
             "replayed": t.replayed}
            for t in reversed(db.closed_trades(account))]


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


def status(db: DB, cfg: AppConfig, budget) -> dict:
    hb = db.kv_get("heartbeat")
    return {
        "heartbeat": hb,
        "market_open": db.kv_get("market_open") == "1",
        "data_ok": db.kv_get("data_ok") != "0",
        "paused_all": pause.flagged(db, pause.ALL),
        "budget": budget.summary(),
        "arms": [{"arm": a.name, "model": a.model, "variant": a.variant,
                  "last_decision": (lambda t: t.isoformat() if t else None)(db.last_decision_ts(a.name)),
                  "paused": pause.flagged(db, a.name)} for a in cfg.arms if a.enabled],
    }
