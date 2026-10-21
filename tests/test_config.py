import shutil
from pathlib import Path

import pytest

from stockbot.config import ConfigError, api_key, load_config

REPO_CONFIG = Path(__file__).parent.parent / "config"


def copy_config(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, dst)
    return dst


def test_loads_repo_config():
    cfg = load_config(REPO_CONFIG)
    assert [a.name for a in cfg.arms] == [
        "kimi-baseline", "kimi-improved", "deepseek-baseline", "deepseek-improved"]
    assert cfg.models["kimi-k3"].monthly_cap_usd == 80
    assert cfg.settings.universe == ("TSLA", "QQQ", "NVDA", "MSFT", "AMZN", "GOOGL", "PLTR")
    assert not cfg.models["claude"].enabled
    assert cfg.settings.min_rr == 1.5
    assert cfg.events[0].name == "FOMC decision"


def test_arm_with_unknown_model_rejected(tmp_path):
    d = copy_config(tmp_path)
    (d / "arms.yaml").write_text(
        "arms:\n  - {name: ghost-arm, model: nope, variant: baseline, enabled: true}\n")
    with pytest.raises(ConfigError, match="ghost-arm"):
        load_config(d)


def test_enabled_caps_exceed_global_rejected(tmp_path):
    d = copy_config(tmp_path)
    text = (d / "models.yaml").read_text().replace("monthly_cap_usd: 80", "monthly_cap_usd: 90")
    (d / "models.yaml").write_text(text)
    with pytest.raises(ConfigError, match="global"):
        load_config(d)


def test_api_key_missing_env(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setattr("stockbot.config.load_dotenv", lambda *a, **k: None)
    cfg = load_config(REPO_CONFIG)
    with pytest.raises(ConfigError, match="MOONSHOT_API_KEY"):
        api_key(cfg.models["kimi-k3"])


def test_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    cfg = load_config(REPO_CONFIG)
    assert api_key(cfg.models["kimi-k3"]) == "sk-test"
