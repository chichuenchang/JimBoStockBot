"""SQLite store. One file, WAL mode. Timestamps stored as UTC ISO-8601 text."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stockbot.models import AccountState, ClosedTrade, ExitPlan, Fill, Position

_ET = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (account TEXT PRIMARY KEY, cash REAL NOT NULL, start_cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS positions (
  account TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, entry_price REAL NOT NULL,
  entry_ts TEXT NOT NULL, stop_loss REAL, profit_target REAL, invalidation TEXT NOT NULL DEFAULT '',
  risk_per_share REAL, PRIMARY KEY (account, symbol));
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, account TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL,
  stop_loss REAL, profit_target REAL, invalidation TEXT NOT NULL DEFAULT '', risk_per_share REAL,
  reason TEXT NOT NULL, justification TEXT NOT NULL DEFAULT '', created_ts TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', fill_id INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS orders_pending ON orders(symbol, status);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY, account TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
  qty REAL NOT NULL, price REAL NOT NULL, ts TEXT NOT NULL, reason TEXT NOT NULL,
  replayed INTEGER NOT NULL DEFAULT 0, justification TEXT NOT NULL DEFAULT '', risk_per_share REAL);
CREATE TABLE IF NOT EXISTS equity_snapshots (
  account TEXT NOT NULL, ts TEXT NOT NULL, nav REAL NOT NULL, PRIMARY KEY (account, ts));
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, text TEXT NOT NULL, features_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, arm TEXT NOT NULL, slot_ts TEXT NOT NULL, reason TEXT NOT NULL, report_id INTEGER,
  prompt TEXT NOT NULL, raw TEXT NOT NULL, parsed_json TEXT NOT NULL, status TEXT NOT NULL,
  input_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  cost REAL NOT NULL, UNIQUE (arm, slot_ts));
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY, arm TEXT NOT NULL, decision_id INTEGER, symbol TEXT NOT NULL,
  rule TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY, model TEXT NOT NULL, arm TEXT, purpose TEXT NOT NULL, ts TEXT NOT NULL,
  month TEXT NOT NULL, input_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL, cost REAL NOT NULL);
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, arm TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


class DB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @classmethod
    def open(cls, path: Path) -> DB:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def _q(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def _x(self, sql: str, *args) -> int:
        return self.conn.execute(sql, args).lastrowid

    # kv
    def kv_get(self, key: str) -> str | None:
        rows = self._q("SELECT value FROM kv WHERE key=?", key)
        return rows[0]["value"] if rows else None

    def kv_set(self, key: str, value: str) -> None:
        self._x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                key, value)

    def kv_delete(self, key: str) -> None:
        self._x("DELETE FROM kv WHERE key=?", key)

    # accounts
    def ensure_account(self, account: str, start_cash: float) -> None:
        self._x("INSERT OR IGNORE INTO accounts(account,cash,start_cash) VALUES(?,?,?)",
                account, start_cash, start_cash)

    def accounts(self) -> list[str]:
        return [r["account"] for r in self._q("SELECT account FROM accounts ORDER BY account")]

    def start_cash(self, account: str) -> float:
        return self._q("SELECT start_cash FROM accounts WHERE account=?", account)[0]["start_cash"]

    def load_account(self, account: str) -> AccountState:
        rows = self._q("SELECT cash FROM accounts WHERE account=?", account)
        if not rows:
            raise KeyError(account)
        positions = {
            r["symbol"]: Position(
                r["symbol"], r["qty"], r["entry_price"], parse_ts(r["entry_ts"]),
                ExitPlan(r["stop_loss"], r["profit_target"], r["invalidation"]), r["risk_per_share"])
            for r in self._q("SELECT * FROM positions WHERE account=?", account)}
        return AccountState(account, rows[0]["cash"], positions)

    def holders(self, symbol: str) -> list[str]:
        return [r["account"] for r in self._q(
            "SELECT account FROM positions WHERE symbol=? ORDER BY account", symbol)]

    def save_account(self, state: AccountState) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute("UPDATE accounts SET cash=? WHERE account=?", (state.cash, state.account))
            self.conn.execute("DELETE FROM positions WHERE account=?", (state.account,))
            for p in state.positions.values():
                self.conn.execute(
                    "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?)",
                    (state.account, p.symbol, p.qty, p.entry_price, iso(p.entry_ts),
                     p.exit_plan.stop_loss, p.exit_plan.profit_target, p.exit_plan.invalidation,
                     p.risk_per_share))

    # orders
    def insert_order(self, account: str, symbol: str, side: str, qty: float | None,
                     exit_plan: ExitPlan | None, reason: str, created_ts: datetime,
                     risk_per_share: float | None = None, justification: str = "") -> int:
        plan = exit_plan or ExitPlan(None, None, "")
        return self._x(
            "INSERT INTO orders(account,symbol,side,qty,stop_loss,profit_target,invalidation,"
            "risk_per_share,reason,justification,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            account, symbol, side, qty, plan.stop_loss, plan.profit_target, plan.invalidation,
            risk_per_share, reason, justification, iso(created_ts))

    def pending_orders(self, symbol: str) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT * FROM orders WHERE symbol=? AND status='pending' ORDER BY id", symbol)]

    def orders_since(self, account: str, ts: datetime) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT * FROM orders WHERE account=? AND created_ts>=? AND status!='cancelled' ORDER BY id",
            account, iso(ts))]

    def mark_order_filled(self, order_id: int, fill_id: int) -> None:
        self._x("UPDATE orders SET status='filled', fill_id=? WHERE id=?", fill_id, order_id)

    def cancel_order(self, order_id: int, why: str) -> None:
        self._x("UPDATE orders SET status='cancelled', note=? WHERE id=?", why, order_id)

    # fills
    def insert_fill(self, fill: Fill, justification: str = "",
                    risk_per_share: float | None = None) -> int:
        return self._x(
            "INSERT INTO fills(account,symbol,side,qty,price,ts,reason,replayed,justification,"
            "risk_per_share) VALUES(?,?,?,?,?,?,?,?,?,?)",
            fill.account, fill.symbol, fill.side, fill.qty, fill.price, iso(fill.ts), fill.reason,
            int(fill.replayed), justification, risk_per_share)

    def _fill_rows(self, account: str) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM fills WHERE account=? ORDER BY ts, id", account)

    def fills(self, account: str) -> list[Fill]:
        return [Fill(r["account"], r["symbol"], r["side"], r["qty"], r["price"], parse_ts(r["ts"]),
                     r["reason"], bool(r["replayed"])) for r in self._fill_rows(account)]

    def closed_trades(self, account: str) -> list[ClosedTrade]:
        """A trade opens on the first buy from flat and closes when qty returns to 0."""
        out: list[ClosedTrade] = []
        open_: dict[str, dict] = {}
        for r in self._fill_rows(account):
            sym = r["symbol"]
            if r["side"] == "buy":
                t = open_.setdefault(sym, {"qty": 0.0, "cost": 0.0, "entry_ts": r["ts"],
                                           "risk": r["risk_per_share"], "why": r["justification"],
                                           "sold": 0.0, "proceeds": 0.0})
                t["qty"] += r["qty"]
                t["cost"] += r["qty"] * r["price"]
                continue
            t = open_.get(sym)
            if t is None:
                continue
            t["sold"] += r["qty"]
            t["proceeds"] += r["qty"] * r["price"]
            if t["sold"] >= t["qty"] - 1e-9:
                qty = t["qty"]
                entry = t["cost"] / qty
                exit_ = t["proceeds"] / t["sold"]
                pnl = t["proceeds"] - t["cost"]
                r_mult = pnl / (t["risk"] * qty) if t["risk"] else None
                out.append(ClosedTrade(account, sym, parse_ts(t["entry_ts"]), parse_ts(r["ts"]),
                                       entry, exit_, qty, pnl, r_mult, r["reason"], t["why"],
                                       bool(r["replayed"])))
                del open_[sym]
        return out

    # equity
    def snapshot_equity(self, account: str, ts: datetime, nav: float) -> None:
        self._x("INSERT OR REPLACE INTO equity_snapshots VALUES(?,?,?)", account, iso(ts), nav)

    def equity_series(self, account: str) -> list[tuple[datetime, float]]:
        return [(parse_ts(r["ts"]), r["nav"]) for r in self._q(
            "SELECT ts, nav FROM equity_snapshots WHERE account=? ORDER BY ts", account)]

    def last_snapshot_before(self, account: str, ts: datetime) -> float | None:
        rows = self._q("SELECT nav FROM equity_snapshots WHERE account=? AND ts<? ORDER BY ts DESC LIMIT 1",
                       account, iso(ts))
        return rows[0]["nav"] if rows else None

    # reports
    def insert_report(self, ts: datetime, text: str, features_json: str) -> int:
        return self._x("INSERT INTO reports(ts,text,features_json) VALUES(?,?,?)", iso(ts), text, features_json)

    def latest_report(self) -> tuple[int, str] | None:
        rows = self._q("SELECT id, text FROM reports ORDER BY id DESC LIMIT 1")
        return (rows[0]["id"], rows[0]["text"]) if rows else None

    def latest_report_features(self) -> dict:
        rows = self._q("SELECT features_json FROM reports ORDER BY id DESC LIMIT 1")
        return json.loads(rows[0]["features_json"]) if rows else {}

    # decisions
    def decision_exists(self, arm: str, slot_ts: datetime) -> bool:
        return bool(self._q("SELECT 1 FROM decisions WHERE arm=? AND slot_ts=?", arm, iso(slot_ts)))

    def insert_decision(self, arm: str, slot_ts: datetime, reason: str, report_id: int | None,
                        prompt: str, raw: str, parsed_json: str, status: str, input_tokens: int,
                        cached_tokens: int, output_tokens: int, cost: float) -> int:
        return self._x(
            "INSERT INTO decisions(arm,slot_ts,reason,report_id,prompt,raw,parsed_json,status,"
            "input_tokens,cached_tokens,output_tokens,cost) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            arm, iso(slot_ts), reason, report_id, prompt, raw, parsed_json, status,
            input_tokens, cached_tokens, output_tokens, cost)

    def last_decision_ts(self, arm: str) -> datetime | None:
        rows = self._q("SELECT MAX(slot_ts) m FROM decisions WHERE arm=?", arm)
        return parse_ts(rows[0]["m"]) if rows and rows[0]["m"] else None

    def insert_rejection(self, arm: str, decision_id: int | None, symbol: str, rule: str,
                         detail: str = "", ts: datetime | None = None) -> None:
        self._x("INSERT INTO rejections(arm,decision_id,symbol,rule,detail,ts) VALUES(?,?,?,?,?,?)",
                arm, decision_id, symbol, rule, detail, iso(ts or datetime.now(UTC)))

    # llm usage
    def insert_usage(self, model: str, arm: str | None, purpose: str, ts: datetime,
                     input_tokens: int, cached_tokens: int, output_tokens: int, cost: float) -> None:
        month = ts.astimezone(_ET).strftime("%Y-%m")
        self._x("INSERT INTO llm_usage(model,arm,purpose,ts,month,input_tokens,cached_tokens,"
                "output_tokens,cost) VALUES(?,?,?,?,?,?,?,?,?)",
                model, arm, purpose, iso(ts), month, input_tokens, cached_tokens, output_tokens, cost)

    def spent(self, model: str | None, month: str) -> float:
        if model is None:
            rows = self._q("SELECT COALESCE(SUM(cost),0) s FROM llm_usage WHERE month=?", month)
        else:
            rows = self._q("SELECT COALESCE(SUM(cost),0) s FROM llm_usage WHERE month=? AND model=?",
                           month, model)
        return rows[0]["s"]

    def spent_by_arm(self, arm: str) -> float:
        return self._q("SELECT COALESCE(SUM(cost),0) s FROM llm_usage WHERE arm=?", arm)[0]["s"]

    # journal
    def journal_append(self, arm: str, ts: datetime, kind: str, text: str) -> None:
        self._x("INSERT INTO journal(arm,ts,kind,text) VALUES(?,?,?,?)", arm, iso(ts), kind, text)

    def journal_recent(self, arm: str, kind: str, n: int) -> list[str]:
        rows = self._q("SELECT text FROM journal WHERE arm=? AND kind=? ORDER BY id DESC LIMIT ?",
                       arm, kind, n)
        return [r["text"] for r in reversed(rows)]
