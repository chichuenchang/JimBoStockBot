# Related projects + papers (read 2026-10-21)

Improved arm already has: code-enforced stops, risk sizing, min R:R, cooldown, loss freeze, entry cap, journal, stop-proximity wakes. Below = gaps only.

## Evidence

| Source | Finding that matters |
|---|---|
| LiveTradeBench (UIUC, 21 LLMs, 50 days live) | LMArena score vs trading Spearman ρ ≈ 0.05 stocks. Reasoning models (R1, o3, Qwen3-Thinking) more volatile, no better. CoT/ReAct no reliable gain. No non-LLM baselines, no costs: authors' own caveat. |
| StockBench (20 DJIA, Mar-Jun 2025) | Best +2.4% vs buy-hold +0.4%. Every agent lost to buy-hold in downturns. Removing news or fundamentals cut returns. Thinking models: fewer math errors, more schema errors. More stocks = worse. Kimi-K2 most stable across regimes. |
| KTD-Fin (arXiv 2605.28359, CSI300) | Once ticker/date leakage masked, agent returns mostly market + style beta. Little stock-picking alpha. |
| Alpha Arena S1/S1.5 | Winners ≤667 trades, losers 785-1,474. Grok 4.20 158 trades vs Qwen 1,418 same round. Losses = right pick, oversized, no stop. 2 weeks too short to rank. No S2 results as of Aug 2026. |
| TradingAgents (Tauric) | Analysts, bull/bear debate, risk team, PM. Memory: each decision settled after holding period, return vs benchmark, one-paragraph reflection fed to next PM call. Paper: 3 stocks, Jan-Mar 2024, SR 8.21 AAPL, rule baselines only, no debate ablation, no single-LLM baseline. Repo v0.4.0+ fixed look-ahead (FRED revisions, same-day social, memory settled early, present-day profile) and debate openers fabricating opponent's argument: paper numbers predate fixes. |
| AMA live (2510.11695, Aug-Sep 2025, TSLA/BMRN/BTC/ETH) | TradingAgents-style debate agent TSLA -38.7% to +21.9% by backbone. Single-agent InvestorAgent best TSLA (SR 6.47, GPT-4.1). Architecture moves results more than model. 2 months, 4 assets: no ranking. |
| Debate or Vote (2508.17536, NeurIPS 2025) | 7 NLP benchmarks: majority vote = most of debate's gain. Proof: debate alone martingale, no expected correctness gain. |
| Minority Sentinel (2606.29270) | Heterogeneous 3-LLM debate: LLM-as-judge override of majority = negative net gain. |
| Hetero debate adversarial (2606.19826) | Different-family honest peer cuts harmful revisions 89% to 35%; persuasive wrong peer restores 90%. |
| Agentic Trading survey (2605.19337, 77 studies to 2026-03) | 19 closed-loop trading studies: 2 time-consistent split, 1 cost model, 0 fully reproducible. |
| FinAgent | Low-level reflection +45-101% annual return, -14-44% max DD. Self-reported backtest only. |
| Lopez-Lira (2304.07619 v6) | LLM headline sentiment predicts next-day returns, strongest small caps + negative news; edge shrinking as LLM adoption rises. |
| Hyper-Alpha-Arena, NOFX | Signal-triggered wakes; Go/code layer owns risk limits. No published results. |

## Recommendations, ranked (evidence x cost)

1. **Control arms, $0.** Rules-only arm (trend filter + ATR stop, no LLM) and random-entry arm with improved-arm rules. Separates LLM skill from harness rules. Nobody in list does this.
2. **Alpha/beta vs QQQ** in `metrics.py` + dashboard. Per KTD-Fin, raw return mostly beta.
3. **Regime gate in code.** QQQ below 50d SMA: no new entries or half risk. StockBench downturn result.
4. **Settle + reflect.** On close: one Flash call writes lesson (R, alpha vs QQQ, did invalidation fire). Last N lessons into journal. TradingAgents pattern; one cheap call per trade.
5. **Trailing stop in code.** Move stop to breakeven at +1R, chandelier 3xATR after. Arena losses = gains given back between wakes.
6. **Earnings blackout auto.** `events.yaml` manual today; need earnings-date source. Block entries day before.
7. **Thinking off by default** for decision calls; thinking on = separate arm if wanted.
8. **Fundamentals line per ticker** in report (P/E, rev growth, next earnings). StockBench ablation.

Skip: multi-agent debate / ensemble voting. 5-15x calls per decision vs $100/mo. Recheck 2026-10-23: no trading study ablates debate vs single agent leak-free; general-reasoning evidence says vote ≈ debate, judge can hurt. If tried: 2-model majority vote arm, not bull/bear + judge.

Judge arms on ≥3 months + bootstrap Sharpe CI, not 2 weeks.

## Build notes (2026-10-21, all 8 built)

- DeepSeek V4 thinks by default (effort high): `{"thinking": {"type": "disabled"}}` off. Kimi K3 always thinks; `reasoning_effort: low` = floor. Both via `extra_body` in `config/models.yaml`.
- Finnhub field names not confirmable from docs; Alpha Vantage chosen. AV `OVERVIEW` premium-only since 2026-10 (free key gets `This is a premium endpoint`): overview from yfinance, AV keeps `EARNINGS_CALENDAR`.
- Reflection uses arm's own model, not analyst model: keeps model comparison clean.

## Sources

github.com/LLMQuant/awesome-trading-agents, alphaxiv.org/abs/2510.02209, arxiv.org/abs/2511.03628, arxiv.org/abs/2605.28359, github.com/TauricResearch/TradingAgents, arxiv.org/abs/2402.18485, arxiv.org/abs/2304.07619, github.com/HammerGPT/Hyper-Alpha-Arena, github.com/NoFxAiOS/nofx, arxiv.org/abs/2412.20138, arxiv.org/abs/2510.11695, arxiv.org/abs/2508.17536, arxiv.org/abs/2606.29270, arxiv.org/abs/2606.19826, arxiv.org/abs/2605.19337, github.com/TauricResearch/TradingAgents/releases
