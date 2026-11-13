"""Test dell'analisi di stampa generica e del tratteggio su casi dal risultato noto."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import bambu, printcheck, slicer  # noqa: E402


def box(sx, sy, sz, z0=0.0):
    V = np.array([[0, 0, 0], [sx, 0, 0], [sx, sy, 0], [0, sy, 0],
                  [0, 0, sz], [sx, 0, sz], [sx, sy, sz], [0, sy, sz]], dtype=float) + [0, 0, z0]
    F = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return V, F


def test_cube_estimates_match_hand_calculation():
    V, F = box(20, 20, 20, z0=7.5)                       # sollevato: va appoggiato al piano
    res = printcheck.analyse(V, F, {"infill": 0.0, "walls": 2, "line_width": 0.45, "top_layers": 5,
                                    "bottom_layers": 4, "layer": 0.2})
    assert res["ok"] and res["closed"] and res["lift_mm"] == pytest.approx(7.5)
    assert res["layers"] == 100 and res["overhang_area_mm2"] == 0 and not res["needs_support"]
    shell = 4 * 400 * 0.45 * 2 + 400 * 0.2 * 5 + 400 * 0.2 * 4       # pareti + sopra + sotto, mm3
    assert res["extruded_cm3"] == pytest.approx(shell / 1000.0, rel=1e-6)
    assert res["mass_g"] == pytest.approx(shell * 1.24 / 1000.0, rel=1e-6)
    full = printcheck.analyse(V, F, {"infill": 1.0})
    assert full["mass_g"] == pytest.approx(8.0 * 1.24, rel=1e-6)


def test_bed_fit_overhang_and_supports():
    V, F = box(200, 100, 30)
    small = printcheck.analyse(V, F, {"bed": [180, 180, 180]})
    assert not small["fits"] and small["fit_scale"] == pytest.approx(0.9)      # 200 mm su un piano da 180
    assert "ridotto al 90%" in printcheck.report(small)
    assert bambu.filament_profile("Bambu Lab X1 Carbon 0.4 nozzle", "PLA") == "Bambu PLA Basic @BBL X1C"
    assert printcheck.analyse(V, F, {"bed": [256, 256, 256]})["fits"]
    # tavolo: piano 60 x 60 x 5 su una gamba 10 x 10 x 30 -> il sotto del piano e uno sbalzo a 90 gradi
    Va, Fa = box(10, 10, 30)
    Vb, Fb = box(60, 60, 5, z0=30)
    Vb = Vb - [25, 25, 0]
    V2, F2 = np.vstack([Va, Vb]), np.vstack([Fa, Fb + 8])
    res = printcheck.analyse(V2, F2)
    assert res["needs_support"] and not res["ok"] and res["overhang_max_deg"] == pytest.approx(90.0)
    assert printcheck.analyse(V2, F2, {"supports": True})["rows"][2][1]       # con i supporti passa
    assert "supporti" in printcheck.report(res)


def test_hatch_square_with_hole():
    outer = np.array([[0, 0], [40, 0], [40, 40], [0, 40]], dtype=float)
    hole = np.array([[10, 10], [30, 10], [30, 30], [10, 30]], dtype=float)
    seg = slicer.hatch([outer], 0.0, 2.0)
    assert len(seg) == 20
    assert np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum() == pytest.approx(20 * 40.0)
    seg = slicer.hatch([outer, hole], 0.0, 2.0)          # il foro toglie 10 righe x 20 mm
    assert np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum() == pytest.approx(20 * 40.0 - 10 * 20.0)
    diag = slicer.hatch([outer], 45.0, 1.0)              # lunghezza totale circa area / passo
    assert np.linalg.norm(diag[:, 1] - diag[:, 0], axis=1).sum() == pytest.approx(1600.0, rel=0.02)
    assert len(slicer.hatch([outer], 0.0, 2.0, min_length=50.0)) == 0


def test_generic_overrides_and_printer_volume():
    o = bambu.overrides({"object": "solid", "print": {"generic": True, "layer": 0.16, "line_width": 0.42,
                                                      "walls": 3, "infill": 0.2, "top_layers": 6, "bottom_layers": 5}})
    assert o["wall_loops"] == "3" and o["sparse_infill_density"] == "20%" and o["layer_height"] == "0.16"
    assert o["top_shell_layers"] == "6" and o["bottom_shell_layers"] == "5" and o["spiral_mode"] == "0"
    assert bambu.printer_volume("stampante sconosciuta") == [256.0, 256.0, 256.0]
    assert bambu.default_printer() in bambu.PRINTERS


def test_best_orientation_flips_the_table():
    """Tavolo (piano su una gamba): capovolto, col piano in basso, non ha piu sbalzi."""
    Va, Fa = box(10, 10, 30)
    Vb, Fb = box(60, 60, 5, z0=30)
    Vb = Vb - [25, 25, 0]
    V, F = np.vstack([Va, Vb]), np.vstack([Fa, Fb + 8])
    ranked = printcheck.orientations(V, F)
    best = ranked[0]
    # le due scatole sono sovrapposte, non unite: dentro resta la faccia superiore della gamba
    # (100 mm2), che capovolta guarda in giu. Tutto il resto dello sbalzo (3600 mm2) sparisce.
    assert best["down"] == [0.0, 0.0, 1.0] and best["overhang_area_mm2"] == pytest.approx(100.0)
    assert best["contact_mm2"] == pytest.approx(3600.0)
    as_drawn = [o for o in ranked if o["down"] == [0.0, 0.0, -1.0]][0]
    assert as_drawn["overhang_area_mm2"] == pytest.approx(3600.0)   # tutto il sotto del piano
    W = printcheck.rotate(V, best["R"])
    assert W[:, 2].min() == pytest.approx(V[:, 2].min())            # resta appoggiato alla stessa quota
    R = printcheck.rotation_to_down([1.0, 0.0, 0.0])
    assert np.allclose(R @ np.array([1.0, 0.0, 0.0]), [0, 0, -1]) and np.allclose(R @ R.T, np.eye(3))
    assert np.linalg.det(R) == pytest.approx(1.0)


def test_support_columns_under_the_table_top():
    """Tavolo: i supporti stanno sotto il piano (3600 mm2, alto 30 mm) e scendono fino al piatto."""
    from generative_print import checks
    Va, Fa = box(10, 10, 30)
    Vb, Fb = box(60, 60, 5, z0=30)
    Vb = Vb - [25, 25, 0]
    V, F = np.vstack([Va, Vb]), np.vstack([Fa, Fb + 8])
    res = printcheck.analyse(V, F, {"supports": True}, raycast=checks.raycast_numpy(V, F))
    cols = res["support_columns"]
    assert len(cols) == pytest.approx(3600.0 / 9.0, rel=0.1)             # una colonna ogni 3 x 3 mm
    assert np.allclose(cols[:, 2], 30.0) and cols[:, 3].max() <= 30.0
    free = (np.abs(cols[:, 0] - 5) > 6) | (np.abs(cols[:, 1] - 5) > 6)   # fuori dalla gamba: fino al piatto
    assert np.allclose(cols[free, 3], 0.0)
    expected = 3600.0 * 30.0 * printcheck.SUPPORT_DENSITY / 1000.0
    assert res["support_cm3"] == pytest.approx(expected, rel=0.12)
    assert "colonne" in printcheck.report(res)
