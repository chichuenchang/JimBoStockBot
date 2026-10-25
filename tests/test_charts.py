import struct
from datetime import date

import pytest

from stockbot import charts
from tests.test_history import days

BARS = days("NVDA", date(2016, 1, 4), [1.0, 1.5, 3.0, 8.0, 20.0, 60.0, 180.0])


def test_render_png_size():
    png = charts.render(BARS, "NVDA 1Day 2016-01-04..2016-01-13")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1024, 512)  # IHDR width, height


def test_render_log_differs():
    assert charts.render(BARS, "t", log=True) != charts.render(BARS, "t")


def test_render_empty_raises():
    with pytest.raises(ValueError):
        charts.render([], "t")
