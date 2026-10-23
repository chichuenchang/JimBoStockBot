import logging

import pytest

from stockbot import cli


@pytest.fixture(autouse=True)
def _tmp_logs(monkeypatch, tmp_path):
    """main() logs to ./logs: keep test runs out of the real stockbot.log."""
    real, logger = cli.setup_logging, logging.getLogger("stockbot")
    handlers, level = logger.handlers[:], logger.level
    monkeypatch.setattr(cli, "setup_logging", lambda log_dir: real(tmp_path / "logs"))
    yield
    for h in logger.handlers:
        if h not in handlers:
            h.close()
    logger.handlers, logger.level = handlers, level
