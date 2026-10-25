"""Plain-English tooltips for every dashboard metric (user is new to trading)."""

TIPS = {
    "nav": "Account value now: cash plus what the shares are worth at the latest price.",
    "return_pct": "How much the account grew or shrank since it started, in percent.",
    "vs_benchmark_pct": "Return minus the return of simply buying QQQ and holding. Positive means the bot beat it.",
    "pnl": "Profit or loss in dollars since the start, including open positions.",
    "costs": "Estimated trading costs: price slippage on each order plus any commission.",
    "llm_spend": "Money spent on AI model calls for this bot.",
    "net_after_llm": "Profit or loss after paying for the AI calls. The number that matters.",
    "win_rate": "Share of closed trades that made money, in percent.",
    "avg_win": "Average dollar profit of winning trades.",
    "avg_loss": "Average dollar loss of losing trades.",
    "profit_factor": "Total won divided by total lost. Above 1 means winners outweigh losers.",
    "expectancy": "Average dollar result per closed trade. Win rate says how often a bot wins; this says whether it makes money doing so.",
    "pnl_ex_best": "Closed-trade profit minus the best winner; ignores open positions. Negative while closed trades net positive: one lucky trade carries the result.",
    "sharpe": "Return per unit of day-to-day bumpiness, yearly scale. Above 1 is good, below 0 is losing.",
    "sharpe_ci": "Range the true Sharpe likely sits in (90% bootstrap). Wide range = too few days to judge. Shown after 20 days.",
    "alpha_pct": "Yearly return NOT explained by just riding QQQ. The bot's real skill. Positive is good.",
    "beta": "How much the account moves when QQQ moves. 1 = same as QQQ, 0.5 = half as much, 0 = unrelated.",
    "max_drawdown": "Biggest drop from a previous high, in percent. Shows the worst pain so far.",
    "trades": "Number of finished trades (bought and later sold).",
    "invested_pct": "Share of the account currently in stocks rather than cash.",
    "r_multiple": "Trade result divided by the amount risked. 2R means it made twice what it risked.",
    "unrealized": "Profit or loss on a position still open, at the latest price.",
    "stop_loss": "Price at which the position is sold to cap the loss.",
    "profit_target": "Price at which the position is sold to take profit.",
    "invalidation": "The model's own condition that would prove its idea wrong.",
    "conviction": "How sure the model said it was at entry, 0 to 1. Bigger conviction = bigger position.",
    "avg_r": "Average R of these trades. If the model's confidence means anything, this rises from low to high.",
}

# Stats table columns, grouped money / trading / risk. Model and setup sit on the leaderboard cards instead.
COLUMN_GROUPS = [
    ("", ["arm"]),
    ("Money", ["nav", "return_pct", "vs_benchmark_pct", "pnl", "costs", "llm_spend", "net_after_llm"]),
    ("Trading", ["trades", "win_rate", "avg_win", "avg_loss", "profit_factor", "expectancy", "pnl_ex_best",
                 "invested_pct"]),
    ("Risk", ["sharpe", "sharpe_ci", "alpha_pct", "beta", "max_drawdown"]),
]
LABELS = {
    "arm": "bot", "nav": "value", "return_pct": "return", "vs_benchmark_pct": "vs QQQ",
    "pnl": "P&L", "costs": "costs", "llm_spend": "AI cost", "net_after_llm": "net after AI",
    "win_rate": "win rate", "avg_win": "avg win", "avg_loss": "avg loss", "profit_factor": "profit factor",
    "expectancy": "per trade", "pnl_ex_best": "P&L minus best",
    "invested_pct": "invested", "sharpe": "Sharpe", "sharpe_ci": "Sharpe range", "alpha_pct": "alpha",
    "max_drawdown": "worst drop",
}
