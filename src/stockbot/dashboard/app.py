"""Localhost dashboard (FastAPI). Binds 127.0.0.1 only."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stockbot import charts, history, interval
from stockbot import pause as pause_flags
from stockbot.agent import chart_tool
from stockbot.clock import RealClock, et_at, et_date
from stockbot.config import AppConfig
from stockbot.dashboard import queries as q
from stockbot.dashboard import tab_order
from stockbot.dashboard.glossary import COLUMN_GROUPS, LABELS, TIPS
from stockbot.data.base import TF_DURATION, DataError, MarketData
from stockbot.db import DB
from stockbot.llm.budget import Budget
from stockbot.models import BENCHMARK, Session

HERE = Path(__file__).parent
HOST = "127.0.0.1"
CANDLE_WINDOW = {"1Min": timedelta(days=2), "1Hour": timedelta(days=20), "1Day": timedelta(days=200)}


def _regular_hours(bars: list, tf: str, sessions: dict[date, Session]) -> list:
    """Drop pre/post-market bars (the feed returns them); an hour bar stays if it overlaps the session."""
    return [b for b in bars if (s := sessions.get(et_date(b.ts)))
            and s.open_utc < b.ts + TF_DURATION[tf] and b.ts < s.close_utc]


def create_app(db: DB, cfg: AppConfig, budget: Budget, data: MarketData | None = None) -> FastAPI:
    app = FastAPI(title="StockBot", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    arm_names = [a.name for a in cfg.arms if a.enabled]
    series_order = arm_names + [BENCHMARK]
    names = {a.name: a.label or a.name for a in cfg.arms}
    tpl.env.filters["arm_label"] = lambda arm: names.get(arm, arm)  # DB may hold arms no longer in config
    js_version = int((HERE / "static" / "app.js").stat().st_mtime)  # cache-bust: new code, new URL

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # No auth on localhost: refuse writes the browser marks as sent from another site.
        if request.method != "GET" and request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    def known(arm: str) -> str:
        if arm not in db.accounts():
            raise HTTPException(404, f"unknown arm {arm}")
        return arm

    def page(request, name, **ctx):
        return tpl.TemplateResponse(request, name, {"tips": TIPS, "labels": LABELS, "series": series_order,
                                                 "arms": tab_order.get(db, arm_names), "names": names,
                                                 "js_version": js_version, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html", rows=q.leaderboard(db, cfg), groups=COLUMN_GROUPS)

    @app.get("/arm/{arm}", response_class=HTMLResponse)
    def arm_page(request: Request, arm: str):
        [row] = q.leaderboard(db, cfg, only=known(arm))
        return page(request, "arm.html", arm=arm, row=row, universe=list(cfg.settings.universe))

    @app.get("/api/leaderboard")
    def api_leaderboard():
        return q.leaderboard(db, cfg)

    @app.get("/api/equity")
    def api_equity(mode: str = "usd"):
        return q.equity(db, "pct" if mode == "pct" else "usd")

    @app.get("/api/drawdown")
    def api_drawdown():
        return q.drawdown(db)

    @app.get("/api/daily_pnl")
    def api_daily():
        return q.daily_pnl(db)

    @app.get("/api/heatmap")
    def api_heatmap():
        return q.heatmap(db)

    @app.get("/api/status")
    def api_status():
        return q.status(db, cfg, budget)

    @app.get("/api/arm/{arm}/positions")
    def api_positions(arm: str):
        return q.positions(db, cfg, known(arm))

    @app.get("/api/arm/{arm}/trades")
    def api_trades(arm: str):
        return q.trades(db, known(arm))

    @app.get("/api/arm/{arm}/conviction")
    def api_conviction(arm: str):
        return q.conviction(db, known(arm))

    @app.get("/api/arm/{arm}/decisions")
    def api_decisions(arm: str):
        return q.decisions(db, known(arm))

    @app.get("/api/arm/{arm}/rejections")
    def api_rejections(arm: str):
        return db.recent_rejections(known(arm), 100)

    @app.get("/api/decision/{decision_id}")
    def api_decision(decision_id: int):
        d = db.decision(decision_id)
        if d is None:
            raise HTTPException(404)
        return {**d, "charts": [asdict(c) for c in chart_tool.seen(d["prompt"])]}

    @app.get("/api/candles")
    async def api_candles(symbol: str, arm: str, tf: str = "1Min"):
        if symbol not in cfg.settings.universe:
            raise HTTPException(404)
        tf = tf if tf in CANDLE_WINDOW or tf in history.HISTORY_TFS else "1Min"
        end = datetime.now(UTC)
        long = tf in history.HISTORY_TFS
        bars = db.daily_bars(symbol, cfg.settings.history_start, et_date(end) + timedelta(days=1)) if long else []
        err, sessions = None, {}
        start = (et_at(et_date(bars[-1].ts) + timedelta(days=1), "00:00") if bars
                 else end - CANDLE_WINDOW["1Day" if long else tf])
        if data is not None and start < end:  # history holds today once the close is synced
            try:
                if long:
                    bars += await data.bars(symbol, "1Day", start, end)
                else:  # calendar knows half-days
                    fetched, cal = await asyncio.gather(data.bars(symbol, tf, start, end),
                                                        data.calendar(et_date(start), et_date(end)))
                    bars, sessions = fetched, {s.day: s for s in cal}
            except DataError as e:
                err = str(e)
        bars = history.aggregate(bars, tf) if long else _regular_hours(bars, tf, sessions)
        return {"bars": [{"time": int(b.ts.timestamp()), "open": b.open, "high": b.high, "low": b.low,
                          "close": b.close} for b in bars],
                "markers": q.fill_markers(db, known(arm), symbol), "error": err}

    @app.get("/api/chart")
    def api_chart(symbol: str, tf: str, start: date, end: date, log: bool = False):
        """The chart-tool picture a bot saw, re-drawn from stored history."""
        if symbol not in cfg.settings.universe or tf not in history.HISTORY_TFS:
            raise HTTPException(404)
        try:
            png = charts.history_chart(db, charts.ChartSpec(symbol, tf, start, end, log))
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return Response(png, media_type="image/png")

    def set_pause(arm: str, on: bool):
        if arm != pause_flags.ALL and arm not in arm_names:
            raise HTTPException(404, f"unknown arm {arm}")
        pause_flags.set_paused(db, arm, on)
        return {"ok": True}

    @app.post("/api/pause/{arm}")
    def pause(arm: str):
        return set_pause(arm, True)

    @app.post("/api/resume/{arm}")
    def resume(arm: str):
        return set_pause(arm, False)

    @app.post("/api/pause_all")
    def pause_all():
        return set_pause(pause_flags.ALL, True)

    @app.post("/api/resume_all")
    def resume_all():
        return set_pause(pause_flags.ALL, False)

    @app.post("/api/interval")
    def set_interval(body: dict):
        m = body.get("minutes")
        if m is not None and type(m) is not int:
            raise HTTPException(400, "minutes must be a whole number or null")
        try:
            interval.set(db, m)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/tab_order")
    def set_tab_order(body: dict):
        try:
            tab_order.set(db, body.get("arms"), arm_names)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    return app


async def serve(ctx) -> None:
    """Run inside the daemon's event loop."""
    app = create_app(ctx.db, ctx.cfg, ctx.budget, ctx.data)
    config = uvicorn.Config(app, host=HOST, port=ctx.cfg.settings.dashboard_port, log_level="warning")
    await uvicorn.Server(config).serve()


def serve_forever(db: DB, cfg: AppConfig, data: MarketData | None = None) -> None:
    app = create_app(db, cfg, Budget(db, cfg, RealClock()), data)
    print(f"dashboard: http://{HOST}:{cfg.settings.dashboard_port}")
    uvicorn.run(app, host=HOST, port=cfg.settings.dashboard_port, log_level="warning")
