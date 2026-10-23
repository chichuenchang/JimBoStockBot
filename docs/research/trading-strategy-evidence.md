# Trading strategy evidence (read 2026-10-22)

Extends `related-projects.md`; no repeats. Question: best bot strategy, what fits StockBot.

## LLM agents, new evidence

| Source | Finding that matters |
|---|---|
| Profit Mirage (arXiv 2510.07920) | 5 GPT-4o agents, Nasdaq-100. In training window +30-44%; after cutoff +9-22%; index ~13.5% both. Backtests inside model knowledge window = memory, not skill. |
| Alpha Illusion (arXiv 2605.16895) | Position paper. "Language confidence is not tradable probability." LLM upstream as information interface; calibration, risk, execution in separate modules. |
| Agentic AI Nowcasting (arXiv 2601.11958) | Live, no lookahead by construction: LLM w/ web search scores Russell 1000 daily, Apr 2025-Jan 2026. Top-20 long: FF5+mom alpha 18.4 bp/day, Sharpe 2.43, costs < 10% gross alpha. Alpha only in top tier; bottom ranks = market. Shorts useless. |
| AI-Trader (arXiv 2512.10971) | Live, 6 LLMs, 3 markets. General intelligence ≠ trading skill; risk control decides cross-market robustness. |
| ATLAS (arXiv 2510.15949) | Reflection-based feedback: no systematic gain. Prompt optimisation from windowed P&L (Adaptive-OPRO) beat fixed prompts. Self-reported. |
| Chen et al. (AEA 2026) | LLM forecasts over-extrapolate recent returns; real stocks show short-term reversal. Expect LLM arms to chase. |
| Barrelet agent-alpha-bench | Qwen3-8B +11% on 50 large caps from one near-100%-NAV bet; 25% per-stock cap turned it −4%. |
| TradeRank (2026-10-10) | 69 models, 10 seasons, 2,921 trades, 43.1% model-seasons profitable. Alpha Arena: no Season 2 results public; last = S1.5 (Dec 2025). |

## Rule strategies, evidence for our universe

| Strategy | Evidence | Fit |
|---|---|---|
| Noise-band intraday momentum (Zarattini, Aziz, Barbon 2024, SSRN 4824172) | SPY 2007-2024: 19.6%/yr net, Sharpe 1.33, hit rate ~43% (convex). Signals only at :00/:30. Exit: VWAP or band trailing stop, flat at close. Better when VIX > 20. | QQQ in universe, 1Min bars on hand. Built as control arm. |
| Market intraday momentum (Gao et al. 2018 JFE) | First half-hour return predicts last half-hour, SPY 1993-2013. Li et al. 2022: holds out-of-sample, 16 markets. | Same family as above; subsumed. |
| 5-min ORB, stocks in play (Zarattini 2024, SSRN 4729284) | Edge only on "in play" names (high relative volume); plain ORB weak. | Needs broad universe scan. Skip. |
| PEAD | Martineau 2021: gone for large caps since ~2006. | Skip. |
| Volatility-managed portfolios | Cederburg 2020, DeMiguel 2022: market-timing-by-vol gain fails out of sample. | Skip as timing; ATR stop sizing already scales risk. |
| Momentum overnight split (Lou, Polk, Skouras 2019) | All momentum abnormal return accrues overnight; intraday partly reverses it. | Swing arms holding overnight = right side. Day-flat arms give that up. |

## Built from this

1. `rules-momentum` control arm: noise-band breakout on benchmark, paper rules at our 45-min cadence. Tests LLM arms vs best published simple system, not only vs strawman trend rule. `src/stockbot/agent/momentum.py`. Trades from 5 prior sessions, paper uses 14: first ~2 weeks run on noisier sigma, weigh less.
2. Conviction stored on entry orders/fills; arm page shows R by conviction bucket. Tests Alpha Illusion claim on our own arms: if high conviction ≠ higher R, `size_entry` conviction multiplier is noise.

## Not built, reason

- Universe expansion + LLM ranking (Nowcasting result): edge needs ~1,000 names + web search; 7 names × our budget cannot test it.
- Prompt optimisation (ATLAS): many arm-days per iteration, self-reported gain.
- Leakage guard in code: model cutoffs for Kimi K3 / DeepSeek V4.1 unverified. Rule instead: replay with real LLM on dates before model cutoff = smoke test only, never evidence.

## Social sweep (KnowKing, 2026-10-22)

YouTube, X, TikTok, Bilibili, EN + ZH; Reddit, Zhihu missing (provider errors). Low trust: used only as checklist of pitfalls practitioners name.

Added:
- `expectancy`, `pnl_ex_best` leaderboard columns. Claims: "61% win rate can still lose money"; "+389%… removing its best trades puts it in the red".
- `finished()`, why: its docstring in `src/stockbot/data/base.py`. Bugs it fixed: live report `rel_volume` read partial-day volume (if Alpaca returns forming bar), replay `chg_1d_pct` compared against close 2 sessions back.

Already covered: costs (`slippage_bps`, `costs` column), look-ahead (`RecordedData`), regime switch (`regime_gate`), parameter fishing (control arms run paper params, untuned).

Not built:
- Mean-reversion / grid arm: only evidence one unverified Bilibili sweep (131,441 backtests, 65 survivors); top comment: high win rate = martingale bag-holding. Chen reversal above is cross-sectional, needs more than 7 names.
- 2x-cost stress column: equals `pnl - costs`, both shown.

Removed: nothing; sweep contradicts no component.

## Sources

ssrn.com/abstract=4824172, quantmacro.substack.com/p/paper-review-an-effective-intraday, sciencedirect.com/science/article/abs/pii/S0304405X18301351, arxiv.org/abs/2510.07920, arxiv.org/abs/2605.16895, arxiv.org/abs/2601.11958, arxiv.org/abs/2512.10971, arxiv.org/abs/2510.15949, aeaweb.org/conference/2026/program/paper/zNrQ4Yn6, github.com/Barrelet/agent-alpha-bench, traderank.ai/blog/alpha-arena-alternatives-2026, ssrn.com/abstract=3111607, personal.lse.ac.uk/polk/research/TugOfWar.pdf, sciencedirect.com/science/article/abs/pii/S0304405X2030132X
