# StockBot

Local LLM paper-trading arena. Alpha Arena replica vs improved harness, several models side by side. Paper money only.
Design: `docs/superpowers/specs/2026-10-21-llm-trading-arena-design.md`. AA teardown: `docs/research/alpha-arena-teardown.md`. Related work + why the improved-arm extras exist: `docs/research/related-projects.md`.
Control arms `rules-trend`, `random-control`: `src/stockbot/agent/control.py`. `rules-momentum` (noise-band intraday momentum on QQQ, why: `docs/research/trading-strategy-evidence.md`): `src/stockbot/agent/momentum.py`; trades only after 5 prior sessions of 1-min bars, so replay needs `record --days` ≥ 6.
Price history since `history_start` (daily, Alpaca SIP) syncs in daemon; arm flag `charts: true` = chart-image tool before deciding: `docs/superpowers/specs/2026-10-25-price-history-charts-design.md`.
Kimi arms: coding-plan key, flat fee (why + probes: `config/models.yaml`). Lower interval burns plan quota. Plan quota polled every 10 min (`src/stockbot/llm/quota.py`); `status` + dashboard show quota and monthly tokens.

## Setup

```powershell
uv sync
copy .env.example .env   # fill keys: Alpaca paper (data only), Moonshot, DeepSeek; Alpha Vantage, AnySearch optional
```

Alpaca paper account: email signup, works from Canada. Model ids + prices in `config/models.yaml` unverified — check provider pages.

## Try without spending

```powershell
uv run stockbot record --days 5 --out data/recorded
uv run stockbot --db data/replay.db replay --data data/recorded --from 2026-10-20 --to 2026-10-20 --fake-llm --serve
```

Real-model smoke test, one cycle, < $1: same `replay` without `--fake-llm`, add `--max-cycles 1`.

## Run live

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
Start-ScheduledTask -TaskName StockBot
```

Dashboard `http://127.0.0.1:8000`. PC must stay awake 09:30–16:00 ET; downtime gets replayed on restart, missed decision slots skipped.

## Control

```powershell
uv run stockbot status
uv run stockbot pause kimi-improved   # or --all; resume same way
uv run stockbot interval 5           # minutes between decisions, live; no arg = show, --reset = settings.yaml
```

DB `data/stockbot.db`, logs `logs/stockbot.log`. Macro events (FOMC, CPI) hand-kept in `config/events.yaml`. Earnings + fundamentals: Alpha Vantage free key, `src/stockbot/data/fundamentals.py`; no key = earnings only from symbol events in `events.yaml`.
