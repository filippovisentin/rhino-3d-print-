"""Test dei modelli di avvio e fine stampa: compilazione e, se i profili Bambu ci sono, la A1 mini vera."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import machine  # noqa: E402

A1M = "Bambu Lab A1 mini 0.4 nozzle"
have = os.path.isdir(os.path.join(machine.PROFILES, "machine"))


def test_render_conditions_expressions_and_variables():
    tpl = ('M140 S[bed]\n{if (kind[tool] == "PLA") && (nozzle != 0.2)}\nM104 S220\n{else}\nM104 S250\n{endif}\n'
           'G1 F{flow[tool]/2.4053*60}\nM109 S{temp[tool]-20}\n{if !spiral && seq != "by object"}A{elsif spiral}B{endif}\n'
           'G29.1 Z{-0.02}\nG1 X[unknown_name]')
    v = {"bed": 65.0, "kind": ["PLA"], "tool": 0, "nozzle": 0.4, "flow": [21.0], "temp": [220.0],
         "spiral": False, "seq": "by layer"}
    out = machine.render(tpl, v).split("\n")
    assert out[0] == "M140 S65" and "M104 S220" in out and "M104 S250" not in out
    assert "G1 F523.843" in out and "M109 S200" in out and "A" in out and "G29.1 Z-0.02" in out
    assert out[-1] == "G1 X[unknown_name]"                      # cio che non conosciamo resta visibile
    assert "M104 S250" in machine.render(tpl, dict(v, kind=["PETG"]))
    assert "B" in machine.render(tpl, dict(v, spiral=True)).split("\n")
    with pytest.raises(ValueError):
        machine.render("{__import__('os')}", v)


@pytest.mark.skipif(not have, reason="profili di Bambu Studio non installati")
def test_real_a1_mini_start_and_end_compile_completely():
    start, end, change, f = machine.start_end(A1M, "PLA", (60.0, 70.0), (60.0, 40.0), 58.0)
    assert machine.leftovers(start) == [] and machine.leftovers(end) == []
    assert f["type"] == "PLA" and 190 <= f["nozzle_first"] <= 240 and 35 <= f["bed_first"] <= 80
    assert ("M190 S%d" % f["bed_first"]) in start and "G29 A1 X60 Y70 I60 J40" in start
    assert "G29.1 Z-0.02" in start and "M1002 set_filament_type:PLA" in start
    assert "G1 Z58.4 F900" in end and "G1 Z158" in end          # si alza di 100 mm: entra nei 180
    assert "layer_num" in change
    tall = machine.start_end(A1M, "PLA", (60.0, 70.0), (60.0, 40.0), 150.0)[1]
    assert "G1 Z250" not in tall                                 # pezzo alto: non sale oltre la macchina


@pytest.mark.skipif(not have, reason="profili di Bambu Studio non installati")
def test_petg_profile_exists_for_the_a1_mini():
    f = machine.filament_values(A1M, "PETG")
    assert f["type"] == "PETG" and f["nozzle"] > 230 and "0.4 nozzle" in f["profile"]
