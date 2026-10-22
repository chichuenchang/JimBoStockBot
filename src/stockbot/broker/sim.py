"""Simulated broker. Conservative fills from 1-min bars.

Market orders fill at the next bar open of their symbol, with slippage against us.
Stops fill at min(stop, open) minus slippage; targets fill exactly at target.
A bar that touches both stop and target counts as a stop.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime

from stockbot.db import DB, parse_ts
from stockbot.models import Bar, ExitPlan, Fill, FillReason, Position


def floor3(x: float) -> float:
    return math.floor(x * 1000 + 1e-9) / 1000


def buy_price(price: float, slippage_bps: float) -> float:
    """What a market buy pays: slippage always against us."""
    return price * (1 + slippage_bps / 1e4)


def sell_price(price: float, slippage_bps: float) -> float:
    return price * (1 - slippage_bps / 1e4)


class SimBroker:
    def __init__(self, db: DB, slippage_bps: float, commission: float):
        self.db = db
        self.slippage_bps = slippage_bps
        self.commission = commission
        self._last: dict[str, float] = {}

    def submit_market(self, account: str, symbol: str, side: str, qty: float | None,
                      exit_plan: ExitPlan | None, reason: str, ts: datetime,
                      risk_per_share: float | None = None, justification: str = "") -> int:
        return self.db.insert_order(account, symbol, side, qty, exit_plan, reason, ts,
                                    risk_per_share, justification)

    def update_exit_plan(self, account: str, symbol: str, plan: ExitPlan) -> None:
        st = self.db.load_account(account)
        if symbol in st.positions:
            st.positions[symbol].exit_plan = plan
            self.db.save_account(st)

    def last_close(self, symbol: str) -> float | None:
        if symbol not in self._last:
            v = self.db.kv_get(f"last_close:{symbol}")
            if v is None:
                return None
            self._last[symbol] = float(v)
        return self._last[symbol]

    def nav(self, account: str, prices: dict[str, float]) -> float:
        st = self.db.load_account(account)
        total = st.cash
        for p in st.positions.values():
            px = prices.get(p.symbol) or self.last_close(p.symbol) or p.entry_price
            total += p.qty * px
        return total

    def due_exits(self, account: str, prices: dict[str, float]) -> dict[str, FillReason]:
        """Held symbols whose price has crossed their stop or target, with which one."""
        out: dict[str, FillReason] = {}
        for p in self.db.load_account(account).positions.values():
            px = prices.get(p.symbol)
            if px is None:
                continue
            sl, tp = p.exit_plan.stop_loss, p.exit_plan.profit_target
            if sl is not None and px <= sl:
                out[p.symbol] = "stop"
            elif tp is not None and px >= tp:
                out[p.symbol] = "target"
        return out

    def on_bar(self, bar: Bar, enforce_exits: Callable[[str], bool],
               replayed: bool = False) -> list[Fill]:
        fills: list[Fill] = []
        for o in self.db.pending_orders(bar.symbol):
            if parse_ts(o["created_ts"]) >= bar.ts:
                continue
            # account, fill and order status commit together: a crash can never refill an order
            with self.db.transaction():
                f = self._fill_order(o, bar, replayed)
            if f:
                fills.append(f)
        for account in self.db.holders(bar.symbol):
            if enforce_exits(account):
                with self.db.transaction():
                    f = self._check_exit(account, bar, replayed)
                if f:
                    fills.append(f)
        self._last[bar.symbol] = bar.close
        self.db.kv_set(f"last_close:{bar.symbol}", repr(bar.close))
        return fills

    def _fill_order(self, o: dict, bar: Bar, replayed: bool) -> Fill | None:
        st = self.db.load_account(o["account"])
        sym = o["symbol"]
        pos = st.positions.get(sym)
        if o["side"] == "buy":
            px = buy_price(bar.open, self.slippage_bps)
            affordable = floor3(max(st.cash - self.commission, 0) / px)
            qty = floor3(min(o["qty"] or 0, affordable))
            if qty <= 0:
                self.db.cancel_order(o["id"], "insufficient_cash")
                return None
            st.cash -= qty * px + self.commission
            plan = ExitPlan(o["stop_loss"], o["profit_target"], o["invalidation"])
            has_plan = plan.stop_loss is not None or plan.profit_target is not None
            if pos is None:
                st.positions[sym] = Position(sym, qty, px, bar.ts, plan, o["risk_per_share"])
                reason = "entry"
            else:
                pos.entry_price = (pos.entry_price * pos.qty + px * qty) / (pos.qty + qty)
                pos.qty += qty
                if has_plan:
                    pos.exit_plan = plan
                if o["risk_per_share"] is not None:
                    pos.risk_per_share = o["risk_per_share"]
                reason = "add"
            fill = Fill(o["account"], sym, "buy", qty, px, bar.ts, reason, replayed)
        else:
            if pos is None:
                self.db.cancel_order(o["id"], "no_position")
                return None
            px = sell_price(bar.open, self.slippage_bps)
            qty = pos.qty if o["qty"] is None else min(o["qty"], pos.qty)
            fill = Fill(o["account"], sym, "sell", qty, px, bar.ts, o["reason"], replayed)
            self._reduce(st, sym, qty, px)
        self.db.save_account(st)
        fid = self.db.insert_fill(fill, o["justification"], o["risk_per_share"])
        self.db.mark_order_filled(o["id"], fid)
        return fill

    def _check_exit(self, account: str, bar: Bar, replayed: bool) -> Fill | None:
        st = self.db.load_account(account)
        pos = st.positions[bar.symbol]
        sl, tp = pos.exit_plan.stop_loss, pos.exit_plan.profit_target
        if sl is not None and bar.low <= sl:
            px, reason = sell_price(min(sl, bar.open), self.slippage_bps), "stop"
        elif tp is not None and bar.high >= tp:
            px, reason = tp, "target"
        else:
            return None
        fill = Fill(account, bar.symbol, "sell", pos.qty, px, bar.ts, reason, replayed)
        self._reduce(st, bar.symbol, pos.qty, px)
        self.db.save_account(st)
        self.db.insert_fill(fill)
        return fill

    def _reduce(self, st, sym: str, qty: float, px: float) -> None:
        pos = st.positions[sym]
        st.cash += qty * px - self.commission
        pos.qty = floor3(pos.qty - qty)
        if pos.qty <= 0:
            del st.positions[sym]
