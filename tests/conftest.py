import logging

import pytest

from stockbot import cli


@pytest.fixture(autouse=True)
def _tmp_logs(monkeypatch, tmp_path):
    """main() logs to ./logs: keep test runs out of the real stockbot.log."""
    real, logger = cli.setup_logging, logging.getLogger("stockbot")
    handlers, level = logger.handlers[:], logger.level
    # relative dirs land under tmp_path; absolute ones (a test's own tmp dir) pass through
    monkeypatch.setattr(cli, "setup_logging", lambda log_dir: real(tmp_path / log_dir))
    yield
    for h in logger.handlers:
        if h not in handlers:
            h.close()
    logger.handlers = handlers
    logger.setLevel(level)
