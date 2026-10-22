import json

import pytest

from stockbot.agent.schema import ParseError, parse_decision

U = ("TSLA", "QQQ", "NVDA", "MSFT", "AMZN", "GOOGL", "PLTR")

AA_MSFT = {
    "xyz:MSFT": {
        "invalidation_condition": "Break below ~485 (recent 4h higher low) or NDX trend break",
        "risk_usd": 0, "confidence": 0, "is_add": False,
        "justification": "MSFT in uptrend", "profit_target": 585.855, "coin": "xyz:MSFT",
        "leverage": 10, "signal": "hold", "quantity": 8, "stop_loss": 439.455,
    }
}


def test_parses_aa_example():
    d = parse_decision(json.dumps(AA_MSFT), "baseline", U)
    m = d.per_symbol["MSFT"]
    assert m.signal == "hold" and m.stop_loss == 439.455 and m.profit_target == 585.855
    assert m.quantity == 8 and m.conviction is None
    assert m.invalidation_condition.startswith("Break below")


def test_strips_xyz_prefix():
    d = parse_decision(json.dumps({"xyz:NVDA": {"signal": "buy_to_enter", "quantity": 1}}), "baseline", U)
    assert d.per_symbol["NVDA"].signal == "buy_to_enter"


def test_json_in_prose_and_fences():
    body = json.dumps({"MSFT": {"signal": "close_position", "justification": "a {brace} inside"}})
    text = f"Here you go:\n```json\n{body}\n```\nThanks"
    assert parse_decision(text, "baseline", U).per_symbol["MSFT"].signal == "close_position"


def test_string_numbers_coerced():
    text = json.dumps({"MSFT": {"signal": "buy_to_enter", "stop_loss": "$1,182.50",
                                "profit_target": " 1300 ", "conviction": "0.7"}})
    m = parse_decision(text, "improved", U).per_symbol["MSFT"]
    assert m.stop_loss == 1182.5 and m.profit_target == 1300.0 and m.conviction == 0.7


def test_zero_stop_is_none():
    d = parse_decision(json.dumps({"MSFT": {"signal": "hold", "stop_loss": 0, "profit_target": 0}}),
                       "baseline", U)
    assert d.per_symbol["MSFT"].stop_loss is None and d.per_symbol["MSFT"].profit_target is None


def test_missing_symbols_default_hold():
    d = parse_decision(json.dumps({"MSFT": {"signal": "close_position"}}), "baseline", U)
    assert len(d.per_symbol) == 7
    assert d.per_symbol["TSLA"].signal == "hold"


def test_unknown_keys_ignored():
    d = parse_decision(json.dumps({"BTC": {"signal": "hold"}, "MSFT": {"signal": "hold"}}), "baseline", U)
    assert d.ignored_keys == ["BTC"]


def test_bad_signal_raises():
    with pytest.raises(ParseError, match="yolo"):
        parse_decision(json.dumps({"MSFT": {"signal": "yolo"}}), "baseline", U)


def test_not_json_raises():
    with pytest.raises(ParseError):
        parse_decision("I think hold", "baseline", U)


def test_bad_number_raises():
    with pytest.raises(ParseError, match="stop_loss"):
        parse_decision(json.dumps({"MSFT": {"signal": "hold", "stop_loss": "lots"}}), "baseline", U)


def test_notes_truncated():
    text = json.dumps({"MSFT": {"signal": "hold"}, "notes_to_self": "x" * 900})
    assert len(parse_decision(text, "improved", U).notes_to_self) == 500


def test_conviction_clamped_and_baseline_ignores_it():
    text = json.dumps({"MSFT": {"signal": "buy_to_enter", "conviction": 3, "quantity": 5}})
    imp = parse_decision(text, "improved", U).per_symbol["MSFT"]
    base = parse_decision(text, "baseline", U).per_symbol["MSFT"]
    assert imp.conviction == 1.0 and imp.quantity is None
    assert base.conviction is None and base.quantity == 5


def test_decisions_list_shape_accepted():
    text = json.dumps({"decisions": [{"coin": "MSFT", "signal": "close_position"}]})
    assert parse_decision(text, "baseline", U).per_symbol["MSFT"].signal == "close_position"


@pytest.mark.parametrize("raw,want", [(True, True), ("true", True), ("false", False), ("False", False),
                                      (False, False), (None, False)])
def test_is_add_string_flags(raw, want):
    d = parse_decision(json.dumps({"MSFT": {"signal": "hold", "is_add": raw}}), "baseline", U)
    assert d.per_symbol["MSFT"].is_add is want
