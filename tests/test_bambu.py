"""Test delle analisi del G-code (generative_print.bambu) su G-code sintetico dal risultato noto.
Non serve Bambu Studio: l'ultimo test lo usa solo se e installato."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import bambu, checks, generators  # noqa: E402


def cone_gcode(r0=40.0, shift=0.15, h=0.2, layers=60, seg=360):
    """Strati circolari il cui raggio cresce di 'shift' mm a strato: sbalzo atan(shift / h)."""
    L = ["; total layer number: %d" % layers, "; filament_density = 1.26", "; spiral_mode = 0"]
    for k in range(layers):
        z = h * (k + 1)
        r = r0 + shift * k
        L += ["; CHANGE_LAYER", "G1 Z%.3f" % z, "; FEATURE: Outer wall", "G1 X%.4f Y0 F3000" % r]
        for i in range(1, seg + 1):
            a = 2 * math.pi * i / seg
            L.append("G1 X%.4f Y%.4f E0.02" % (r * math.cos(a), r * math.sin(a)))
    return "\n".join(L)


def test_wall_overhang_matches_cone_angle():
    g = cone_gcode(shift=0.15)
    res = bambu.wall_overhang(g, 0.2, limit_deg=45.0, every=1)
    expected = math.degrees(math.atan2(0.15, 0.2))          # 36,9 gradi
    assert res["max_deg"] == pytest.approx(expected, abs=1.0)
    assert res["layers_over_limit"] == 0


def test_wall_overhang_flags_steep_cone_and_jumps():
    res = bambu.wall_overhang(cone_gcode(shift=0.25), 0.2, limit_deg=45.0, every=1)   # 51 gradi
    assert res["layers_over_limit"] == res["layers_checked"]
    jump = cone_gcode(shift=1.0)                                                      # 5 mm a strato
    assert bambu.wall_overhang(jump, 0.2, every=1)["discontinuities_z"]


def test_spiral_continuity_and_feature_lengths():
    h, r, seg = 0.2, 30.0, 180
    L = ["; model printing time: 1h 2m 3s", "; FEATURE: Outer wall", "G1 X30 Y0 Z0.2 F3000"]
    for i in range(1, 10 * seg + 1):
        a = 2 * math.pi * i / seg
        L.append("G1 X%.4f Y%.4f Z%.5f E0.01" % (r * math.cos(a), r * math.sin(a), 0.2 + h * i / seg))
    g = "\n".join(L)
    sp = bambu.spiral_continuity(g, 0.0, 10.0)
    assert sp["continuous"] and sp["segments"] == 10 * seg
    stats = bambu.parse_gcode(g)
    assert stats["time_h"] == pytest.approx(1 + 2 / 60 + 3 / 3600, abs=1e-3)
    assert stats["feature_m"]["Outer wall"] == pytest.approx(10 * 2 * math.pi * r / 1000, abs=0.01)
    broken = g + "\nG1 X0 Y0\nG1 X5 Y5 E0.1"                 # spostamento a vuoto dentro la parete
    assert not bambu.spiral_continuity(broken, 0.0, 10.0)["continuous"]


def test_line_section_matches_slicer_formula():
    assert checks.line_section(0.45, 0.2) == pytest.approx(0.45 * 0.2 - 0.2 ** 2 * (1 - math.pi / 4))


def test_overrides_follow_the_recipe():
    lamp = generators.resolve({"mesh": {"edge": 4.0}})
    o = bambu.overrides(lamp)
    assert o["spiral_mode"] == "0" and o["wall_generator"] == "arachne" and o["enable_support"] == "0"
    vase = generators.vase_params({"object": "vase", "print": {"mode": "spiral", "line_width": 0.7}})
    o = bambu.overrides(generators.resolve(vase))
    assert o["spiral_mode"] == "1" and o["wall_loops"] == "1" and o["outer_wall_line_width"] == "0.7"


@pytest.mark.skipif(not bambu.available(), reason="Bambu Studio non installato")
def test_profiles_flatten_without_inherits_loops():
    m = bambu.flatten("machine", "Bambu Lab X1 Carbon 0.4 nozzle")
    assert m["inherits"] == "Bambu Lab X1 Carbon 0.4 nozzle" and m["from"] == "User"
    assert "printable_area" in m and "nozzle_diameter" in m


def test_wall_overhang_in_spiral_uses_layer_markers():
    """In spirale z sale a ogni tratto: lo sbalzo si misura da un giro (strato) al successivo."""
    h, seg, turns = 0.2, 360, 40
    L = ["; FEATURE: Outer wall", "G1 X30 Y0 Z0.2 F3000"]
    for i in range(1, turns * seg + 1):
        if i % seg == 1:
            L.append("; CHANGE_LAYER")
        a = 2 * math.pi * i / seg
        r = 30.0 + 0.1 * i / seg                              # il raggio cresce di 0,1 mm a giro
        L.append("G1 X%.4f Y%.4f Z%.5f E0.01" % (r * math.cos(a), r * math.sin(a), 0.2 + h * i / seg))
    res = bambu.wall_overhang("\n".join(L), h, every=1)
    assert res["max_deg"] == pytest.approx(math.degrees(math.atan2(0.1, h)), abs=1.5)


def test_shrinking_shell_is_supported_not_overhang():
    """Guscio la cui parete si assottiglia: il contorno interno si allarga verso il materiale, come sul
    bordo superiore della lampada. Quei punti stanno sopra lo strato sotto: appoggiati, non sbalzo."""
    h, seg = 0.2, 360
    L = []
    for k in range(30):
        L += ["; CHANGE_LAYER", "G1 Z%.3f" % (h * (k + 1)), "; FEATURE: Outer wall"]
        for r in (50.0, 45.0 + 0.1 * k):                  # esterno fisso, interno che si allarga di 0,1
            L.append("G1 X%.4f Y0" % r)
            for i in range(1, seg + 1):
                a = 2 * math.pi * i / seg
                L.append("G1 X%.4f Y%.4f E0.02" % (r * math.cos(a), r * math.sin(a)))
    res = bambu.wall_overhang("\n".join(L), h, every=1)
    assert res["max_deg"] < 5.0 and res["layers_over_limit"] == 0
