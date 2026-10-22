"""One decision cycle for one arm: prompt, call, parse, validate, submit."""
from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time, timedelta
from typing import Literal

from stockbot.agent.control import random_decision, rules_decision
from stockbot.agent.momentum import momentum_bars, momentum_decision
from stockbot.agent.prompts import JournalView, build_user_prompt, render_state, system_prompt
from stockbot.agent.reflect import reflect_new_trades
from stockbot.agent.rules import Proposal, RuleContext, check_entry, check_stop_update, size_entry
from stockbot.agent.schema import Decision, ParseError, SymbolDecision, parse_decision
from stockbot.broker.sim import SimBroker, buy_price
from stockbot.clock import ET, Clock, et_at, et_date
from stockbot.config import AppConfig, ArmCfg
from stockbot.data.base import MarketData
from stockbot.data.fundamentals import FundamentalsSource, business_days_until, with_earnings
from stockbot.db import DB
from stockbot.llm.base import LLMClient, LLMError
from stockbot.llm.budget import Budget
from stockbot.models import AccountState, ExitPlan, Position
from stockbot.pause import is_paused
from stockbot.report.features import regime

Status = Literal["ok", "skipped_duplicate", "hold_only", "llm_failed", "parse_failed", "apply_failed"]
log = logging.getLogger("stockbot")
RETRY_SUFFIX = "\n\nYour previous reply was invalid: {err}. Reply with JSON only."
RETRY_MARK = "\n\n----- retry reply -----\n\n"
SUMMARY_MAX = 300


@dataclass
class AppContext:
    cfg: AppConfig
    db: DB
    data: MarketData
    broker: SimBroker
    budget: Budget
    clients: dict[str, LLMClient]
    clock: Clock
    fundamentals: FundamentalsSource | None = None


@dataclass(frozen=True)
class CycleResult:
    arm: str
    slot_ts: datetime
    status: Status
    decision_id: int | None
    orders: int
    rejections: int


def hold_only(ctx: AppContext, arm: ArmCfg) -> bool:
    return is_paused(ctx.db, arm.name) or (arm.uses_llm and ctx.budget.state(arm.model) == "exhausted")


def et_midnight(ts: datetime) -> datetime:
    return datetime.combine(ts.astimezone(ET).date(), time(0), tzinfo=ET)


@dataclass
class _Exchange:
    """What a cycle sent its model, got back and paid. Stored: the last prompt sent (the retry
    prompt contains the first) and every reply."""
    prompt: str = ""
    replies: list[str] = field(default_factory=list)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    def transcript(self) -> str:
        return RETRY_MARK.join(self.replies)


def _record(ctx, arm, slot, reason, report_id, ex: _Exchange, decision, status):
    parsed = json.dumps(asdict(decision)) if decision else "{}"
    return ctx.db.insert_decision(arm.name, slot, reason, report_id, ex.prompt, ex.transcript(), parsed, status,
                                  ex.input_tokens, ex.cached_input_tokens, ex.output_tokens, ex.cost)


async def run_cycle(ctx: AppContext, arm: ArmCfg, slot_ts: datetime,
                    reason: Literal["scheduled", "wake"],
                    prices: dict[str, float] | None = None) -> CycleResult:
    """prices: one snapshot shared by every arm of a scheduled slot; fetched when None."""
    db, s = ctx.db, ctx.cfg.settings
    if db.decision_exists(arm.name, slot_ts):
        return CycleResult(arm.name, slot_ts, "skipped_duplicate", None, 0, 0)
    if hold_only(ctx, arm):
        did = _record(ctx, arm, slot_ts, reason, None, _Exchange(), None, "hold_only")
        return CycleResult(arm.name, slot_ts, "hold_only", did, 0, 0)

    now = ctx.clock.now()
    if prices is None:
        prices = await ctx.data.latest_prices(list(s.universe))
    orders = 0
    if not arm.ruled:  # stops not enforced intrabar: checked here instead
        for sym, why in ctx.broker.due_exits(arm.name, prices).items():
            ctx.broker.submit_market(arm.name, sym, "sell", None, None, why, now)
            orders += 1

    report = db.latest_report()
    report_id, report_text = report if report else (None, "# CURRENT STATE OF MARKETS\n(no report yet)")
    state = db.load_account(arm.name)
    nav = ctx.broker.nav(arm.name, prices)
    ex = _Exchange()
    if arm.uses_llm:
        decision, failed = await _llm_decision(ctx, arm, ex, report_text, state, prices, nav, now)
        if failed:
            did = _record(ctx, arm, slot_ts, reason, report_id, ex, None, failed)
            return CycleResult(arm.name, slot_ts, failed, did, orders, 0)
    else:
        decision = await _control_decision(ctx, arm, slot_ts, state, prices)

    rejections: list[tuple[str, str, str]] = []
    status: Status = "ok"
    try:
        orders += _apply(ctx, arm, decision, state, prices, nav, now, rejections)
    except Exception:
        # An LLM call is already paid for: record the decision so the slot is never re-called.
        log.exception("applying decision for %s failed", arm.name)
        status = "apply_failed"
    did = _record(ctx, arm, slot_ts, reason, report_id, ex, decision, status)
    for sym, rule, detail in rejections:
        db.insert_rejection(arm.name, did, sym, rule, detail, now)
    if arm.uses_llm:
        _journal(db, arm, decision, state, now)
    return CycleResult(arm.name, slot_ts, status, did, orders, len(rejections))


def _journal(db: DB, arm: ArmCfg, decision: Decision, state: AccountState, now: datetime) -> None:
    summary = "; ".join(f"{d.symbol} {d.signal}: {d.justification}"
                        for d in decision.per_symbol.values()
                        if d.signal != "hold" or d.symbol in state.positions or d.justification)
    db.journal_append(arm.name, now, "summary",
                      f"{now.astimezone(ET):%m-%d %H:%M} " + (summary or "no positions, all hold")[:SUMMARY_MAX])
    if decision.notes_to_self:
        db.journal_append(arm.name, now, "notes", decision.notes_to_self)


async def _llm_decision(ctx: AppContext, arm: ArmCfg, ex: _Exchange, report_text: str, state: AccountState,
                        prices: dict[str, float], nav: float,
                        now: datetime) -> tuple[Decision | None, Status | None]:
    """Ask the arm's model, retrying once on an unparseable reply. (decision, None) or
    (None, failure status); ex collects the exchange either way."""
    db, s = ctx.db, ctx.cfg.settings
    journal = None
    if arm.variant == "improved":
        await reflect_new_trades(ctx, arm, now)
        notes = db.journal_recent(arm.name, "notes", 1)
        journal = JournalView(db.journal_recent(arm.name, "summary", 5),
                              db.closed_trades(arm.name)[-10:], notes[0] if notes else None,
                              db.journal_recent(arm.name, "lesson", 5))
    system = system_prompt(arm.variant, s)
    user = build_user_prompt(report_text, render_state(arm.variant, state, prices, nav, now, journal, s))
    client = ctx.clients[arm.model]

    async def ask(prompt: str, purpose: str) -> str:
        ex.prompt = prompt
        try:
            res, cost = await ctx.budget.complete(client, arm.model, arm.name, purpose, system, prompt)
        except LLMError as e:
            ex.cost += e.cost
            ex.replies.append(str(e))
            raise
        ex.input_tokens += res.usage.input_tokens
        ex.cached_input_tokens += res.usage.cached_input_tokens
        ex.output_tokens += res.usage.output_tokens
        ex.cost += cost
        ex.replies.append(res.text)
        return res.text

    try:
        try:
            return parse_decision(await ask(user, "decision"), arm.variant, s.universe), None
        except ParseError as e:
            reply = await ask(user + RETRY_SUFFIX.format(err=e), "retry")
        try:
            return parse_decision(reply, arm.variant, s.universe), None
        except ParseError as e:
            db.journal_append(arm.name, now, "summary", f"reply unparseable: {e}"[:SUMMARY_MAX])
            return None, "parse_failed"
    except LLMError:
        return None, "llm_failed"


async def _control_decision(ctx: AppContext, arm: ArmCfg, slot_ts: datetime, state: AccountState,
                            prices: dict[str, float]) -> Decision:
    s = ctx.cfg.settings
    if arm.variant == "momentum":
        sym = s.benchmark_symbol
        today, prior, close = await momentum_bars(ctx.data, sym, ctx.clock.now())
        end = min(close, et_at(et_date(slot_ts), s.cycle_end)) if close else slot_ts
        last_slot = slot_ts + timedelta(minutes=s.cycle_every_min) > end
        return momentum_decision(sym, s.universe, state.positions.get(sym), prices.get(sym), today, prior,
                                 state.cash, s.slippage_bps, last_slot)
    feats, universe = ctx.db.latest_report_features(), s.universe
    if arm.variant == "rules":
        return rules_decision(state.positions, prices, feats, universe)
    rng = random.Random(f"{arm.name}|{slot_ts.isoformat()}")  # same slot, same draws on rerun
    return random_decision(rng, state.positions, prices, feats, universe)


def _rule_context(ctx: AppContext, arm: ArmCfg, state: AccountState, nav: float,
                  now: datetime, prices: dict[str, float]) -> RuleContext:
    db, s = ctx.db, ctx.cfg.settings
    midnight = et_midnight(now)
    today = db.orders_since(arm.name, midnight)
    prev = db.last_snapshot_before(arm.name, midnight) or db.start_cash(arm.name)
    feats = db.latest_report_features()
    bench = s.benchmark_symbol
    risk_off = s.regime_gate and regime(prices.get(bench), (feats.get(bench) or {}).get("sma50_1d")) == "risk_off"
    return RuleContext(
        nav=nav, cash=state.cash, positions=state.positions,
        entries_today=sum(1 for o in today if o["side"] == "buy"),
        # fills catch stop/target exits (no order); orders catch sells not filled yet
        exited_today=db.sold_since(arm.name, midnight) | {o["symbol"] for o in today if o["side"] == "sell"},
        day_pnl_frac=nav / prev - 1 if prev else 0.0,
        atr_1h={k: v.get("atr14_1h") for k, v in feats.items()}, risk_off=risk_off,
        blackout=_blackout(ctx, feats, now))


def _blackout(ctx: AppContext, feats: dict, now: datetime) -> frozenset[str]:
    """Symbols with earnings (report features) or a symbol event (events.yaml) within
    earnings_blackout_days sessions, today included."""
    days = ctx.cfg.settings.earnings_blackout_days
    today = et_date(now)
    return frozenset(sym for e in with_earnings(ctx.cfg.events, feats) for sym in e.symbols
                     if 0 <= business_days_until(today, e.day) <= days)


def _apply(ctx: AppContext, arm: ArmCfg, decision: Decision, state: AccountState,
           prices: dict[str, float], nav: float, now: datetime,
           rejections: list[tuple[str, str, str]]) -> int:
    s, broker = ctx.cfg.settings, ctx.broker
    orders = 0
    rc = _rule_context(ctx, arm, state, nav, now, prices) if arm.ruled else None
    for sym, d in decision.per_symbol.items():
        held = state.positions.get(sym)
        if d.signal == "close_position":
            if held:
                broker.submit_market(arm.name, sym, "sell", None, None, "close", now,
                                     justification=d.justification)
                orders += 1
        elif d.signal == "hold":
            if held:
                _update_plan(ctx, arm, held, d, now, rejections)
        else:
            qty = _enter(ctx, arm, d, held, prices, now, rc, rejections)
            if qty > 0:
                orders += 1
                if rc is not None:
                    rc = _after_entry(rc, d, qty, buy_price(prices[sym], s.slippage_bps), now)
    return orders


def _after_entry(rc: RuleContext, d: SymbolDecision, qty: float, e: float,
                 now: datetime) -> RuleContext:
    """Count an order placed this cycle as if filled, so later entries see it."""
    positions = dict(rc.positions)
    held = positions.get(d.symbol)
    positions[d.symbol] = Position(d.symbol, qty + (held.qty if held else 0), e, now,
                                   ExitPlan(d.stop_loss, d.profit_target, d.invalidation_condition))
    return replace(rc, cash=rc.cash - qty * e, positions=positions, entries_today=rc.entries_today + 1)


def _update_plan(ctx, arm, held, d: SymbolDecision, now, rejections) -> None:
    if d.stop_loss is None and d.profit_target is None and not d.invalidation_condition:
        return
    old = held.exit_plan
    stop = d.stop_loss if d.stop_loss is not None else old.stop_loss
    if arm.ruled and d.stop_loss is not None:
        bad = check_stop_update(old.stop_loss, d.stop_loss)
        if bad:
            rejections.append((d.symbol, bad, f"old {old.stop_loss} new {d.stop_loss}"))
            stop = old.stop_loss
    plan = ExitPlan(stop, d.profit_target if d.profit_target is not None else old.profit_target,
                    d.invalidation_condition or old.invalidation)
    if plan != old:
        ctx.broker.update_exit_plan(arm.name, d.symbol, plan)


def _enter(ctx, arm, d: SymbolDecision, held, prices, now, rc, rejections) -> float:
    """Validate and submit a buy. Returns the quantity ordered (0 when rejected)."""
    s = ctx.cfg.settings
    price = prices.get(d.symbol)
    if price is None:
        rejections.append((d.symbol, "no_price", ""))
        return 0.0
    e = buy_price(price, s.slippage_bps)
    plan = ExitPlan(d.stop_loss, d.profit_target, d.invalidation_condition)
    reason = "add" if held else "entry"
    if not arm.ruled:
        if not d.quantity or d.quantity <= 0:
            rejections.append((d.symbol, "missing_quantity", ""))
            return 0.0
        risk = e - d.stop_loss if d.stop_loss is not None and d.stop_loss < e else None
        ctx.broker.submit_market(arm.name, d.symbol, "buy", d.quantity, plan, reason, now,
                                 risk, d.justification)
        return d.quantity
    p = Proposal(d.symbol, price, d.stop_loss, d.profit_target, d.invalidation_condition,
                 d.conviction)
    bad = check_entry(p, rc, s)
    if bad:
        rejections.append((d.symbol, bad, f"entry {price} stop {d.stop_loss} target {d.profit_target}"))
        return 0.0
    qty = size_entry(p, rc, s)
    ctx.broker.submit_market(arm.name, d.symbol, "buy", qty, plan, reason, now, e - d.stop_loss,
                             d.justification, d.conviction)
    return qty
