"""Test dei supporti su casi dal risultato noto: un tavolo (piano 60 x 60 su una gamba 10 x 10)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import checks, printcheck, supports  # noqa: E402
from test_printcheck import box  # noqa: E402


def table(shelf=False):
    Va, Fa = box(10, 10, 30)
    Vb, Fb = box(60, 60, 5, z0=30)
    Vb = Vb - [25, 25, 0]
    V, F = np.vstack([Va, Vb]), np.vstack([Fa, Fb + 8])
    if shelf:                                   # mensola bassa sotto un lato del piano
        Vc, Fc = box(20, 60, 4)
        V, F = np.vstack([V, Vc + [15, -25, 0]]), np.vstack([F, Fc + 16])
    return V, F


def over(V, F):
    ang, areas, bed = checks.overhang_angles(V, F)
    return ~bed & (ang > 43.0)


def test_ldi_crossings_of_a_box():
    V, F = box(10, 10, 30)
    L = supports.ldi(V, F, 1.0)
    assert len(L["z"]) == 200 and L["down"].sum() == 100          # 100 celle, sotto e sopra
    i, j = supports.cell_of(L, np.array([[5.0, 5.0]]))
    found, z, inside = supports.below(L, i * L["shape"][1] + j, [15.0])
    assert found[0] and z[0] == pytest.approx(0.0) and inside[0]    # a meta altezza si e dentro il pezzo
    found, z, inside = supports.below(L, i * L["shape"][1] + j, [40.0])
    assert z[0] == pytest.approx(30.0) and not inside[0]


def test_snug_supports_fill_exactly_under_the_top():
    V, F = table()
    sp = supports.generate(V, F, over(V, F), {"support_type": "snug"}, res=1.0)
    c = sp["columns"]
    assert len(c) == 3600 - 100                                      # tutto il piano meno la gamba
    assert np.allclose(c[:, 2], 29.8) and np.allclose(c[:, 3], 0.0)  # 0,2 mm sotto il pezzo, fino al piatto
    assert sp["envelope_mm3"] == pytest.approx(3500 * 29.8)
    expected = 3500 * (0.4 * supports.INTERFACE_DENSITY + 29.4 * supports.BASE_DENSITY)
    assert sp["volume_mm3"] == pytest.approx(expected)


def test_grid_supports_are_wider_and_bed_only_drops_those_on_the_model():
    V, F = table(shelf=True)
    snug = supports.generate(V, F, over(V, F), {"support_type": "snug"}, res=1.0)
    grid = supports.generate(V, F, over(V, F), {"support_type": "grid"}, res=1.0)
    assert snug["on_model"].sum() == pytest.approx(20 * 60, rel=0.05)      # sopra la mensola
    assert np.allclose(snug["columns"][snug["on_model"], 3], 4.2)
    assert len(grid["columns"]) > len(snug["columns"])
    xy = grid["columns"][:, :2]
    assert xy[:, 0].min() < -25.5 and xy[:, 0].max() > 35.5                # sporge dal piano di ~2 mm
    inside_leg = (np.abs(xy[:, 0] - 5) < 4.9) & (np.abs(xy[:, 1] - 5) < 4.9)
    assert not inside_leg.any()                                            # mai dentro il pezzo
    bed = supports.generate(V, F, over(V, F), {"support_type": "snug", "support_bed_only": True}, res=1.0)
    assert not bed["on_model"].any() and len(bed["columns"]) == len(snug["columns"]) - snug["on_model"].sum()


def test_layer_pattern_lines_and_interface():
    V, F = table()
    sp = supports.generate(V, F, over(V, F), {"support_type": "snug"}, res=1.0)
    base, face = supports.layer_pattern(sp, 10.0)
    assert len(face) == 0 and len(base) > 20
    length = np.abs(base[:, 1, 0] - base[:, 0, 0]).sum()
    assert length == pytest.approx(3500.0 / sp["spacing"], rel=0.1)        # una linea ogni 2,5 mm
    base, face = supports.layer_pattern(sp, 29.7)
    assert len(base) == 0 and np.abs(face[:, 1, 1] - face[:, 0, 1]).sum() == pytest.approx(3500.0, rel=0.02)


def test_tree_reaches_the_bed_merges_and_avoids_the_part():
    V, F = table(shelf=True)
    sp = supports.generate(V, F, over(V, F), {"support_type": "tree"}, res=1.0)
    st, segs = sp["stats"], sp["segments"]
    assert st["tips"] == pytest.approx(3500 / 25.0, rel=0.25)             # una punta ogni 5 x 5 mm
    assert 0 < st["on_bed"] < st["tips"] / 3                               # i rami si uniscono
    vox = supports.voxels(supports.ldi(V, F, 1.0), 1.0)
    L = supports.ldi(V, F, 1.0)
    mid = (segs[:, 0:3] + segs[:, 4:7]) / 2.0
    low = mid[mid[:, 2] < 27.0]                                            # sotto la zona di contatto
    i, j = supports.cell_of(L, low[:, :2])
    ok = (i >= 0) & (i < vox.shape[0]) & (j >= 0) & (j < vox.shape[1])
    assert not vox[i[ok], j[ok], np.floor(low[ok, 2]).astype(int)].any()   # nessun ramo dentro il pezzo
    slope = np.linalg.norm(segs[:, 4:6] - segs[:, 0:2], axis=1) / np.maximum(segs[:, 2] - segs[:, 6], 1e-9)
    assert slope[segs[:, 2] - segs[:, 6] > 0.5].max() <= np.tan(np.radians(40.0)) + 1e-6
    assert st["on_model"] < st["tips"] * 0.2 and segs[:, 6].min() == pytest.approx(0.0)
    assert 0 < sp["volume_mm3"] < 0.8 * supports.generate(V, F, over(V, F), {"support_type": "grid"},
                                                           res=1.0)["volume_mm3"]
    Vt, Ft = supports.tree_mesh(segs)
    assert len(Vt) == 16 * len(segs) and Ft.max() == len(Vt) - 1
    sec = supports.tree_sections(segs, 10.0)
    assert len(sec) >= st["on_bed"] and (sec[:, 2] > 0.6).all()


def test_analyse_uses_the_chosen_support_type():
    V, F = table()
    for kind in supports.TYPES:
        res = printcheck.analyse(V, F, {"supports": True, "support_type": kind})
        assert res["support"]["type"] == kind and res["support_cm3"] > 0
        assert "Supporti" in printcheck.report(res)
    assert "support" not in printcheck.analyse(*box(20, 20, 20), over={"supports": True})
