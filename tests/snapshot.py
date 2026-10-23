"""Fixed config snapshot tests run on, so enabling/disabling models in config/ never breaks them."""
from pathlib import Path

from stockbot.cli import main
from stockbot.config import load_config

CONFIG = Path(__file__).parent / "config"
CFG = load_config(CONFIG)


def cli(db_path: Path, *args: str) -> int:
    return main(["--config", str(CONFIG), "--db", str(db_path), *args])
