import shutil
from pathlib import Path

import pytest

from stockbot.config import ConfigError, api_key, load_config
from tests.snapshot import CFG, CONFIG as TEST_CONFIG

REPO_CONFIG = Path(__file__).parent.parent / "config"


def copy_config(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(TEST_CONFIG, dst)
    return dst


def test_repo_config_loads():
    load_config(REPO_CONFIG)


def test_loads_test_config():
    assert [a.name for a in CFG.arms] == [
        "kimi-baseline", "kimi-improved", "deepseek-baseline", "deepseek-improved", "rules-trend",
        "random-control", "rules-momentum"]
    assert CFG.models["kimi-k3"].monthly_cap_usd == 80
    assert CFG.models["deepseek-v4.1-flash"].extra_body == {"thinking": {"type": "disabled"}}
    assert CFG.models["kimi-k3"].extra_body == {"reasoning_effort": "low"}
    assert CFG.settings.universe == ("TSLA", "QQQ", "NVDA", "MSFT", "AMZN", "GOOGL", "PLTR")
    assert not CFG.models["claude"].enabled
    assert CFG.settings.min_rr == 1.5
    assert CFG.events[0].name == "FOMC decision"


def test_arm_with_unknown_model_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text(
        "arms:\n  - {name: ghost-arm, model: nope, variant: baseline, enabled: true}\n")
    with pytest.raises(ConfigError, match="ghost-arm"):
        load_config(d)


def test_arm_with_bad_key_is_config_error(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text(
        "arms:\n  - {name: typo-arm, modle: kimi-k3, variant: baseline, enabled: true}\n")
    with pytest.raises(ConfigError, match="typo-arm"):
        load_config(d)


def test_momentum_arm_needs_benchmark_in_universe(tmp_path):
    d = copy_config(tmp_path)
    text = (d / "settings.yaml").read_text().replace("universe: [TSLA, QQQ,", "universe: [TSLA,")
    (d / "settings.yaml").write_text(text)
    with pytest.raises(ConfigError, match="rules-momentum"):
        load_config(d)


def test_search_flag_loaded():
    assert [a.name for a in CFG.arms if a.search] == ["deepseek-improved"]


def test_repo_search_arms():
    cfg = load_config(REPO_CONFIG)
    assert [a.name for a in cfg.arms if a.search] == ["kimi-improved", "deepseek-improved"]
    assert cfg.models["deepseek-v4.1-flash"].monthly_cap_usd == 35


def test_repo_kimi_arms_mirror_deepseek():
    cfg = load_config(REPO_CONFIG)
    arms = {a.name: a for a in cfg.arms}
    for kind in ("baseline", "improved"):
        k, d = arms[f"kimi-{kind}"], arms[f"deepseek-{kind}"]
        assert k.enabled and k.model == "kimi-k3.1"
        assert (k.variant, k.search, k.charts) == (d.variant, d.search, d.charts)
    m = cfg.models["kimi-k3.1"]
    assert m.enabled and m.model == "k3.1-pro" and m.extra_body == {"reasoning_effort": "none"}
    assert m.quota_url == "https://api.kimi.com/coding/v1/usages"


def test_search_on_control_arm_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text("arms:\n  - {name: r, variant: rules, enabled: true, search: true}\n")
    with pytest.raises(ConfigError, match="search needs an LLM arm"):
        load_config(d)


def test_search_settings_defaults():
    s = CFG.settings
    assert (s.search_max_results, s.search_max_pages, s.search_page_chars) == (10, 3, 15000)


def test_enabled_caps_exceed_global_rejected(tmp_path):
    d = copy_config(tmp_path)
    text = (d / "models.yaml").read_text().replace("monthly_cap_usd: 80", "monthly_cap_usd: 90")
    (d / "models.yaml").write_text(text)
    with pytest.raises(ConfigError, match="global"):
        load_config(d)


def test_api_key_missing_env(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setattr("stockbot.config.load_dotenv", lambda *a, **k: None)
    with pytest.raises(ConfigError, match="MOONSHOT_API_KEY"):
        api_key(CFG.models["kimi-k3"])


def test_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    assert api_key(CFG.models["kimi-k3"]) == "sk-test"


def test_charts_flag_only_on_jimbo():
    assert [a.name for a in CFG.arms if a.charts] == ["deepseek-improved"]
    assert [a.name for a in load_config(REPO_CONFIG).arms if a.charts] == ["kimi-improved", "deepseek-improved"]
    assert CFG.settings.chart_max_per_decision == 3


def test_regime_gate_off_only_on_jimbo():
    assert [a.name for a in load_config(REPO_CONFIG).arms if not a.regime_gate] == ["kimi-improved", "deepseek-improved"]


def test_regime_gate_on_unruled_arm_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text("arms:\n  - {name: m, variant: momentum, enabled: true, regime_gate: false}\n")
    with pytest.raises(ConfigError, match="regime_gate needs a ruled arm"):
        load_config(d)


def test_charts_on_control_arm_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text("arms:\n  - {name: r, variant: rules, enabled: true, charts: true}\n")
    with pytest.raises(ConfigError, match="charts needs an LLM arm"):
        load_config(d)
