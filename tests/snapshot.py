"""Fixed config snapshot tests run on, so enabling/disabling models in config/ never breaks them."""
from pathlib import Path

from stockbot.config import load_config

CONFIG = Path(__file__).parent / "config"
CFG = load_config(CONFIG)
