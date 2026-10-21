# Alpha Arena teardown (Season 1.5, US stocks)

Source: alphaarena.ai public JSON, read 2026-10-21 via logged-in Chrome page context.
`curl` blocked by Vercel Security Checkpoint — page-context `fetch` works.

## Endpoints

`/api/conversations` (last 100 decisions), `/api/trades` (last 400), `/api/leaderboard`,
`/api/account-totals?lastHourlyMarker=N`, `/api/since-inception-values`, `/api/analytics`.

Conversation fields: `user_prompt`, `llm_response`, `cot_trace`, `cot_trace_summary`,
`competition_id` 1-4, `prompt_name` (`TestPrompt1` all comps), `skill` (`swing_trading`), `model_type`.
System prompt NOT exposed. Mode rules (Monk / Max Leverage) live there.

## Setup

- Venue: Hyperliquid HIP-3 stock perps, prefix `xyz:` (`xyz:XYZ100` = NDX). Trades 24/7 incl. weekends.
- Universe: TSLA, XYZ100, NVDA, MSFT, AMZN, GOOGL, PLTR. $10k each run.
- Leverage cap: 20x XYZ100, 10x single names.
- Fee hurdle quoted in model reasoning: ~0.15% round trip.
- Cadence: 100 decisions / 20 min across 32 runs, so each run every ~6 min.
- Model ids are OpenRouter-style (`qwen/qwen3-max`, `x-ai/grok-4`, `deepseek/deepseek-chat-v3.1`). Winner `mystery-model` = `typhoon` = Grok 4.20.
- 4 comps share one user prompt. Comp 3 adds `# CURRENT STATE OF COMPETITION` (rank, NAV, Sharpe, max DD of all rivals).

## User prompt structure (~21-25k chars)

Upstream research brief, identical for all models. Models only decide, never research.

```
# CURRENT STATE OF MARKETS            (timestamp ET)
0. Context Snapshot (Risk Regime)     regime, macro, "Dog" (spot/NDX) vs "Tail" (HL perp), news freshness
## 1. Raw Data Dashboard (Dog vs. Tail)   per ticker: 4h structure, funding/8h, OI, rel vol, Dog-vs-Tail tag
## 2. Narrative vs. Reality Check     theme, age, price reaction, catalyst risk, state (PRICED IN / ABSORPTION / DIVERGENCE RISK)
## 3. FOMO Map & Catalyst Horizon     macro calendar, thematic risk scans, per-ticker upside-chase / downside-flush levels
## 4. Alpha Setups: Menu of Hypotheses  per ticker A/B/C: view, timeframe, alpha type, edge depth, risk regime, freshness, invalidation, steel-man risk
## 5. Edge Quality Matrix             High conviction / Tactical / No edge + risk-manager note
# CURRENT STATE OF COMPETITION        (comp 3 only)
# CURRENT AVAILABLE CAPITAL / # CURRENT NAV / # CURRENT PRICES OF TRADEABLE COINS
# CURRENT LIVE POSITIONS AND PERFORMANCE   python-dict repr per position incl. exit_plan
```

Season 1 (crypto) prompt differed: raw series (3-min intraday + 4h, EMA20, MACD, RSI7/14, OI, funding), no narrative brief.

## Output schema

Dict keyed by symbol:

```json
{"xyz:MSFT": {"coin": "xyz:MSFT", "signal": "hold", "quantity": 8, "leverage": 10,
  "profit_target": 585.855, "stop_loss": 439.455,
  "invalidation_condition": "Break below ~485 ...", "confidence": 0, "risk_usd": 0,
  "is_add": false, "justification": "..."}}
```

`signal` values seen: `hold`, `buy_to_enter`, `close_position` (shorts via a sell-side enter).

## Harness defects seen in data (our optimisation targets)

- Positions with `stop_loss: 0`, `profit_target: 0`, empty invalidation. `sl_oid: -1`, so no resting stop orders. Exits only happen when the LLM wakes.
- `confidence` / `risk_usd` come back 0. Model echoes the position dict instead of computing them.
- No sanity check: Claude long PLTR entry 179.94, target 179.36 (below entry). Winner's GOOGL short invalidation "4H close above ~320" with price already 320.1.
- No exposure cap: winner held PLTR notional $120.5k on $16.2k NAV (7.5x NAV). Winner's +34.6% = mostly this single bet.
- Comp 3 standings caused tilt: trailing models opened 10-15x "catch-up" trades. Grok-4 ended at $385.
- Decisions ran on weekends while underlying cash market closed.

## Final leaderboard facts

Top 6 runs: ≤667 trades each. Bottom runs: 785-1,474 trades, fees up to $4.4k on $10k. Win rate 26-41% for every run, so no separation there.
Full table: alphaarena.ai/leaderboard.
