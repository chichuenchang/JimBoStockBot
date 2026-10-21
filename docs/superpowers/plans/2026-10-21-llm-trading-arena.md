# LLM Trading Arena Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local Windows daemon running several LLM paper-trading arms (AA-replica vs improved harness) with a localhost dashboard.

**Architecture:** One asyncio process. Alpaca supplies data only; `SimBroker` simulates fills from 1-min bars. Scheduler runs report job, decision cycles, minute monitor. SQLite stores everything. FastAPI dashboard runs in same process.

**Tech Stack:** Python 3.12, uv, httpx, PyYAML, python-dotenv, FastAPI, uvicorn, Jinja2, pytest, pytest-asyncio, respx. No pandas. Frontend: TradingView `lightweight-charts@4.2.3`, `chart.js@4.4.4` from unpkg/jsdelivr.

**Spec:** `docs/superpowers/specs/2026-10-21-llm-trading-arena-design.md`. AA prompt/schema reference: `docs/research/alpha-arena-teardown.md`.

## Global Constraints

- Python `>=3.12,<3.13`. Package `src/stockbot`, entry point `stockbot = "stockbot.cli:main"`.
- Paper only. No code path places real broker orders. Alpaca hosts: `https://data.alpaca.markets`, `https://paper-api.alpaca.markets` only.
- Dashboard binds `127.0.0.1` only, port from settings (default `8000`).
- Timestamps stored UTC ISO-8601; displayed `America/New_York`.
- Universe default: `TSLA, QQQ, NVDA, MSFT, AMZN, GOOGL, PLTR`. Long only, cash only.
- Start NAV `10000`. Slippage `5` bps. Commission `0`.
- Cycles `09:45`–`15:45` ET every `45` min. Reports `09:15`, `12:30` ET.
- Budget: global `$100`/calendar month (ET). Caps: `kimi-k3` `$80`, `deepseek-v4.1-flash` `$20` (includes analyst). Warn at `0.9`.
- Improved rules: risk `1%` NAV/trade × conviction, max `25%` NAV/position, max `5` positions, max `3` entries/day, min R:R `1.5` after slippage, min stop distance `0.5 × ATR14(1h)`, cooldown same trading day, freeze entries at day P&L `≤ -3%`, max `3` wakes/day/arm.
- LLM timeout `120` s, one retry on parse failure, then all-hold.
- Secrets only via `.env`; never logged, never in DB.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `feat/arena-v1` (create before Task 1: `git checkout -b feat/arena-v1`).

## Review Focus

1. LLM reply wraps JSON in prose / code fences, or numbers as strings like `"$182.50"` → parse succeeds, values coerced. Test in Task 8.
2. Half-day session (close 13:00 ET) and DST switch → cycle slots after close dropped, slot times correct in ET. Test in Task 3.
3. Daemon restarts mid-cycle → same (arm, slot) never decided twice. Test in Task 13.
4. Alpaca returns 429/5xx or times out → backoff retry, then step skipped and logged; daemon keeps running. Test in Tasks 11 and 15.
5. Symbol has no 1-min bar for some minutes (sparse IEX) → pending order waits for next bar of that symbol; NAV uses last known close. Test in Task 5.

---

## File Structure

```
pyproject.toml
config/settings.yaml  models.yaml  arms.yaml  events.yaml
.env.example
scripts/install_task.ps1
src/stockbot/
  config.py          load + validate all config
  clock.py           Clock protocol, RealClock, ReplayClock, session/slot logic
  db.py              sqlite schema, connection, repository functions
  models.py          shared dataclasses: Bar, Session, NewsItem, ExitPlan, Position, Fill, AccountState, ClosedTrade
  indicators.py      ema, rsi, macd, atr, swing_levels, rel_volume
  metrics.py         sharpe, max_drawdown, trade_stats, daily_returns
  broker/sim.py      SimBroker
  llm/base.py        Usage, LLMResult, LLMClient, make_client, cost_usd
  llm/openai_compat.py
  llm/anthropic_client.py
  llm/fake.py
  llm/budget.py      Budget
  agent/schema.py    SymbolDecision, Decision, parse_decision
  agent/rules.py     Proposal, RuleContext, check_entry, size_entry, check_stop_update
  agent/prompts.py   system prompts, render_state, build_user_prompt
  agent/cycle.py     AppContext, run_cycle
  data/base.py       MarketData protocol
  data/alpaca.py     AlpacaData
  data/recorded.py   RecordedData (replay/tests)
  report/features.py ticker_features
  report/analyst.py  build_report
  monitor.py         monitor_tick, wake_due
  daemon.py          Daemon
  cli.py             run | replay | record | pause | resume | status
  dashboard/app.py   FastAPI app factory
  dashboard/queries.py leaderboard + chart data from db
  dashboard/glossary.py plain-English metric tips
  dashboard/templates/{base,index,arm}.html
  dashboard/static/app.js
tests/  (mirrors src; fixtures in tests/fixtures/)
```

---

### Task 1: Scaffold + config

**Files:**
- Create: `pyproject.toml`, `.env.example`, `config/*.yaml`, `src/stockbot/__init__.py`, `src/stockbot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `ModelCfg(name: str, adapter: Literal["openai_compat","anthropic","fake"], base_url: str|None, model: str, api_key_env: str|None, price_in: float, price_in_cached: float, price_out: float, monthly_cap_usd: float, enabled: bool)` frozen dataclass. Prices per 1M tokens.
  - `ArmCfg(name: str, model: str, variant: Literal["baseline","improved"], enabled: bool)`
  - `Settings(...)` frozen dataclass, one field per Global Constraints value: `universe: tuple[str,...]`, `start_nav`, `slippage_bps`, `commission_per_order`, `cycle_start: str`, `cycle_end: str`, `cycle_every_min: int`, `report_times: tuple[str,...]`, `analyst_model: str`, `global_cap_usd`, `budget_warn_frac`, `db_path: Path`, `dashboard_port: int`, `risk_per_trade`, `max_position_frac`, `max_positions: int`, `max_entries_per_day: int`, `min_rr`, `min_stop_atr`, `daily_loss_freeze`, `max_wakes_per_day: int`, `llm_timeout_s: float`, `benchmark_symbol: str = "QQQ"`.
  - `Event(day: date, time_et: str|None, name: str, symbols: tuple[str,...])`
  - `AppConfig(settings: Settings, models: dict[str, ModelCfg], arms: tuple[ArmCfg,...], events: tuple[Event,...])`
  - `load_config(config_dir: Path) -> AppConfig`; `api_key(m: ModelCfg) -> str`; `class ConfigError(Exception)`.

- [ ] **Step 1: Create `pyproject.toml`** (hatchling, src layout, deps per Tech Stack, `[tool.pytest.ini_options] asyncio_mode = "auto"`), run `uv sync --python 3.12`. Expected: `.venv` created, no errors.

- [ ] **Step 2: Write config files.**
  - `models.yaml`: enabled `kimi-k3` (adapter `openai_compat`, base_url `https://api.moonshot.ai/v1`, model `kimi-k3`, key `MOONSHOT_API_KEY`, prices `3.0/0.30/15.0`, cap `80`); enabled `deepseek-v4.1-flash` (`https://api.deepseek.com`, model `deepseek-flash`, key `DEEPSEEK_API_KEY`, prices `0.30/0.03/1.20` = peak, conservative, cap `20`); disabled placeholders cap `0`: `claude` (anthropic), `gpt`, `grok`, `qwen`, `gemini` (openai_compat via OpenRouter `https://openrouter.ai/api/v1`, key `OPENROUTER_API_KEY`). Comment at top: model ids + prices unverified, check provider pages.
  - `arms.yaml`: `kimi-baseline`, `kimi-improved`, `deepseek-baseline`, `deepseek-improved`.
  - `events.yaml`: example entries (FOMC, CPI) with comment "user maintains".
  - `.env.example`: `ALPACA_API_KEY=`, `ALPACA_SECRET_KEY=`, `MOONSHOT_API_KEY=`, `DEEPSEEK_API_KEY=`, `OPENROUTER_API_KEY=`, `ANTHROPIC_API_KEY=`.

- [ ] **Step 3: Write failing tests** in `tests/test_config.py`:

```python
def test_loads_repo_config():
    cfg = load_config(Path("config"))
    assert [a.name for a in cfg.arms] == ["kimi-baseline","kimi-improved","deepseek-baseline","deepseek-improved"]
    assert cfg.models["kimi-k3"].monthly_cap_usd == 80
    assert cfg.settings.universe == ("TSLA","QQQ","NVDA","MSFT","AMZN","GOOGL","PLTR")
    assert not cfg.models["claude"].enabled

def test_arm_with_unknown_model_rejected(tmp_path): ...   # raises ConfigError, message names arm
def test_enabled_caps_exceed_global_rejected(tmp_path): ...  # kimi cap 90 + deepseek 20 > 100 -> ConfigError
def test_api_key_missing_env(monkeypatch): ...  # ConfigError mentions env var name, not value
```

- [ ] **Step 4: Run** `uv run pytest tests/test_config.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 5: Implement `config.py`.** `load_dotenv()` inside `api_key` caller path only (not import time).
- [ ] **Step 6: Run tests.** Expected: 4 PASS.
- [ ] **Step 7: Commit** `feat: project scaffold and config loading`.

---

### Task 2: Shared models + database

**Files:**
- Create: `src/stockbot/models.py`, `src/stockbot/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces (`models.py`, dataclasses):
  - `Bar(symbol: str, ts: datetime, open: float, high: float, low: float, close: float, volume: float)` — `ts` = bar start, UTC.
  - `Session(day: date, open_utc: datetime, close_utc: datetime)`
  - `NewsItem(ts: datetime, headline: str, symbols: tuple[str,...], source: str)`
  - `ExitPlan(stop_loss: float|None, profit_target: float|None, invalidation: str)`
  - `Position(symbol, qty: float, entry_price: float, entry_ts: datetime, exit_plan: ExitPlan, risk_per_share: float|None)`
  - `Fill(account: str, symbol: str, side: Literal["buy","sell"], qty: float, price: float, ts: datetime, reason: Literal["entry","add","close","stop","target"], replayed: bool)`
  - `AccountState(account: str, cash: float, positions: dict[str, Position])`
  - `ClosedTrade(account, symbol, entry_ts, exit_ts, entry_price, exit_price, qty, pnl: float, r_multiple: float|None, exit_reason: str, justification: str)`
- Produces (`db.py`): `class DB` wrapping `sqlite3` (WAL, `check_same_thread=False`), `DB.open(path: Path) -> DB` creates schema idempotently. Tables: `accounts, positions, orders, fills, equity_snapshots, reports, decisions, rejections, llm_usage, journal, kv`. Repository methods used by later tasks:
  - `kv_get(key) -> str|None`, `kv_set(key, value)`
  - `ensure_account(account, start_cash)`, `load_account(account) -> AccountState`, `save_account(state)`
  - `insert_order(account, symbol, side, qty, exit_plan, reason, created_ts) -> int`, `pending_orders(symbol) -> list[dict]`, `mark_order_filled(order_id, fill_id)`, `cancel_order(order_id, why)`
  - `insert_fill(fill, justification="") -> int`, `fills(account) -> list[Fill]`, `closed_trades(account) -> list[ClosedTrade]`
  - `snapshot_equity(account, ts, nav)`, `equity_series(account) -> list[tuple[datetime,float]]`
  - `insert_report(ts, text, features_json) -> int`, `latest_report() -> tuple[int, str]|None`
  - `decision_exists(arm, slot_ts) -> bool`; `insert_decision(arm, slot_ts, reason, report_id, prompt, raw, parsed_json, status, input_tokens, cached_tokens, output_tokens, cost) -> int` — UNIQUE(arm, slot_ts).
  - `insert_rejection(arm, decision_id, symbol, rule, detail)`
  - `insert_usage(model, arm|None, purpose, ts, input_tokens, cached_tokens, output_tokens, cost)`, `spent(model|None, month: str) -> float` (`month` = `"YYYY-MM"` ET)
  - `journal_append(arm, ts, kind: Literal["summary","notes"], text)`, `journal_recent(arm, kind, n) -> list[str]`

- [ ] **Step 1: Write failing tests:**

```python
def test_schema_idempotent(tmp_path): DB.open(p); DB.open(p)  # no error
def test_account_roundtrip(tmp_path): ensure_account("a", 10000); state with 1 Position (ExitPlan 98/103) saved and reloaded equal
def test_decision_unique_per_slot(tmp_path): second insert_decision same (arm, slot_ts) raises sqlite3.IntegrityError; decision_exists True
def test_spent_filters_month_and_model(tmp_path): usages 2026-10 kimi 1.5, 2026-10 deepseek 0.5, 2026-11 kimi 9 -> spent("kimi-k3","2026-10")==1.5, spent(None,"2026-10")==2.0
def test_closed_trades_pairs_fills(tmp_path): buy 10 @100 (risk_per_share 2), sell 10 @104 reason "target" -> one ClosedTrade pnl 40, r_multiple 2.0
```

- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -v`. Expected FAIL.
- [ ] **Step 3: Implement `models.py`, `db.py`.** `closed_trades` pairs fills FIFO per (account, symbol); adds average into entry price; `r_multiple = pnl / (risk_per_share × qty)` or None.
- [ ] **Step 4: Run tests.** Expected PASS.
- [ ] **Step 5: Commit** `feat: shared models and sqlite store`.

---

### Task 3: Clock + sessions + slots

**Files:**
- Create: `src/stockbot/clock.py`
- Test: `tests/test_clock.py`

**Interfaces:**
- Consumes: `Session`, `Settings`.
- Produces: `ET = ZoneInfo("America/New_York")`; `class Clock(Protocol): def now(self) -> datetime`; `RealClock`; `ReplayClock(start: datetime)` with `advance(td)` / `set(dt)`; `cycle_slots(session: Session, s: Settings) -> list[datetime]` (UTC); `report_slots(session, s) -> list[datetime]`; `in_session(ts, session) -> bool` (open ≤ ts < close); `month_key(ts) -> str` (ET `"YYYY-MM"`).

- [ ] **Step 1: Write failing tests:**

```python
def test_full_day_has_9_slots(): session 2026-10-21 13:30–20:00 UTC -> slots ET 09:45,10:30,...,15:45 (9 items)
def test_half_day_drops_slots_after_close(): session 2026-11-27 close 18:00 UTC (13:00 ET) -> last slot 12:45 ET, 5 slots
def test_dst_switch(): session 2026-11-02 (EST) -> first slot 14:45 UTC
def test_report_slots(): -> 09:15, 12:30 ET; half day keeps 09:15 and 12:30 (both before 13:00)
def test_month_key_uses_et(): datetime(2026,11,1,3,0,tzinfo=UTC) -> "2026-10"
```

- [ ] **Step 2: Run, expect FAIL. Step 3: implement. Step 4: run, expect PASS.**
- [ ] **Step 5: Commit** `feat: market clock and slot schedule`.

---

### Task 4: Indicators + metrics

**Files:**
- Create: `src/stockbot/indicators.py`, `src/stockbot/metrics.py`
- Test: `tests/test_indicators.py`, `tests/test_metrics.py`

**Interfaces:**
- Produces: `ema(xs: list[float], n: int) -> list[float|None]`; `rsi(closes, n=14) -> list[float|None]` (Wilder); `macd(closes, fast=12, slow=26, signal=9) -> tuple[list, list, list]`; `atr(bars: list[Bar], n=14) -> list[float|None]` (Wilder); `swing_levels(bars, lookback=3) -> tuple[list[float], list[float]]` (highs, lows; pivot = bar high/low extreme vs `lookback` bars each side, newest first, max 3 each); `rel_volume(day_bars: list[Bar], n=20) -> float|None`.
- Produces: `daily_returns(series: list[tuple[datetime,float]]) -> list[float]` (last NAV per ET date, pct change); `sharpe(daily: list[float]) -> float|None` (sample std, ×√252, None if <2 points or std 0); `max_drawdown(navs: list[float]) -> float` (positive fraction); `TradeStats(n, win_rate, avg_win, avg_loss, profit_factor: float|None)`; `trade_stats(trades: list[ClosedTrade]) -> TradeStats`.

- [ ] **Step 1: Write failing tests:**

```python
def test_ema_seed_is_sma(): ema([1,2,3,4,5],3) == [None,None,2.0,3.0,4.0]
def test_rsi_all_up_is_100(): rsi(list(range(1,20)),14)[-1] == 100.0
def test_atr_constant_range(): 20 bars high=low+2, no gaps -> atr[-1] == pytest.approx(2.0)
def test_swing_levels_finds_peak(): highs [1,2,3,9,3,2,1] lookback 3 -> 9 in highs
def test_sharpe(): sharpe([0.01,-0.005,0.02]) == pytest.approx(10.513, rel=1e-3)
def test_sharpe_none_when_flat(): sharpe([0.0,0.0]) is None
def test_max_drawdown(): max_drawdown([100,120,90,130,117]) == pytest.approx(0.25)
def test_trade_stats(): pnls [100,50,-30,-20] -> win_rate .5, avg_win 75, avg_loss -25, profit_factor 3.0
def test_profit_factor_no_losses(): pnls [10] -> profit_factor None
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: indicators and performance metrics`.

---

### Task 5: Simulated broker

**Files:**
- Create: `src/stockbot/broker/__init__.py`, `src/stockbot/broker/sim.py`
- Test: `tests/test_sim_broker.py`

**Interfaces:**
- Consumes: `DB` (Task 2), models.
- Produces: `class SimBroker(db: DB, slippage_bps: float, commission: float)`:
  - `submit_market(account, symbol, side, qty: float|None, exit_plan: ExitPlan|None, reason, ts, risk_per_share: float|None = None, justification="") -> int` — `qty=None` on sell = full close. Queued; fills on next bar of that symbol with `bar.ts > ts`.
  - `on_bar(bar: Bar, enforce_exits: Callable[[str], bool], replayed=False) -> list[Fill]` — order: (1) fill pending orders for `bar.symbol` at `bar.open × (1 ± bps)`; (2) for every account holding `bar.symbol` where `enforce_exits(account)`: stop/target check.
  - `due_exits(account, prices: dict[str,float]) -> list[str]` — symbols whose price ≤ stop or ≥ target (baseline cycle-time check).
  - `update_exit_plan(account, symbol, plan: ExitPlan)`
  - `nav(account, prices: dict[str,float]) -> float`; `last_close(symbol) -> float|None` (kept in memory, persisted via `kv`).
- Fill rules (spec + Claude's call: stop fills also take slippage, conservative):
  - buy fill `open × (1 + bps/1e4)`; qty clipped to `floor_3dp(cash / fill_px)`; qty 0 → cancel order "insufficient_cash".
  - stop: `bar.low ≤ stop` → `min(stop, open) × (1 − bps/1e4)`.
  - target: `bar.high ≥ target` → `target` exactly (limit).
  - both same bar → stop.
  - Bars outside session never reach broker (daemon filters); broker itself does not check.
  - Fractional qty rounded down to 0.001.

- [ ] **Step 1: Write failing tests** (account `a`, cash 10000, bps 5):

```python
def test_market_buy_fills_next_bar_open_with_slippage(): submit buy 10 @10:00; bar 10:01 open 100 -> fill 100.05, cash 8999.5
def test_order_waits_for_symbol_bar(): pending MSFT buy; NVDA bar arrives -> no fill; MSFT bar 10:03 -> fill   # Review Focus 5
def test_buy_clipped_to_cash(): cash 500, buy 10 @ open 100 -> qty 4.997
def test_stop_fill(): stop 98, bar open 99 low 97.5 -> 97.951
def test_gap_through_stop(): stop 98, bar open 96 -> 95.952
def test_target_fill(): target 103, high 103.5 -> 103.0
def test_stop_wins_same_bar(): low ≤ stop and high ≥ target -> reason "stop"
def test_exits_not_enforced_when_flag_false(): enforce_exits returns False -> no stop fill
def test_due_exits(): price 97 with stop 98 -> ["MSFT"]
def test_nav_uses_last_close_when_no_new_bar(): position 10 MSFT, last close 105, no new bar -> nav = cash + 1050   # Review Focus 5
def test_replayed_flag_set(): on_bar(..., replayed=True) -> fill.replayed True
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: simulated broker with conservative fill model`.

---

### Task 6: LLM adapters + cost

**Files:**
- Create: `src/stockbot/llm/__init__.py`, `base.py`, `openai_compat.py`, `anthropic_client.py`, `fake.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `ModelCfg`, `api_key`.
- Produces: `Usage(input_tokens: int, cached_input_tokens: int, output_tokens: int)`; `LLMResult(text: str, usage: Usage, latency_s: float)`; `class LLMError(Exception)`; `class LLMClient(Protocol): async def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResult`; `make_client(cfg: ModelCfg, timeout_s: float, http: httpx.AsyncClient|None = None) -> LLMClient`; `cost_usd(cfg, usage) -> float` = `((in − cached) × price_in + cached × price_in_cached + out × price_out) / 1e6`; `FakeLLM(replies: list[str] | Callable[[str,str], str])`.
- OpenAI-compatible: `POST {base_url}/chat/completions`, messages `[system, user]`, `response_format={"type":"json_object"}` when `json_mode`. Cached tokens from `usage.prompt_tokens_details.cached_tokens` or `usage.prompt_cache_hit_tokens` (DeepSeek), else 0. Text = `choices[0].message.content` (ignore `reasoning_content`).
- Anthropic: implementer loads `claude-api` skill before writing `anthropic_client.py`. Raw httpx to `/v1/messages`; usage from `input_tokens`, `cache_read_input_tokens`, `output_tokens`.
- HTTP 429/5xx/timeout → `LLMError` (no retry here; cycle decides).

- [ ] **Step 1: Write failing tests** (respx mocks):

```python
def test_cost_usd(): kimi cfg, Usage(25000, 20000, 4000) -> (5000*3 + 20000*0.3 + 4000*15)/1e6 == 0.081
async def test_openai_compat_parses_content_and_usage(): mock reply content '{"a":1}', usage prompt 100 completion 20 prompt_cache_hit_tokens 60 -> Usage(100,60,20)
async def test_openai_compat_sends_json_mode_and_bearer(): request body response_format type json_object; header Authorization "Bearer k"
async def test_http_429_raises_llm_error(): ...
async def test_timeout_raises_llm_error(): ...
async def test_fake_llm_cycles_replies(): ...
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: provider-agnostic LLM adapters`.

---

### Task 7: Budget

**Files:**
- Create: `src/stockbot/llm/budget.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: `DB.spent`, `DB.insert_usage`, `AppConfig`, `Clock`, `month_key`, `cost_usd`.
- Produces: `class Budget(db, cfg: AppConfig, clock: Clock)`: `state(model: str) -> Literal["ok","warn","exhausted"]` (exhausted when model spent ≥ cap OR global spent ≥ global cap; warn when either ≥ 0.9 of its cap); `record(model, arm: str|None, purpose: Literal["decision","analyst","retry"], usage: Usage) -> float` (returns cost); `summary() -> dict` (`{model: {spent, cap, state}, "_total": {...}}`) for dashboard.

- [ ] **Step 1: Write failing tests:**

```python
def test_ok_warn_exhausted(): kimi spent 71 -> "ok"; 72 -> "warn"; 80 -> "exhausted"
def test_global_cap_exhausts_all(): kimi 79 + deepseek 21 (global 100) -> both "exhausted"
def test_new_month_resets(): spent in 2026-10, clock 2026-11-02 -> "ok"
def test_record_returns_cost_and_persists(): ...
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: monthly LLM budget guard`.

---

### Task 8: Decision schema parsing

**Files:**
- Create: `src/stockbot/agent/__init__.py`, `src/stockbot/agent/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `SymbolDecision(symbol, signal: Literal["hold","buy_to_enter","close_position"], quantity: float|None, conviction: float|None, stop_loss: float|None, profit_target: float|None, invalidation_condition: str, is_add: bool, justification: str)`; `Decision(per_symbol: dict[str, SymbolDecision], notes_to_self: str|None, ignored_keys: list[str])`; `class ParseError(Exception)`; `parse_decision(text: str, variant: Literal["baseline","improved"], universe: tuple[str,...]) -> Decision`.
- Rules: accept AA shape (top-level dict keyed by symbol; also accept `xyz:` prefix and strip it). Extract first balanced `{...}` if text has prose/fences. Numbers may be strings with `$`, `,`, whitespace → float; `0` stop/target → None (AA convention). Missing universe symbols → `hold`. Non-universe keys → `ignored_keys` (except `notes_to_self`). Unknown `signal` → ParseError. `conviction` clamped [0,1]. `notes_to_self` truncated to 500 chars. Baseline ignores conviction; improved ignores quantity. Entry-field completeness is NOT checked here (rules do it).

- [ ] **Step 1: Write failing tests:**

```python
def test_parses_aa_example(): teardown-doc MSFT example (signal hold, stop 439.455) -> stop_loss 439.455
def test_strips_xyz_prefix(): key "xyz:NVDA" -> per_symbol["NVDA"]
def test_json_in_prose_and_fences(): "Here you go:\n```json\n{...}\n```\nThanks"  # Review Focus 1
def test_string_numbers_coerced(): "stop_loss": "$1,182.50" -> 1182.5   # Review Focus 1
def test_zero_stop_is_none(): ...
def test_missing_symbols_default_hold(): only MSFT given -> 7 entries, others hold
def test_bad_signal_raises(): "signal": "yolo" -> ParseError
def test_not_json_raises(): "I think hold" -> ParseError
def test_notes_truncated(): 900 chars -> 500
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: tolerant decision parser`.

---

### Task 9: Improved rules + sizing

**Files:**
- Create: `src/stockbot/agent/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `Settings`, `Position`.
- Produces: `Proposal(symbol, entry_price: float, stop: float|None, target: float|None, invalidation: str, conviction: float, is_add: bool)`; `RuleContext(nav: float, cash: float, positions: dict[str, Position], entries_today: int, exited_today: set[str], day_pnl_frac: float, atr_1h: dict[str, float|None])`; `check_entry(p, ctx, s) -> str|None`; `size_entry(p, ctx, s) -> float`; `check_stop_update(old: float|None, new: float|None) -> str|None` (`"stop_lowered"` when new < old).
- Rejection codes, checked in this order: `missing_exit_plan` (stop/target None or invalidation blank), `stop_not_below_entry`, `target_not_above_entry`, `rr_below_min`, `stop_too_tight` (skip if ATR None), `max_positions` (adds exempt), `max_entries_per_day` (adds count), `cooldown`, `daily_loss_freeze`, `position_cap_reached`, `zero_size`.
- R:R: `e = entry × (1 + bps/1e4)`, `rr = (target − e) / (e − stop)`.
- Size: `qty = risk_per_trade × nav / (e − stop) × conviction`, then cap so `(existing_qty + qty) × e ≤ max_position_frac × nav`, then cap `qty × e ≤ cash`, floor to 0.001. `check_entry` calls `size_entry` and returns `zero_size` / `position_cap_reached` accordingly.

- [ ] **Step 1: Write failing tests** (nav 10000, cash 10000, bps 5, ATR 1.0 unless stated):

```python
def test_rr_reject(): entry 100 stop 98 target 103 -> "rr_below_min"
def test_rr_pass_and_size_capped(): entry 100 stop 98 target 103.2 conviction 1 -> None; size 24.987 (25% cap: 2500/100.05)
def test_size_risk_based(): entry 100 stop 95 target 110 conviction .5 -> 9.900  (100 / 5.05 × 0.5, floored)
def test_missing_exit_plan(): stop None -> "missing_exit_plan"
def test_stop_too_tight(): ATR 4.0, stop distance 1.5 (< 2.0) -> "stop_too_tight"
def test_max_positions_add_exempt(): 5 positions incl NVDA; new MSFT -> "max_positions"; add NVDA -> not "max_positions"
def test_max_entries_per_day(): entries_today 3 -> "max_entries_per_day"
def test_cooldown(): exited_today {"MSFT"} -> "cooldown"
def test_daily_loss_freeze(): day_pnl_frac -0.03 -> "daily_loss_freeze"
def test_stop_only_moves_up(): check_stop_update(98, 97) -> "stop_lowered"; (98, 99) -> None; (None, 97) -> None
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: improved-arm risk rules and sizing`.

---

### Task 10: Prompts

**Files:**
- Create: `src/stockbot/agent/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `AccountState`, `Settings`.
- Produces: `SYSTEM_BASELINE: str`, `SYSTEM_IMPROVED: str`; `JournalView(summaries: list[str], closed: list[ClosedTrade], notes: str|None)`; `render_state(variant, state: AccountState, prices: dict[str,float], nav: float, now: datetime, journal: JournalView|None) -> str`; `build_user_prompt(report_text: str, state_text: str) -> str` (report first, then state; stable prefix for caching).
- Baseline state uses AA headings verbatim: `# CURRENT AVAILABLE CAPITAL`, `# CURRENT NAV`, `# CURRENT PRICES OF TRADEABLE COINS`, `# CURRENT LIVE POSITIONS AND PERFORMANCE` (positions as AA-style dict lines). Improved adds `# YOUR JOURNAL` (last 5 summaries, last 10 closed trades with P&L and exit reason, notes_to_self) and `# HARNESS RULES` listing every Global Constraints rule with values from `Settings`.
- System prompts: reconstruct AA role (swing trader, universe, long only, cash only, output schema from teardown doc, signals `hold|buy_to_enter|close_position`). Improved: same plus "harness sizes from conviction; entries need stop, target, invalidation; violating entries are rejected". No leaderboard in either.

- [ ] **Step 1: Write failing tests:**

```python
def test_baseline_has_aa_headings(): all 4 headings present; "# YOUR JOURNAL" absent
def test_improved_has_journal_and_rules(): "# YOUR JOURNAL", "# HARNESS RULES", "1.5" and "25%" present
def test_report_comes_first(): build_user_prompt("R", "S").index("R") < .index("S")
def test_no_leaderboard(): "STATE OF COMPETITION" not in either system prompt nor state
def test_state_shows_et_time(): now 2026-10-21 14:00 UTC -> "10:00 am ET"
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: baseline and improved prompts`.

---

### Task 11: Market data (Alpaca + recorded)

**Files:**
- Create: `src/stockbot/data/__init__.py`, `base.py`, `alpaca.py`, `recorded.py`
- Test: `tests/test_data.py`, fixtures `tests/fixtures/bars_*.json`

**Interfaces:**
- Produces: `class MarketData(Protocol)` with async `bars(symbol, timeframe: Literal["1Min","1Hour","4Hour","1Day"], start: datetime, end: datetime) -> list[Bar]`, `latest_prices(symbols) -> dict[str,float]`, `news(symbols, since: datetime) -> list[NewsItem]`, `calendar(start: date, end: date) -> list[Session]`; `class DataError(Exception)`.
- `AlpacaData(key, secret, http: httpx.AsyncClient|None=None)`: bars `GET /v2/stocks/{symbol}/bars?timeframe=&start=&end=&feed=iex&adjustment=split&limit=10000` (follow `next_page_token`); latest `GET /v2/stocks/trades/latest?symbols=&feed=iex`; news `GET /v1beta1/news?symbols=&start=&limit=50`; calendar `GET https://paper-api.alpaca.markets/v2/calendar?start=&end=` (open/close are ET local strings → convert to UTC). Headers `APCA-API-KEY-ID`, `APCA-API-SECRET-KEY`. 429/5xx/timeout: 3 retries, backoff 1 s, 2 s, 4 s, then `DataError`.
- `RecordedData(dir: Path, clock: Clock)`: reads `{symbol}_{timeframe}.json`, `news.json`, `calendar.json`; never returns bars with `ts >= clock.now()` (no lookahead).

- [ ] **Step 1: Write failing tests:**

```python
async def test_bars_paginates(): two pages via respx -> concatenated, ts parsed UTC
async def test_calendar_converts_et(): {"date":"2026-11-27","open":"09:30","close":"13:00"} -> close_utc 18:00
async def test_retries_then_data_error(): 429 x4 -> DataError, 4 requests made   # Review Focus 4 (patch sleep)
async def test_retry_succeeds(): 503 then 200 -> bars
async def test_recorded_no_lookahead(): clock 10:00 -> no bar ts ≥ 10:00
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: alpaca data client and recorded replay data`.

---

### Task 12: Market report

**Files:**
- Create: `src/stockbot/report/__init__.py`, `features.py`, `analyst.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `MarketData`, indicators, `LLMClient`, `Budget`, `DB.insert_report`, `AppConfig`.
- Produces: `ticker_features(symbol, bars_1h, bars_4h, bars_1d) -> dict` keys: `last, chg_1d_pct, ema20_4h, ema50_4h, rsi14_1h, macd_hist_1h, atr14_1h, swing_highs, swing_lows, rel_volume, trend_4h` (`"up"|"down"|"sideways"` from close vs ema20/ema50); `render_computed_table(features: dict[str,dict]) -> str`; `async build_report(data: MarketData, llm: LLMClient|None, budget: Budget, cfg: AppConfig, db: DB, now: datetime) -> tuple[int, str]`.
- Report text = analyst output (AA sections 0–5 headings exactly as teardown doc, minus Tail) + `## Computed Data (harness, authoritative)` table appended verbatim. Analyst prompt gets features JSON, news (72 h), events next 10 days from `events.yaml`, and the instruction: use only numbers from the data. If analyst budget exhausted or `LLMError`/empty → report = computed table + events + headlines + line `Analyst unavailable: <reason>`.
- Bar windows: 1h last 10 sessions, 4h last 30 sessions, 1d last 30 sessions.

- [ ] **Step 1: Write failing tests** (RecordedData fixtures + FakeLLM):

```python
def test_features_trend_up(): rising fixture -> trend_4h "up", atr14_1h > 0
async def test_report_appends_computed_table(): FakeLLM returns sections text -> report contains it AND "## Computed Data (harness, authoritative)"
async def test_report_fallback_when_budget_exhausted(): -> "Analyst unavailable: budget" and table present, no LLM call made
async def test_report_records_usage(): budget.record called with purpose "analyst"
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: shared market report with analyst LLM`.

---

### Task 13: Decision cycle

**Files:**
- Create: `src/stockbot/agent/cycle.py`
- Test: `tests/test_cycle.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `AppContext(cfg: AppConfig, db: DB, data: MarketData, broker: SimBroker, budget: Budget, clients: dict[str, LLMClient], clock: Clock)`; `is_paused(db, arm: str) -> bool` (`kv` keys `pause:<arm>`, `pause:_all`); `hold_only(ctx, arm: ArmCfg) -> bool` (paused or budget exhausted); `CycleResult(arm, slot_ts, status: Literal["ok","skipped_duplicate","hold_only","llm_failed","parse_failed"], decision_id: int|None, orders: int, rejections: int)`; `async run_cycle(ctx, arm: ArmCfg, slot_ts: datetime, reason: Literal["scheduled","wake"]) -> CycleResult`.
- Flow:
  1. `decision_exists(arm, slot_ts)` → `skipped_duplicate` (Review Focus 3).
  2. `hold_only` → record decision row status `hold_only`, no LLM call.
  3. Prices via `data.latest_prices`. Baseline: `broker.due_exits` → submit full closes reason `stop`/`target` first.
  4. Build prompt (`latest_report`; journal for improved), call LLM (timeout from settings). `LLMError` → status `llm_failed`, all hold. Parse; on `ParseError` one retry with user prompt + `"\n\nYour previous reply was invalid: <error>. Reply with JSON only."`; second failure → `parse_failed`, all hold. Each call → `budget.record` (`decision` / `retry`).
  5. Apply per symbol: `close_position` → submit sell (no-op if flat). `hold` with plan fields → `update_exit_plan` (improved: `check_stop_update`, rejection `stop_lowered` keeps old stop). `buy_to_enter` → baseline: submit buy `quantity` (no qty → rejection `missing_quantity`); improved: `check_entry` → submit sized buy with `risk_per_share = e − stop`, else `insert_rejection`.
  6. `insert_decision` (status `ok`), journal: summary = first 300 chars of justifications joined; notes → `journal_append(kind="notes")`.
- `day_pnl_frac` = nav now vs last equity snapshot of previous session (else start_nav). `entries_today`, `exited_today` from fills in current ET date.

- [ ] **Step 1: Write failing tests** (tmp DB, RecordedData, FakeLLM):

```python
async def test_duplicate_slot_skipped(): run twice same slot -> second "skipped_duplicate", FakeLLM called once   # Review Focus 3
async def test_hold_only_when_paused(): kv pause:kimi-improved -> "hold_only", no LLM call
async def test_hold_only_when_budget_exhausted(): ...
async def test_parse_retry_then_ok(): replies ["garbage", valid] -> "ok", 2 usage rows (decision, retry)
async def test_parse_fail_twice_all_hold(): -> "parse_failed", no orders
async def test_improved_rejection_logged(): buy without stop -> rejection "missing_exit_plan", no order
async def test_improved_sized_order(): valid buy -> one pending order, qty == size_entry(...)
async def test_baseline_uses_llm_quantity(): buy quantity 3 -> order qty 3
async def test_baseline_due_exit_closed_before_llm(): price below stop -> sell order reason "stop"
async def test_improved_stop_cannot_lower(): hold with lower stop -> rejection "stop_lowered", plan unchanged
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: per-arm decision cycle`.

---

### Task 14: Monitor, wakes, restart replay

**Files:**
- Create: `src/stockbot/monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `AppContext`, `SimBroker.on_bar`, `hold_only`, `in_session`.
- Produces: `async monitor_tick(ctx, start: datetime, end: datetime, sessions: list[Session], replayed: bool) -> list[Fill]` — fetches 1-min bars for universe in `[start, end)`, drops out-of-session bars, feeds broker in ts order (ties by symbol), `enforce_exits(account)` = improved arm OR `hold_only` arm OR benchmark → False only for active baseline arms; snapshots equity for every account at each minute boundary present in bars; persists `kv last_bar_ts`. `wake_due(ctx, arm: ArmCfg, bar: Bar, features: dict) -> bool` — improved arms only, held symbol, when (a) `bar.close ≤ entry − 0.75 × (entry − stop)` or (b) bar crosses (open/close straddle) a `swing_highs`/`swing_lows` level from latest report features; limited `max_wakes_per_day` and ≥15 min since that arm's last decision (`kv wakes:<arm>:<date>`).
- Restart: daemon calls `monitor_tick(ctx, last_bar_ts, now, sessions, replayed=True)` when gap > 2 min.

- [ ] **Step 1: Write failing tests:**

```python
async def test_baseline_stop_not_enforced_intraday(): baseline arm, bar breaches stop -> no fill
async def test_improved_stop_enforced(): improved arm same bar -> stop fill
async def test_out_of_session_bar_ignored(): 08:00 ET bar -> no fill, no snapshot
async def test_restart_replay_marks_fills(): gap 30 min with stop hit inside -> fill.replayed True, last_bar_ts advanced
async def test_wake_limits(): 4 qualifying bars -> wake_due True 3 times
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: minute monitor, wake triggers, restart replay`.

---

### Task 15: Daemon + CLI + end-to-end replay

**Files:**
- Create: `src/stockbot/daemon.py`, `src/stockbot/cli.py`, `scripts/install_task.ps1`
- Test: `tests/test_daemon.py`, `tests/test_e2e_replay.py`

**Interfaces:**
- Consumes: all prior tasks; `dashboard.app.create_app` (Task 16) — daemon starts it only when `serve_dashboard=True`, so Task 15 tests run without it.
- Produces: `class Daemon(ctx: AppContext, serve_dashboard: bool = True)`: `async step(now: datetime) -> None` (one iteration), `async run_forever(poll_s: float = 20)`. `step` order: refresh calendar once per ET day (store sessions); ensure accounts (all enabled arms + `benchmark-QQQ`, buy benchmark with all cash at first session minute); restart replay if needed; `monitor_tick(ctx, last_bar_ts, now − 1 min, sessions, replayed=False)`; due report slots (`kv report:<slot>`); due cycle slots → `run_cycle` for each enabled arm sequentially; wakes → `run_cycle(..., reason="wake", slot_ts=bar.ts)`. Any `DataError`/unexpected exception inside a job → logged to `logs/stockbot.log` (rotating), step continues (Review Focus 4).
- CLI (`argparse`): `stockbot run` (real clock, AlpacaData); `stockbot record --days N --out DIR` (save Alpaca bars/news/calendar for replay); `stockbot replay --data DIR --from DATE --to DATE --fake-llm` (ReplayClock stepped per minute, RecordedData, FakeLLM emitting random valid decisions, temp DB unless `--db`, `--serve` keeps dashboard up after replay ends); `stockbot pause <arm>|--all`, `resume <arm>|--all`, `status` (budget summary, last cycle per arm, open positions count).
- `install_task.ps1`: `Register-ScheduledTask -TaskName StockBot` at logon, action `<repo>\.venv\Scripts\python.exe -m stockbot run`, working dir repo, settings `-RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable`. User runs it; plan does not execute it. Script also prints reminder to disable sleep during market hours.

- [ ] **Step 1: Write failing tests:**

```python
async def test_step_survives_data_error(): data.latest_prices raises DataError -> step returns, error logged, next step runs cycle   # Review Focus 4
async def test_reports_and_cycles_fire_once(): ReplayClock across 09:00–16:10 ET of fixture day -> 2 reports, 9 decisions per arm
async def test_benchmark_buys_at_open(): benchmark-QQQ holds QQQ after first session minute, cash < 1
def test_cli_pause_resume(tmp_path): pause kimi-improved -> kv set; resume -> cleared
async def test_e2e_replay_one_day(): `replay` over fixture day with FakeLLM -> no exceptions, every arm has equity snapshots, decisions rows == 9 per arm, spend recorded
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Commit** `feat: daemon loop, CLI, replay mode, Windows task script`.

---

### Task 16: Dashboard

**Files:**
- Create: `src/stockbot/dashboard/__init__.py`, `app.py`, `queries.py`, `glossary.py`, `templates/base.html`, `templates/index.html`, `templates/arm.html`, `static/app.js`
- Modify: `src/stockbot/daemon.py` (serve via `uvicorn.Server` in same loop, host `127.0.0.1`)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `DB`, metrics, `Budget.summary`, `is_paused`.
- Produces: `create_app(db: DB, cfg: AppConfig, budget: Budget) -> FastAPI`. `queries.leaderboard(db, cfg) -> list[dict]` row keys: `arm, model, variant, nav, return_pct, vs_benchmark_pct, pnl, costs (fees+slippage), llm_spend, net_after_llm, win_rate, avg_win, avg_loss, profit_factor, sharpe, max_drawdown, trades, invested_pct`; benchmark row included, flagged.
- Routes: `GET /` (leaderboard + charts + status strip), `GET /arm/{name}`; JSON: `/api/leaderboard`, `/api/equity` (all accounts, `?mode=usd|pct`), `/api/drawdown`, `/api/daily_pnl`, `/api/heatmap` (arm × symbol realized P&L), `/api/status` (daemon heartbeat from `kv heartbeat`, last cycle per arm, data ok flag `kv data_ok`, budget summary, paused flags), `/api/arm/{name}/positions|trades|decisions|rejections`, `/api/decision/{id}` (full prompt + raw reply), `/api/candles?symbol=&arm=&tf=` (bars from Alpaca via `MarketData` + arm's fills as markers); `POST /api/pause/{arm}`, `/api/resume/{arm}`, `/api/pause_all`, `/api/resume_all`.
- Front end: pages poll JSON every 5 s when `/api/status.market_open`, else 60 s. Equity/drawdown/daily-P&L/heatmap with chart.js; candles with lightweight-charts + markers. Each metric header has `title` tooltip from `glossary.TIPS[key]` (plain English, ≤ 25 words each; every leaderboard key covered). Decision feed: summary + expand to full prompt/raw.
- Key never rendered: test asserts no env value appears in any response.

- [ ] **Step 1: Write failing tests** (FastAPI `TestClient`, seeded tmp DB):

```python
def test_leaderboard_rows(): seeded 2 arms + benchmark -> 3 rows, keys exact list above
def test_vs_benchmark(): arm +5%, benchmark +2% -> vs_benchmark_pct == 3.0
def test_net_after_llm(): pnl 120, llm_spend 20 -> 100
def test_pause_endpoint_sets_kv(): POST /api/pause/kimi-improved -> is_paused True
def test_glossary_covers_all_columns(): set(TIPS) ⊇ leaderboard keys
def test_no_secrets_in_responses(monkeypatch): MOONSHOT_API_KEY="sk-secret" -> "sk-secret" not in any GET route body
def test_index_renders(): GET / -> 200, contains "Leaderboard"
```

- [ ] **Step 2–4:** FAIL, implement, PASS.
- [ ] **Step 5: Manual check:** `uv run stockbot replay --data tests/fixtures/day1 --from 2026-10-20 --to 2026-10-20 --fake-llm --db run.db --serve`, open `http://127.0.0.1:8000`. Expected: leaderboard 5 rows, equity curves drawn, arm page shows decisions and candles with markers.
- [ ] **Step 6: Commit** `feat: localhost dashboard`.

---

### Task 17: First live dry day + README

**Files:**
- Create: `README.md` (caveman ultra per user rule: setup commands, `.env`, `install_task.ps1`, sleep warning, where logs/db live; reference spec for everything else)

- [ ] **Step 1:** User fills `.env` with Alpaca paper keys + provider keys (user does this; never paste keys in chat).
- [ ] **Step 2:** `uv run stockbot record --days 30 --out data/recorded` then `uv run stockbot replay --data data/recorded --from <last trading day> --to <same> --fake-llm`. Expected: runs clean on real recorded data.
- [ ] **Step 3:** Single real-model smoke: `uv run stockbot replay ... --from <day> --to <day>` without `--fake-llm` but `--max-cycles 1`. Expected: 4 decision rows status `ok`, spend < $1 shown in `stockbot status`. (Add `--max-cycles` flag in `cli.py` here; test in `tests/test_daemon.py::test_max_cycles`.)
- [ ] **Step 4:** Commit `docs: README and live smoke flag`.
- [ ] **Step 5:** User runs `scripts/install_task.ps1` and lets daemon trade next session; next day check dashboard.

---

## Self-review notes

- Spec coverage: every spec section maps to a task — report (12), cycle + variants (9, 10, 13), sim broker + replay (5, 14), LLM + budget (6, 7), dashboard (16), storage (2), testing needs (fake LLM 6, replay 11/15), startup (15), success run (17).
- Claude's calls added during planning: stop fills take slippage too; non-universe keys ignored; `0` stop/target read as None (AA convention); wake rule (a)/(b) concrete definition; benchmark account name `benchmark-QQQ`; DeepSeek priced at peak.
