# Price history + Jimbo chart tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily history since 2016 in DB, dashboard week/month + long charts, optional chart-image step for `deepseek-improved`.

**Architecture:** `daily_bars` table filled from Alpaca SIP by `history.sync`, run by daemon. `history.aggregate` makes week/month. `charts.render` draws PNG. `agent/chart_tool.py` = step like `research.py`; images ride new `images` arg through `Budget.complete` to clients.

**Tech Stack:** Python 3.12, SQLite, httpx, FastAPI, matplotlib (new), lightweight-charts.

**Spec:** `docs/superpowers/specs/2026-10-25-price-history-charts-design.md`

## Global Constraints

- Baseline arm prompts byte-identical; only arms with `charts: true` see tool.
- History source `feed=sip`, `adjustment=split`; fetch end ≤ now − 16 min.
- `history_start: 2016-01-01`, `chart_max_per_decision: 3`.
- Chart tool shows only days < ET date of decision time.
- Edit files with Edit tool (mixed LF/CRLF). Commit msg 5 words, no trailer.
- Tests: `.venv/Scripts/python.exe -m pytest -q`.

## Review Focus

- Live DB already holds rows from older code: `CREATE TABLE IF NOT EXISTS` only; no migration break. (Task 1 test opens DB twice.)
- Alpaca down at daemon start: sync fails, decision cycles still run, retry ≥30 min later not every 20 s. (Task 3 test.)
- Bot sends `{"chart": {...}}` single object not list: accepted. (Task 5 test.)
- Bot asks symbol outside universe / `end` before `start` / tf `1Min`: note text, other charts still drawn. (Task 5 test.)
- Dashboard `tf=1Week` with empty `daily_bars` (fresh install / replay): returns live bars or empty, no 500. (Task 4 test.)

---

### Task 1: History store + aggregation

**Files:** Modify `src/stockbot/db.py`; Create `src/stockbot/history.py`; Modify `src/stockbot/data/alpaca.py`, `src/stockbot/config.py` (Settings `history_start: date = date(2016, 1, 1)`); Test `tests/test_history.py`.

**Produces:**
- Schema `daily_bars(symbol TEXT, day TEXT, open, high, low, close, volume REAL, PRIMARY KEY(symbol, day))`.
- `DB.upsert_daily(bars: list[Bar]) -> None`, `DB.daily_bars(symbol, start: date, end: date) -> list[Bar]` (start ≤ day < end, oldest first, `ts` = ET midnight as aware UTC), `DB.last_daily(symbol) -> Bar | None`, `DB.delete_daily(symbol)`.
- `AlpacaData.daily_history(symbol, start: datetime, end: datetime) -> list[Bar]` = `bars` with `feed=sip` (add `feed` kwarg to private fetch, default `iex`).
- `history.aggregate(bars: list[Bar], tf: Literal["1Day","1Week","1Month"]) -> list[Bar]`: ISO week / calendar month; o first, h max, l min, c last, v sum, ts first bar's ts.
- `async history.sync(db, source, symbols, start: date, end: datetime) -> int` (bars written). Per symbol: none stored = fetch `start..end`; else refetch from last stored day; last day close differs >1% = `delete_daily` + full refetch (split).

- [ ] Tests: `test_daily_roundtrip_and_reopen` (upsert, reopen DB, read range half-open), `test_aggregate_week_month` (5 days Mon–Fri + next Mon → 2 weeks, ohlcv exact; Jan 30–Feb 2 → 2 months), `test_sync_fills_gap` (fake source, 2nd sync fetches only from last day), `test_sync_split_refetches` (last close ×0.5 in source → full refetch, old rows gone), `test_alpaca_daily_history_sip` (respx: query has `feed=sip`).
- [ ] Run, FAIL. Implement. Run, PASS. Full suite PASS.
- [ ] Commit `Add daily history store sync`.

### Task 2: Chart renderer

**Files:** Create `src/stockbot/charts.py`; `pyproject.toml` (`uv add matplotlib`); Test `tests/test_charts.py`.

**Produces:** `charts.render(bars: list[Bar], title: str, log: bool = False) -> bytes` — Agg backend, 1024×512 px, close line, y label `price ($)`, date x axis (`AutoDateLocator` + `ConciseDateFormatter`), grid, title. Raises `ValueError` on empty bars.

- [ ] Tests: PNG magic `\x89PNG`, size 1024×512 (read IHDR), log flag sets `yscale log` (render twice, bytes differ), empty → ValueError.
- [ ] FAIL, implement, PASS. Commit `Add trajectory chart PNG renderer`.

### Task 3: Daemon sync + wiring

**Files:** Modify `src/stockbot/agent/cycle.py` (`AppContext.history: HistorySource | None = None`), `src/stockbot/daemon.py`, `src/stockbot/cli.py` (live run passes `_alpaca()` instance as history; replay None); Test `tests/test_daemon.py`.

**Produces:** `history.HistorySource` Protocol (`daily_history`). `Daemon._history(now, session)`: `last_complete` = today ET if `session` and `now >= close_utc + 16 min`, else yesterday ET. Skip when kv `history_synced` == `last_complete.isoformat()` or `now < self._history_retry`. Sync end = `min(ET midnight(last_complete+1) as UTC, now − 16 min)`. Success: set kv. Failure (any Exception): log once, `_history_retry = now + 30 min`. Runs before cycles in `step`, inside `_guard`-like try so cycles still run.

- [ ] Tests: `test_history_sync_once_per_day` (two steps same day → one sync), `test_history_failure_backoff` (source raises → no retry 1 min later, retry after 31 min; cycles still ran), `test_history_none_in_replay` (ctx.history None → no error).
- [ ] FAIL, implement, PASS. Commit `Sync price history in daemon`.

### Task 4: Dashboard week/month + long range + chart endpoint

**Files:** Modify `src/stockbot/dashboard/app.py`, `templates/arm.html`, `static/app.js`; Test `tests/test_dashboard.py`.

**Produces:**
- `/api/candles` `tf` ∈ `1Min,1Hour,1Day,1Week,1Month`. Day+: `db.daily_bars(sym, history_start, tomorrow)` + live IEX `1Day` bars after last stored day (DataError → stored only, `error` set) → `aggregate(tf)`.
- `GET /api/chart?symbol&tf&start&end&log` → `image/png` via `charts.render` from `daily_bars` + `aggregate`; 404 bad symbol/no bars.
- JS: tick format date-only for day+ tf; decision detail scans prompt for chart spec lines (Task 5 format) and adds `<img src="/api/chart?...">` under Prompt.
- arm.html select gains `1Week`, `1Month`.

- [ ] Tests: `test_candles_week_from_history` (seeded daily → weekly bars), `test_candles_week_empty_history` (no rows, no data → 200, bars []), `test_chart_png` (200, png content-type), `test_chart_bad_symbol_404`.
- [ ] FAIL, implement, PASS. Commit `Dashboard week month history charts`.

### Task 5: LLM images + chart step for Jimbo

**Files:** Modify `src/stockbot/llm/base.py`, `openai_compat.py`, `anthropic_client.py`, `fake.py`, `budget.py`, `config.py`, `agent/prompts.py`, `agent/cycle.py`, `config/arms.yaml`, `config/settings.yaml`, `tests/config/arms.yaml`; Create `src/stockbot/agent/chart_tool.py`; Test `tests/test_llm.py`, `tests/test_chart_tool.py`, `tests/test_cycle.py`, `tests/test_prompts.py`, `tests/test_config.py`.

**Produces:**
- `complete(system, user, json_mode=True, images: tuple[bytes, ...] = ())` on `LLMClient`, all clients, `Budget.complete`. openai_compat: images present → user content `[{"type":"text",...}, {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}...]`; absent → plain string (unchanged body). Anthropic: `{"type":"image","source":{"type":"base64","media_type":"image/png","data":...}}` blocks. FakeLLM appends to `self.images`.
- `ArmCfg.charts: bool = False`; ConfigError `charts needs an LLM arm`. `Settings.chart_max_per_decision: int = 3`. `charts: true` on `deepseek-improved` in both arms.yaml.
- `chart_tool.Spec(symbol, tf, start: date, end: date, log: bool)`. `chart_tool.request(reply) -> list[dict] | None`: obj has `"chart"` and no `"decisions"` → list (single dict wrapped); else None. `chart_tool.Session(db, s: Settings, now)`: `request(reply)` raises ParseError on second chart step; `run(raw: list) -> tuple[str, tuple[bytes, ...]]`. Clamp: `end` default/after = `et_date(now)`, days < `et_date(now)` only; `start` < `history_start` → `history_start`; first `chart_max_per_decision` items only. Text block per chart: `# CHART image {n}: {SYM} {tf} {start}..{end} {linear|log}`; skipped = `# CHART {n} skipped: <reason>`; ends `"\n\nReply with the decision JSON."`
- prompts: `system_prompt(variant, s, search=False, charts=False)`; `_CHARTS` text (once, ≤ N charts, `{"chart": [{"symbol","tf": 1Day|1Week|1Month,"start","end","log"}]}`, shows trajectory not exact levels, optional).
- cycle: chart session when `arm.charts`; `turn` checks chart request before search; images accumulate and pass on every later `ask`; purpose `chart_step`.

- [ ] Tests: openai body with/without images (respx), fake records images; `test_request_*` (list, single dict, decision → None, second step ParseError); `test_run_clamps_and_skips` (bad symbol, 1Min, end<start, cap 3, today's bar excluded); cycle `test_chart_then_decide` (images passed on 2nd call, purpose list `["decision","chart_step"]`, prompt has `# CHART image 1`), `test_chart_then_search_then_decide`; `test_baseline_prompt_unchanged` (baseline system prompt has no "chart"); config error test.
- [ ] FAIL, implement, PASS. Full suite PASS. Commit `Add chart tool for Jimbo`.

### Task 6: Docs + live check

- [ ] README: one line under arms/search section naming `charts: true` + `history_start`.
- [ ] Live probe: script syncs real DB copy (scratchpad), renders NVDA 1Week PNG, view it; one real DeepSeek call with that PNG, note prompt tokens in spec "Measured".
- [ ] Commit `Document chart tool, measured cost`.
