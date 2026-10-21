# StockBot: local LLM trading arena — design

Status: draft 2026-10-21. Background: `docs/research/alpha-arena-teardown.md` (AA = Alpha Arena).

## Intent

Several LLMs trade US stocks side by side, each in its own paper account, run by a daemon on user's Windows PC (Canada). LLM decides. Harness does everything else.
Experiment: AA Season 1.5 replica harness vs our improved harness, same models. Find which LLM + harness actually trades well. Paper money only. User new to stocks: dashboard must explain itself.

## Goals

- Unattended run during US market hours; survives reboot, crash, PC sleep.
- 4 arms at start: {Kimi K3, DeepSeek V4.1 Flash} × {baseline, improved}. More models = config entry.
- Hard LLM budget $100/month.
- Local web dashboard: per-bot P&L, trades, reasoning.
- Honest results: conservative fills, QQQ benchmark, every prompt + reply stored.

## Non-goals (v1)

- Real-money trading. No live broker code path at all. Broker interface exists so a live adapter (Interactive Brokers Canada, or Alpaca once Canada opens, target ~Nov 2026 unconfirmed) can come later.
- Leverage, shorting, options, crypto, perps.
- AA modes Monk / Max Leverage / Situational Awareness (system prompts unpublished; leaderboard-in-prompt caused tilt).
- Backtesting LLMs over history (cost + lookahead leakage).
- Remote access to dashboard.

## Anti-goals (failure even if it runs)

- LLM output can bypass any limit in improved arm.
- Budget silently exceeded.
- Fill model flatters results (fills better than real market would give).
- Dashboard or keys reachable from internet.
- Missed stops while PC off left uncounted.

## Constraints

- Windows 11, Python 3.12. Runs as user process, not admin.
- Alpaca paper account (email only, works from Canada) used for **data only**: 1-min bars, daily/4h bars, latest quotes, news, market calendar. Free IEX feed. Order execution simulated by harness.
- Market hours 09:30–16:00 ET, Alpaca calendar for holidays / half days. All timestamps stored UTC, shown ET.
- Universe: TSLA, QQQ, NVDA, MSFT, AMZN, GOOGL, PLTR (AA list; QQQ replaces NDX). Config list.
- Start NAV $10,000 per arm. Long only, cash only (1x).
- Keys in `.env` (gitignored): `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `MOONSHOT_API_KEY`, `DEEPSEEK_API_KEY`, placeholders for others.

## Architecture

Single Python asyncio process `stockbot`:

```
scheduler ── market report job ──> analyst LLM ──> report (shared)
          ── decision cycle ─────> per arm: build prompt > LLM > parse > validate > sim broker
          ── monitor (1 min) ────> sim broker: stops/targets/fills, equity snapshot
          ── dashboard (FastAPI, 127.0.0.1:8000)
SQLite (one file) holds everything.
```

Startup: Windows Task Scheduler task "at logon" + restart on failure. Builder writes install script.

### Market report (shared, identical for all arms)

Mirrors AA user-prompt sections 0–5 (teardown doc). Drop "Tail" (no perps for stocks).
- Harness computes data deterministically: per ticker 4h + 1h bars, EMA20/50, RSI14, MACD, ATR14, recent swing highs/lows, rel. volume vs 20-day, last-day % change. Alpaca news headlines last 72h. Macro calendar from config file `events.yaml` (user/builder maintains FOMC, CPI, NFP, earnings dates).
- Analyst LLM (DeepSeek V4.1 Flash, config) turns data into sections 0–5 text. Numbers in report must come from computed data; analyst writes interpretation only.
- Schedule: 09:15 ET + 12:30 ET. Decision cycles use latest report.

### Decision cycle

Every 45 min, 09:45–15:45 ET (9/day, config). All arms run same cycle, same report, same prices.
Prompt order: system prompt > report > arm state. Stable prefix first for provider prompt caching.

Arm state: cash, NAV, prices, open positions with exit plan, plus (improved only) journal: last 5 decision summaries, last 10 closed trades with outcome, `notes_to_self`.

Output: AA per-symbol JSON schema (teardown doc), `signal` ∈ `hold` | `buy_to_enter` | `close_position`.
- Baseline: LLM sets `quantity`. Fields as AA.
- Improved: LLM sets `conviction` 0–1 instead of `quantity`; `stop_loss`, `profit_target`, `invalidation_condition` required on entry; top-level `notes_to_self` ≤ 500 chars.

Parse fail: one retry with error message appended. Second fail or timeout (120 s): all hold, logged.

### Baseline vs improved arm

| | baseline (AA replica) | improved |
|---|---|---|
| sizing | LLM quantity, clipped to cash | harness: qty = 1% NAV ÷ (entry − stop) × conviction, max 25% NAV/position |
| stops/targets | checked only at decision time (AA had no resting orders) | monitor checks every minute |
| validation | parse + cash only | full rule set below |
| memory | none | journal + notes_to_self |
| price-level wake | no | yes: extra cycle when price crosses an open stop's invalidation level or a report key level; max 3/day/arm |

Improved rule set, reject entry + log reason when:
- stop ≥ entry or target ≤ entry
- (target − entry) ÷ (entry − stop) < 1.5 after slippage
- stop distance < 0.5 × ATR14(1h)
- open positions would exceed 5, or entries today would exceed 3
- symbol exited < 1 trading day ago
- arm down ≥ 3% today (entries frozen until next day; exits allowed)

### Simulated broker

- Market order fills at next 1-min bar open ± 5 bps slippage (config). Commission 0 (Alpaca-like), config field.
- Stop: bar low ≤ stop then fill at min(stop, bar open). Target: bar high ≥ target then fill at target. Both in one bar: stop wins.
- No fills outside regular hours. Overnight gaps hit stops at open price.
- Restart after downtime: fetch missed 1-min bars, replay monitor chronologically before new cycles. Dashboard marks replayed fills.
- Equity snapshot per minute in market hours.
- Benchmark arm: QQQ buy-and-hold $10k from arena start date, same fill model.

### LLM layer

- Adapter interface: `complete(messages, json_schema) -> text, usage`. Two adapters: OpenAI-compatible (Moonshot, DeepSeek, OpenRouter, Ollama, OpenAI) and Anthropic.
- `models.yaml` entry: `name, adapter, base_url, model, api_key_env, price_in, price_in_cached, price_out, monthly_cap_usd, enabled`. Placeholders `enabled: false` for Claude, GPT, Grok, Qwen, Gemini.
- Arms in `arms.yaml`: `name, model, variant (baseline|improved), enabled`.
- Starting prices (tracker sites, unverified; confirm on official pages): Kimi K3 $3 in / $0.30 cached / $15 out per 1M. DeepSeek V4.1 Flash ~$0.30 / $1.20 peak, half off-peak.
- Cost from provider `usage` × config price, stored per call.

### Budget

- Global cap $100/calendar month; per-model caps in config (start: Kimi $80, DeepSeek $20, includes analyst).
- Estimate: Kimi ~$0.15/decision × 2 arms × 9 × 21 ≈ $57 before caching; DeepSeek arms + reports ≈ $7.
- 90%: dashboard warning. 100%: model calls stop; affected arms switch to hold-only and harness enforces their stated stops/targets every minute.

### Dashboard

FastAPI + server-rendered pages, 127.0.0.1 only, polls every 5 s in market hours. TradingView Lightweight Charts for candles, any chart lib for rest.
- Leaderboard table per arm: NAV, return %, vs QQQ, P&L $, fees+slippage, LLM spend, net after LLM spend, win rate, avg win/avg loss, profit factor, Sharpe (daily returns, √252, rf 0), max drawdown, trade count, % invested.
- Charts: equity curves all arms + QQQ ($ / % toggle), drawdown, daily P&L bars, P&L heatmap arm × symbol.
- Status strip: daemon up, last cycle per arm, data feed ok, budget used vs cap, pause button per arm + global pause.
- Arm page: open positions (entry, current, unrealized, stop, target, invalidation), closed trades (times, prices, hold, P&L, R-multiple, reason), decision feed (summary, expand to full prompt + raw reply), rejected orders with rule, candle chart per symbol with this arm's entries/exits.
- Plain-English tooltip on each metric (user new to stocks).

### Storage

SQLite tables (builder may refine): `arms, reports, decisions (prompt, raw, parsed, tokens, cost), rejections, orders, fills, positions, equity_snapshots, llm_usage, journal`. Keep everything; no pruning v1.

### Position management (Claude's calls, from builder check)

- Fractional shares to 0.001.
- `close_position` = full close. No partial closes v1.
- `hold` may carry new `stop_loss` / `profit_target` / `invalidation_condition`; harness updates exit plan. Improved arm: stop may only move up.
- `is_add`: baseline allowed (AA). Improved: an add counts as an entry, passes the same rules, resized on combined position, 25% NAV cap on total.
- Baseline stops: at each cycle, before calling the LLM, harness closes positions whose price is past stop or target. Guess at AA behaviour (unpublished).
- Paused arm = budget-exhausted arm: no LLM calls, harness enforces stated stops/targets every minute.
- IEX 1-min bars can skip minutes. Missing minute = no trigger; fill at next available bar.

## Testing needs

- Fake LLM adapter (scripted / random valid replies) + replay clock over recorded Alpaca bars, so the full daemon runs end-to-end with $0 spend.
- Unit tests: fill model edge cases (gap through stop, stop+target same bar), improved rule set, sizing, budget cutoff, restart replay.

## Success

- Runs 20 consecutive trading days unattended, no lost decisions besides logged LLM failures.
- Month spend ≤ $100.
- After 1–3 months user can read from dashboard which arm beat QQQ net of LLM cost.

## Left to builder

System prompt + analyst prompt wording (baseline system prompt = our reconstruction, AA's unpublished). Indicator details, swing-level algorithm, page layout, libraries beyond those named, package/dependency tooling, DB schema details, log format.
