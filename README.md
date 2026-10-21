# StockBot

Local LLM paper-trading arena. Alpha Arena replica vs improved harness, several models side by side. Paper money only.
Design: `docs/superpowers/specs/2026-10-21-llm-trading-arena-design.md`. AA teardown: `docs/research/alpha-arena-teardown.md`.

## Setup

```powershell
uv sync
copy .env.example .env   # fill keys: Alpaca paper (data only), Moonshot, DeepSeek
```

Alpaca paper account: email signup, works from Canada. Model ids + prices in `config/models.yaml` unverified — check provider pages.

## Try without spending

```powershell
uv run stockbot record --days 5 --out data/recorded
uv run stockbot replay --data data/recorded --from 2026-10-20 --to 2026-10-20 --fake-llm --db data/replay.db --serve
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
```

DB `data/stockbot.db`, logs `logs/stockbot.log`. Events (FOMC, CPI, earnings) hand-kept in `config/events.yaml`.
