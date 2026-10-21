"""Localhost dashboard (FastAPI). Binds 127.0.0.1 only."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stockbot.clock import RealClock
from stockbot.config import AppConfig
from stockbot.daemon import BENCHMARK
from stockbot.dashboard import queries as q
from stockbot.dashboard.glossary import TIPS
from stockbot.data.base import DataError, MarketData
from stockbot.db import DB
from stockbot.llm.budget import Budget

HERE = Path(__file__).parent
HOST = "127.0.0.1"
CANDLE_WINDOW = {"1Min": timedelta(days=2), "1Hour": timedelta(days=20), "1Day": timedelta(days=200)}


def create_app(db: DB, cfg: AppConfig, budget: Budget, data: MarketData | None = None) -> FastAPI:
    app = FastAPI(title="StockBot", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    arm_names = [a.name for a in cfg.arms if a.enabled]
    series_order = arm_names + [BENCHMARK]

    def known(arm: str) -> str:
        if arm not in db.accounts():
            raise HTTPException(404, f"unknown arm {arm}")
        return arm

    def page(request, name, **ctx):
        return tpl.TemplateResponse(request, name, {"tips": TIPS, "series": series_order, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html", rows=q.leaderboard(db, cfg), keys=q.LEADERBOARD_KEYS)

    @app.get("/arm/{arm}", response_class=HTMLResponse)
    def arm_page(request: Request, arm: str):
        known(arm)
        return page(request, "arm.html", arm=arm, universe=list(cfg.settings.universe))

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

    @app.get("/api/arm/{arm}/decisions")
    def api_decisions(arm: str):
        return q.decisions(db, known(arm))

    @app.get("/api/arm/{arm}/rejections")
    def api_rejections(arm: str):
        return q.rejections(db, known(arm))

    @app.get("/api/decision/{decision_id}")
    def api_decision(decision_id: int):
        d = q.decision_detail(db, decision_id)
        if d is None:
            raise HTTPException(404)
        return d

    @app.get("/api/candles")
    async def api_candles(symbol: str, arm: str, tf: str = "1Min"):
        if symbol not in cfg.settings.universe:
            raise HTTPException(404)
        bars, err = [], None
        if data is not None:
            end = datetime.now(UTC)
            tf = tf if tf in CANDLE_WINDOW else "1Min"
            try:
                bars = [{"time": int(b.ts.timestamp()), "open": b.open, "high": b.high, "low": b.low,
                         "close": b.close} for b in await data.bars(symbol, tf, end - CANDLE_WINDOW[tf], end)]
            except DataError as e:
                err = str(e)
        return {"bars": bars, "markers": q.fill_markers(db, known(arm), symbol), "error": err}

    def set_pause(arm: str, on: bool):
        key = "pause:_all" if arm == "_all" else f"pause:{arm}"
        if arm != "_all" and arm not in arm_names:
            raise HTTPException(404, f"unknown arm {arm}")
        db.kv_set(key, "1") if on else db.kv_delete(key)
        return {"ok": True}

    @app.post("/api/pause/{arm}")
    def pause(arm: str):
        return set_pause(arm, True)

    @app.post("/api/resume/{arm}")
    def resume(arm: str):
        return set_pause(arm, False)

    @app.post("/api/pause_all")
    def pause_all():
        return set_pause("_all", True)

    @app.post("/api/resume_all")
    def resume_all():
        return set_pause("_all", False)

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
