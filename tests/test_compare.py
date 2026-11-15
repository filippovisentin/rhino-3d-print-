"""Test del confronto tra G-code: lettura per tipo di tratto e per quota."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import compare, gcode  # noqa: E402
from test_gcode import CHANGE, END, FIL, START, cube_layers  # noqa: E402


def test_read_matches_what_the_writer_declares():
    res = gcode.write(cube_layers(count=4), START, END, CHANGE, FIL)
    st = compare.read(res["text"])
    assert st["volume_mm3"] == pytest.approx(res["volume_mm3"], rel=1e-3)
    assert set(st["features"]) == {"Outer wall", "Inner wall", "Internal solid infill"}
    assert sorted(st["layers"]) == [0.2, 0.4, 0.6, 0.8]
    assert st["retractions"] == res["retractions"]
    groups = compare.grouped(st)
    assert groups["pareti"] + groups["pieno"] == pytest.approx(st["volume_mm3"])
    lines = compare.report(st, st)
    assert any(l.startswith("totale") and l.rstrip().endswith("0%") for l in lines)
