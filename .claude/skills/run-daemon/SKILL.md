---
name: run-daemon
description: Start the live StockBot daemon in a separate console window.
disable-model-invocation: true
---

Run from repo root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_daemon.ps1
```

Report script output. Exit 1 = daemon already running; do not start another. Live run spends real LLM budget.
