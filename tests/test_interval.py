import pytest

from stockbot import interval
from stockbot.db import DB
from tests.snapshot import CFG

S = CFG.settings


@pytest.fixture
def db(tmp_path):
    return DB.open(tmp_path / "t.db")


def test_default_from_settings(db):
    assert interval.get(db, S) == S.cycle_every_min
    assert interval.source(db) == "default"


def test_override_beats_settings(db):
    interval.set(db, 7)
    assert interval.get(db, S) == 7
    assert interval.source(db) == "override"
    interval.set(db, None)
    assert interval.get(db, S) == S.cycle_every_min
    assert interval.source(db) == "default"


@pytest.mark.parametrize("bad", [0, 61, -5])
def test_out_of_range_rejected(db, bad):
    interval.set(db, 9)
    with pytest.raises(ValueError):
        interval.set(db, bad)
    assert interval.get(db, S) == 9
