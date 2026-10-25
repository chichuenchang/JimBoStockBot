# Price history since 2016 + chart tool for Jimbo — design

Status: draft 2026-10-25. Base: `2026-10-21-llm-trading-arena-design.md`, step protocol from `2026-10-23-search-5min-design.md`.

## Intent

Bots see only indicator snapshots (report features) and recent bars. No sense of long-run trajectory. Give Jimbo (`deepseek-improved`) optional tool: pick symbol + range, get picture of past trajectory. Shape, not precise numbers (user). Dashboard gets same history.

## Goals

- Dashboard price chart: `1Week`, `1Month` added. `1Day/1Week/1Month` reach back to 2016-01-01, all universe symbols.
- Daily history stored locally, kept current.
- Jimbo may request trajectory chart(s) before deciding. Chart = PNG, price on y axis, dates on x axis. Sent as image.

## Non-goals

- Chart tool for `deepseek-baseline` or any other arm (user). Baseline untouched.
- Raw history numbers in prompt text. Picture only (user).
- Intraday (`1Min/1Hour`) history before live-feed window.
- Model-native tool calling.

## Anti-goals

- Chart shows data after decision time (lookahead in replay/tests).
- Chart failure kills decision. Fail = text note, decide without.
- Baseline prompt or cost changes.

## Measured (probes 2026-10-25)

- Alpaca `feed=iex` daily NVDA earliest bar 2020-07-27. No 2016.
- `feed=sip`, `adjustment=split`: 2016-01-04 onward, `1Day/1Week/1Month` all HTTP 200 on our free key.
- DeepSeek `deepseek-flash` (`/models`: `input_modalities: ["text","image"]`): OpenAI-style `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}` part works. 64×64 PNG + short text = 197 prompt tokens.
- Built (2026-10-25): full backfill 22 symbols = 58,602 bars, 3.3 s. PLTR starts 2020-09-30 (IPO). 1024×512 chart ≈ 330 prompt tokens each (87 text-only, 407 one image, 748 two). DeepSeek described NVDA log-weekly + PLTR daily trajectories correctly (trend, 2022 drawdown, range position).
- SIP split-day junk: NVDA 2024-06-10 high 195.95 vs close 121.79; TSLA 2021-03-04 high 291.31 vs close 207.15. Only 2 of 58,602 bars; shows as dashboard candle wick only, bot chart uses closes.
- Chart `end` inclusive everywhere (bot, spec line, `/api/chart`); bot charts stop at yesterday (today's daily bar holds the close = lookahead).

## Design

### History store

- Table `daily_bars(symbol, day, open, high, low, close, volume, PRIMARY KEY(symbol, day))`.
- Source: Alpaca `feed=sip`, `adjustment=split`, `1Day`. Settings `history_start: 2016-01-01`.
- Backfill on daemon start for missing symbols/days; refresh after each close. End = now − 16 min (free plan: SIP blocked last 15 min).
- Split guard: on refresh, refetch last stored day; close differs >1% = refetch symbol's full history.
- Week/month bars aggregated locally from daily (o=first open, h=max, l=min, c=last close, v=sum; week = ISO week, month = calendar month).

### Dashboard

- `tf` select: `1Min, 1Hour, 1Day, 1Week, 1Month`.
- `1Day/1Week/1Month` read `daily_bars` from `history_start`, plus today's partial bar from live IEX during session. `1Min/1Hour` unchanged (`CANDLE_WINDOW`).
- Candles + trade markers as today. Tick format shows date (not hh:mm) for day+ timeframes.
- Decision detail renders charts Jimbo saw (re-drawn from spec in prompt via `/api/chart`).

### Chart tool (per-arm flag)

- `arms.yaml`: `charts: true` on `deepseek-improved` only. `ArmCfg.charts: bool = False`. Config error on non-LLM arm.
- Settings: `chart_max_per_decision: 3`.
- Step (same stateless protocol as search; independent of search/read order, once per decision):
  `{"chart": [{"symbol": "NVDA", "tf": "1Week", "start": "2019-01-01", "end": "2026-10-24", "log": false}, ...]}`
  - `tf` ∈ `1Day/1Week/1Month`. `end` omitted or later = yesterday (no lookahead). `start` omitted = `end` − 1 year; clamped to `history_start`. `log` optional, default false.
  - Symbol outside universe / bad dates = that chart skipped with text note. Extra charts beyond cap ignored.
- Harness appends text block (spec of each chart, "image N") + PNG(s) as image parts on the user message. Prior prompt prefix unchanged so cache holds.
- Renderer `stockbot/charts.py`: matplotlib Agg, close-price line, labelled price y axis + date x axis, title `SYMBOL tf start–end`, ~1024×512. New dep `matplotlib`.
- Stored: appended text block (specs) in `decisions.prompt`; PNG not stored.

### LLM interface

- `LLMClient.complete(system, user, json_mode=True, images: list[bytes] = ())`. `openai_compat` sends content-part list when images present. `anthropic` adapter: image blocks too (cheap to add). Fake: records images. `Budget.complete` passes through.

### Prompt (chart arm only)

System prompt addition: may request up to N history charts once before deciding; range + timeframe free; charts show trajectory, not exact levels; optional.

## Tests

- Aggregation week/month; split-guard refetch; backfill gap fill (fake data).
- Step parse: valid, bad symbol, cap, end clamp, order with search.
- Baseline system prompt + user prompt byte-identical before/after.
- Renderer returns PNG for fake bars; empty bars = note.
- openai_compat body with images; dashboard `tf=1Week` returns aggregated bars.

## Builder's calls (not user-decided)

- Line of closes, not candles, in bot picture ("trajectory").
- Cap 3 charts/decision, one chart step/decision.
- Bot may pick log scale.
- matplotlib over hand-drawn Pillow.
- Week/month aggregated locally, not fetched.
- PNG not stored; re-rendered for dashboard.
