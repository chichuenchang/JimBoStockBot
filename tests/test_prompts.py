from datetime import UTC, datetime

from stockbot.agent.prompts import JournalView, build_user_prompt, render_state, system_prompt
from stockbot.models import AccountState, ClosedTrade, ExitPlan, Position
from tests.snapshot import CFG

S = CFG.settings
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
STATE = AccountState("a", 5000.0, {"MSFT": Position("MSFT", 10, 480.0, NOW, ExitPlan(470, 500, "4h < 470"), 10)})
PRICES = {"MSFT": 483.0, "NVDA": 182.0}
AA_HEADINGS = ["# CURRENT AVAILABLE CAPITAL", "# CURRENT NAV",
               "# CURRENT PRICES OF TRADEABLE COINS", "# CURRENT LIVE POSITIONS AND PERFORMANCE"]


def journal():
    t = ClosedTrade("a", "NVDA", NOW, NOW, 180, 175, 5, -25, -1.0, "stop", "breakout")
    return JournalView(summaries=["held MSFT"], closed=[t], notes="avoid chasing")


def test_baseline_has_aa_headings():
    text = render_state("baseline", STATE, PRICES, 9830.0, NOW, None, S)
    for h in AA_HEADINGS:
        assert h in text
    assert "# YOUR JOURNAL" not in text
    assert "'symbol': 'MSFT'" in text and "'stop_loss': 470" in text


def test_improved_has_journal_and_rules():
    text = render_state("improved", STATE, PRICES, 9830.0, NOW, journal(), S)
    assert "# YOUR JOURNAL" in text and "# HARNESS RULES" in text
    assert "1.5" in text and "25%" in text
    assert "avoid chasing" in text and "NVDA" in text and "stop" in text


def test_report_comes_first():
    p = build_user_prompt("REPORT", "STATE")
    assert p.index("REPORT") < p.index("STATE")


def test_no_leaderboard():
    for v in ("baseline", "improved"):
        assert "STATE OF COMPETITION" not in system_prompt(v, S)
        assert "STATE OF COMPETITION" not in render_state(v, STATE, PRICES, 9830.0, NOW, journal(), S)


def test_state_shows_et_time():
    assert "10:00 am ET" in render_state("baseline", STATE, PRICES, 9830.0, NOW, None, S)


def test_system_prompts_differ_on_sizing():
    assert "quantity" in system_prompt("baseline", S)
    assert "conviction" in system_prompt("improved", S)
    for v in ("baseline", "improved"):
        sp = system_prompt(v, S)
        assert "buy_to_enter" in sp and "close_position" in sp and "PLTR" in sp
