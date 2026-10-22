"""Daemon loop: calendar, monitor, reports, decision cycles, wakes. One step = one pass."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from stockbot.agent.cycle import AppContext, run_cycle
from stockbot.broker.sim import buy_price, floor3
from stockbot.clock import cycle_slots, et_date, in_session, report_slots
from stockbot.data.base import DataError
from stockbot.models import BENCHMARK, Session
from stockbot.monitor import MINUTE, monitor_tick, wake_due
from stockbot.report.analyst import build_report

log = logging.getLogger("stockbot")
REPLAY_GAP = timedelta(minutes=2)


def floor_minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


class Daemon:
    def __init__(self, ctx: AppContext, serve_dashboard: bool = True):
        self.ctx = ctx
        self.serve_dashboard = serve_dashboard
        self.sessions: list[Session] = []
        self._calendar_day = None
        self._data_failed = False
        self.cycles_run = 0  # scheduled slots completed by this daemon instance
        s = ctx.cfg.settings
        for arm in ctx.cfg.arms:
            if arm.enabled:
                ctx.db.ensure_account(arm.name, s.start_nav)
        ctx.db.ensure_account(BENCHMARK, s.start_nav)

    # helpers
    async def _guard(self, what: str, coro) -> bool:
        try:
            await coro
            return True
        except DataError as e:
            self._data_failed = True
            log.error("%s: data error: %s", what, e)
        except Exception:
            log.exception("%s failed", what)
        return False

    def _today(self, now: datetime) -> Session | None:
        d = et_date(now)
        return next((s for s in self.sessions if s.day == d), None)

    def _done(self, kind: str, slot: datetime) -> bool:
        return self.ctx.db.kv_get(f"{kind}:{slot.isoformat()}") is not None

    def _mark(self, kind: str, slot: datetime) -> None:
        self.ctx.db.kv_set(f"{kind}:{slot.isoformat()}", "1")

    # jobs
    async def _refresh_calendar(self, now: datetime) -> None:
        d = et_date(now)
        if self._calendar_day == d:
            return
        self.sessions = await self.ctx.data.calendar(d - timedelta(days=7), d + timedelta(days=7))
        self._calendar_day = d

    async def _benchmark(self, now: datetime, session: Session) -> None:
        db = self.ctx.db
        if db.kv_get("benchmark_bought") or now < session.open_utc:
            return
        sym = self.ctx.cfg.settings.benchmark_symbol
        price = (await self.ctx.data.latest_prices([sym])).get(sym) or self.ctx.broker.last_close(sym)
        if not price:
            return
        cash = db.load_account(BENCHMARK).cash
        qty = floor3(cash / buy_price(price, 2 * self.ctx.cfg.settings.slippage_bps))  # 2x: cash cushion vs open gap
        self.ctx.broker.submit_market(BENCHMARK, sym, "buy", qty, None, "entry", now)
        db.kv_set("benchmark_bought", now.isoformat())

    async def _monitor(self, now: datetime, session: Session | None):
        db = self.ctx.db
        end = floor_minute(now) - MINUTE
        raw = db.kv_get("last_bar_ts")
        last = datetime.fromisoformat(raw) if raw else min(end, session.open_utc if session else end)
        if last >= end:
            return None, False
        replayed = end - last > REPLAY_GAP
        return await monitor_tick(self.ctx, last, end, self.sessions, replayed), replayed

    async def _reports(self, now: datetime, session: Session) -> None:
        due = [t for t in report_slots(session, self.ctx.cfg.settings) if t <= now and not self._done("report", t)]
        if not due:
            return
        model = self.ctx.cfg.settings.analyst_model
        await build_report(self.ctx.data, self.ctx.clients.get(model), self.ctx.budget, self.ctx.cfg,
                           self.ctx.db, now, self.ctx.fundamentals)
        for t in due:
            self._mark("report", t)

    async def _cycles(self, now: datetime, session: Session) -> None:
        due = [t for t in cycle_slots(session, self.ctx.cfg.settings) if t <= now and not self._done("cycle", t)]
        if not due:
            return
        for old in due[:-1]:
            self._mark("cycle", old)  # missed during downtime; only the latest slot runs
        slot = due[-1]
        prices: dict[str, float] = {}

        async def snapshot():  # one price snapshot for all arms: slow LLM calls must not skew it
            prices.update(await self.ctx.data.latest_prices(list(self.ctx.cfg.settings.universe)))
        if not await self._guard("cycle prices", snapshot()):
            return
        ok = True
        for arm in self.ctx.cfg.arms:
            if arm.enabled:
                ok &= await self._guard(f"cycle {arm.name}", run_cycle(self.ctx, arm, slot, "scheduled", prices))
        if ok:
            self._mark("cycle", slot)
            self.cycles_run += 1

    async def _wakes(self, tick) -> None:
        if tick is None or not tick.bars:
            return
        feats = self.ctx.db.latest_report_features()
        for arm in self.ctx.cfg.arms:
            if not arm.enabled or arm.variant != "improved":
                continue
            for bar in tick.bars:
                if wake_due(self.ctx, arm, bar, feats):
                    await self._guard(f"wake {arm.name}", run_cycle(self.ctx, arm, bar.ts, "wake"))
                    break

    async def step(self, now: datetime) -> None:
        db = self.ctx.db
        self._data_failed = False
        db.kv_set("heartbeat", now.isoformat())
        await self._guard("calendar", self._refresh_calendar(now))
        session = self._today(now)
        db.kv_set("market_open", "1" if session and in_session(now, session) else "0")
        if session is not None:
            await self._guard("benchmark", self._benchmark(now, session))
        tick, replayed = None, False
        try:
            tick, replayed = await self._monitor(now, session)
        except DataError as e:
            self._data_failed = True
            log.error("monitor: data error: %s", e)
        except Exception:
            log.exception("monitor failed")
        if session is not None:
            await self._guard("report", self._reports(now, session))
            await self._cycles(now, session)
            if not replayed:  # never wake the LLM on stale catch-up bars
                await self._wakes(tick)
        db.kv_set("data_ok", "0" if self._data_failed else "1")

    async def run_forever(self, poll_s: float = 20) -> None:
        server_task = None
        if self.serve_dashboard:
            from stockbot.dashboard.app import serve
            server_task = asyncio.create_task(serve(self.ctx))
        try:
            while True:
                try:
                    await self.step(self.ctx.clock.now())
                except Exception:  # e.g. DB locked: next pass retries; never die unattended
                    log.exception("daemon step failed")
                await asyncio.sleep(poll_s)
        finally:
            if server_task:
                server_task.cancel()
