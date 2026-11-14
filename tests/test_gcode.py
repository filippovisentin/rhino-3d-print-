"""Test della scrittura del G-code: un cubo pieno dal peso noto, ordine, ritrazioni, velocita, controllo."""
import math
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import gcode, machine  # noqa: E402

A1M = "Bambu Lab A1 mini 0.4 nozzle"
have = os.path.isdir(os.path.join(machine.PROFILES, "machine"))
START, END = "; avvio di prova\nG90\nM83\n", "; fine di prova\n"
CHANGE = "; layer num/total_layer_count: {layer_num+1}/[total_layer_count]\nM73 L{layer_num+1}"
FIL = {"type": "PLA", "nozzle_first": 220.0, "nozzle": 215.0, "bed_first": 65.0, "bed": 60.0, "max_flow": 21.0,
       "density": 1.24, "diameter": 1.75, "flow_ratio": 1.0, "fan": 80.0, "fan_off_layers": 1}


def square(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def cube_layers(side=20.0, h=0.2, lw=0.45, origin=(80.0, 80.0), walls=2, count=None):
    """Cubo PIENO: pareti a distanza `spacing`, righe piene alternate in x e in y, accorciate di 0,35 linee."""
    sp = gcode.spacing(lw, h)
    ox, oy = origin
    layers = []
    for i in range(count or int(round(side / h))):
        paths = []
        for k in range(walls):
            a = lw / 2.0 + k * sp
            paths.append({"type": "outer" if k == 0 else "inner", "closed": True,
                          "pts": square(ox + a, oy + a, ox + side - a, oy + side - a)})
        lo = lw / 2.0 + (walls - 0.5) * sp                       # bordo interno dell'ultima parete
        hi = side - lo
        m = int(round((hi - lo) / sp))
        step = (hi - lo) / m
        for j in range(m):
            c, a, b = lo + (j + 0.5) * step, lo + 0.35 * lw, hi - 0.35 * lw
            pts = [[ox + a, oy + c], [ox + b, oy + c]] if i % 2 == 0 else [[ox + c, oy + a], [ox + c, oy + b]]
            paths.append({"type": "solid", "closed": False, "pts": np.array(pts)})
        layers.append({"z": round((i + 1) * h, 6), "h": h, "paths": paths})
    return layers


def body(text):
    return text.split(gcode.BODY_START)[1].split(gcode.BODY_END)[0].split("\n")


def test_section_and_spacing_fill_exactly():
    assert gcode.section(0.45, 0.2) == pytest.approx(0.25 * 0.2 + math.pi * 0.01)
    assert gcode.spacing(0.45, 0.2) * 0.2 == pytest.approx(gcode.section(0.45, 0.2))


def test_solid_cube_weighs_what_its_volume_says():
    res = gcode.write(cube_layers(), START, END, CHANGE, FIL)
    chk = gcode.check(res["text"], expected_mm3=8000.0, tol=0.04)
    assert chk["ok"], chk["errors"]
    assert chk["layers"] == res["layers"] == 100
    assert res["weight_g"] == pytest.approx(8.0 * 1.24, rel=0.04)          # 20 mm di lato, pieno: 9,92 g
    assert chk["weight_g"] == pytest.approx(res["weight_g"], rel=1e-4)      # il testo dice lo stesso
    assert chk["min"][0] >= 80.0 and chk["max"][0] <= 100.0 and chk["max"][2] == pytest.approx(20.4)
    assert 60.0 < res["time_s"] < 3600.0 and res["total_time_s"] > res["time_s"]
    head = res["text"].split("; HEADER_BLOCK_END")[0]
    assert "; total layer number: 100" in head and ("; total filament weight [g] : %.2f" % res["weight_g"]) in head


def test_order_in_a_layer_and_nearest_first():
    far = {"type": "outer", "closed": True, "pts": square(60, 60, 70, 70)}
    near = {"type": "outer", "closed": True, "pts": square(10, 10, 20, 20)}
    line = {"type": "sparse", "closed": False, "pts": np.array([[50.0, 50.0], [22.0, 22.0]])}
    paths = [line, far, near, {"type": "support", "closed": False, "pts": np.array([[5.0, 5.0], [5.0, 9.0]])},
             {"type": "inner", "closed": True, "pts": square(11, 11, 19, 19)}]
    text = gcode.write([{"z": 0.2, "h": 0.2, "paths": paths}], START, END, CHANGE, FIL)["text"]
    feats = [l[11:] for l in body(text) if l.startswith("; FEATURE: ")]
    assert feats == ["Support", "Inner wall", "Outer wall", "Sparse infill"]
    seq, pos = gcode.sequence([far, near], (0.0, 0.0))
    assert seq[0][0] is near and np.allclose(seq[0][1][0], [10, 10]) and np.allclose(seq[0][1][-1], [10, 10])
    assert len(seq[0][1]) == 5 and np.allclose(pos, seq[1][1][-1])           # l'anello torna al punto di partenza
    rev, _ = gcode.sequence([line], (20.0, 20.0))
    assert np.allclose(rev[0][1][0], [22.0, 22.0])                           # la riga parte dal capo piu vicino


def test_retraction_and_lift_only_on_long_travels():
    a = {"type": "solid", "closed": False, "pts": np.array([[10.0, 10.0], [30.0, 10.0]])}
    b = {"type": "solid", "closed": False, "pts": np.array([[30.0, 10.4], [10.0, 10.4]])}     # a 0,4 mm: niente ritrazione
    c = {"type": "solid", "closed": False, "pts": np.array([[60.0, 60.0], [80.0, 60.0]])}     # lontano: ritrazione
    res = gcode.write([{"z": 0.2, "h": 0.2, "paths": [a, b, c]}], START, END, CHANGE, FIL)
    L = body(res["text"])
    assert res["retractions"] == 3                    # prima di partire, verso il tratto lontano, a fine strato
    assert L.count("G1 E-0.8 F1800") == 3 and L.count("G1 E0.8 F1800") == 2
    assert L.count("G1 Z0.6 F24000") == 2 and "G1 X30 Y10.4 F24000" in L
    assert gcode.check(res["text"])["ok"]
    rows = [{"type": "support", "closed": False, "link": 4.0, "pts": np.array([[10.0, 10 + 2.5 * k], [30.0, 10 + 2.5 * k]])}
            for k in range(4)]
    zig = gcode.write([{"z": 0.2, "h": 0.2, "paths": rows}], START, END, CHANGE, FIL)       # righe unite a zig-zag
    assert zig["retractions"] == 2 and zig["by_type"]["support"] == pytest.approx(4 * 20.0 + 3 * 2.5)
    assert zig["travel_mm"] == pytest.approx(math.hypot(10, 10)) and gcode.check(zig["text"])["ok"]


def test_speed_first_layer_flow_cap_fan_temperature_and_progress():
    layers = cube_layers(count=4)
    res = gcode.write(layers, START, END, CHANGE, dict(FIL, max_flow=4.0), {"speed": {"outer": 90.0}})
    text = res["text"]
    blocks = text.split("; CHANGE_LAYER")
    cap = round(4.0 / gcode.section(0.45, 0.2) * 60)                          # 49 mm/s: sotto ogni velocita chiesta
    assert "G1 F2400" in blocks[1] and "G1 F%d" % cap not in blocks[1]       # primo strato a 40 mm/s
    assert set(re.findall(r"^G1 F(\d+)$", blocks[2], re.M)) == {str(cap)}
    free = gcode.write(layers, START, END, CHANGE, FIL, {"speed": {"outer": 90.0}})["text"].split("; CHANGE_LAYER")[2]
    assert "G1 F5400" in free and "G1 F10800" in free and "G1 F9000" in free  # esterna 90, interna 180, pieno 150
    assert "M106" not in blocks[1] and "M106 S204" in blocks[2]               # ventola all'80 % dal secondo strato
    assert "M104 S215" in blocks[2] and "M140 S60" in blocks[2] and "M204 S500" in blocks[1] and "M204 S5000" in blocks[2]
    pct = [int(p) for p in re.findall(r"^M73 P(\d+) R\d+$", text, re.M)]
    assert pct == sorted(pct) and pct[0] == 0 and pct[-1] == 100 and len(pct) == 6
    assert "M73 L4" in blocks[4] and "layer_num" not in text


def test_check_catches_what_is_wrong():
    text = gcode.write(cube_layers(count=3), START, END, CHANGE, FIL)["text"]
    assert gcode.check(text)["ok"]
    out = gcode.check(text.replace("G1 X80.225 Y80.225", "G1 X250 Y80.225", 1))
    assert not out["ok"] and "fuori dalla stampante" in out["errors"][0]
    out = gcode.check(text.replace("G1 E0.8 F1800\n", "", 1))
    assert not out["ok"] and any("ritirato" in e for e in out["errors"])
    out = gcode.check(text.replace("; Z_HEIGHT: 0.4", "; Z_HEIGHT: 0.7", 1))
    assert not out["ok"] and any("non poggia" in e for e in out["errors"])
    lines = text.split("\n")
    k = next(i for i, l in enumerate(lines) if re.match(r"G1 X\S+ Y\S+ E", l))
    lines[k] = re.sub(r"E\S+", "E9", lines[k])
    out = gcode.check("\n".join(lines))
    assert not out["ok"] and any("linea larga" in e for e in out["errors"]) and any("dichiarato" in e for e in out["errors"])
    assert not gcode.check(text, expected_mm3=5000.0)["ok"]
    assert not gcode.check("G1 X1 Y1")["ok"]


@pytest.mark.skipif(not have, reason="profili di Bambu Studio non installati")
def test_real_a1_mini_file_is_complete():
    res = gcode.generate(cube_layers(count=10), A1M, "PLA")
    text = res["text"]
    assert res["check"]["ok"], res["check"]["errors"]
    assert machine.leftovers(text) == []
    assert "G29 A1 X80 Y80 I20 J20" in text                    # livella solo la zona del pezzo
    assert text.index("M1002 gcode_claim_action") < text.index(gcode.BODY_START) < text.index(gcode.BODY_END)
    assert "M991 S0 P9 ;notify layer change" in text and "G1 Z2.4 F900" in text.split(gcode.BODY_END)[1]
    assert res["filament"]["flow_ratio"] == pytest.approx(0.98)
