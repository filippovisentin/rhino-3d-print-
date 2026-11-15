"""Test dei percorsi di riempimento e supporto: tavolo (piano 60 x 60 x 5 su una gamba 10 x 10 x 30)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generative_print import gcode, printcheck, slicer, supports, toolpaths  # noqa: E402
from test_supports import table  # noqa: E402


def total(paths, kind):
    return sum(float(np.hypot(*(p["pts"][1:] - p["pts"][:-1]).T).sum()) for p in paths if p["type"] == kind)


def square(a, b, ccw=True):
    p = np.array([[a, a], [b, a], [b, b], [a, b]], dtype=float)
    return p if ccw else p[::-1]


def test_above_is_the_twin_of_below():
    V, F = table()
    L = toolpaths.exterior(supports.ldi(V, F, 1.0))
    i, j = supports.cell_of(L, np.array([[V[:, 0].mean(), V[:, 1].mean()], [V[:, 0].max() - 3.0, V[:, 1].mean()]]))
    cell = i * L["shape"][1] + j
    fa, za, da = toolpaths.above(L, cell, np.array([15.0, 15.0]))
    assert fa.all() and za[0] == 35.0 and not da[0]            # nella gamba: sopra c'e la cima del piano
    assert za[1] == 30.0 and da[1]                             # fuori dalla gamba: il sotto del piano
    raw = supports.ldi(V, F, 1.0)                              # gamba e piano sono due scatole che si toccano:
    assert len(raw["z"]) - len(L["z"]) == 2 * 100              # le due facce interne a z 30 spariscono
    assert not toolpaths.above(L, cell, np.array([36.0, 36.0]))[0].any()


def test_winding_hatch_handles_overlapping_hole():
    outer, hole = square(0, 20), square(15, 25, ccw=False)      # il foro sporge oltre il contorno
    seg = slicer.hatch([outer, hole], 0.0, 1.0, winding=True)
    assert len(seg) and seg[:, :, 0].max() <= 20.0 + 1e-9 and seg[:, :, 1].max() <= 20.0
    inside = seg[(seg[:, 0, 1] > 15.0)]
    assert np.allclose(inside[:, 1, 0], 15.0)                   # accanto al foro le righe si fermano al foro
    assert np.hypot(*(seg[:, 1] - seg[:, 0]).T).sum() == np.hypot(*(slicer.hatch([outer], 0.0, 1.0)[:, 1]
                                                                    - slicer.hatch([outer], 0.0, 1.0)[:, 0]).T).sum() - 25.0


def test_fill_is_solid_near_skins_and_sparse_inside():
    V, F = table()
    L = toolpaths.fill_grid(V, F)
    lo, hi = V[:, :2].min(axis=0), V[:, :2].max(axis=0)
    c = (lo + hi) / 2.0
    leg = [np.array([[c[0] - 4, c[1] - 4], [c[0] + 4, c[1] - 4], [c[0] + 4, c[1] + 4], [c[0] - 4, c[1] + 4]])]
    top = [np.array([[lo[0] + 1, lo[1] + 1], [hi[0] - 1, lo[1] + 1], [hi[0] - 1, hi[1] - 1], [lo[0] + 1, hi[1] - 1]])]
    args = (0.2, 0.45, 0.15, 4, 5)
    first = toolpaths.fill_paths(leg, L, 0.1, 0, *args)
    assert total(first, "solid") > 100.0 and total(first, "sparse") == 0.0          # primo strato: tutto pieno
    mid = toolpaths.fill_paths(leg, L, 15.1, 75, *args)
    assert total(mid, "solid") == 0.0 and 10.0 < total(mid, "sparse") < 40.0        # meta gamba: solo rado
    under = toolpaths.fill_paths(top, L, 30.1, 150, *args)                          # primo strato del piano
    plate = toolpaths.fill_paths(top, L, 32.5, 162, *args)                          # a meta spessore del piano
    roof = toolpaths.fill_paths(top, L, 34.9, 174, *args)
    full = 58.0 * 58.0 / gcode.spacing(0.45, 0.2)
    assert total(roof, "solid") > 0.97 * full and total(roof, "sparse") == 0.0
    assert 0.9 * full < total(under, "solid") < full and total(under, "sparse") > 0  # sopra la gamba non serve pieno
    assert total(plate, "solid") == 0.0 and total(plate, "sparse") > 400.0
    assert all(p["pts"].shape == (2, 2) for p in under if p["type"] == "solid")


def test_support_paths_and_move():
    V, F = table()
    res = printcheck.analyse(V, F, {"supports": True, "support_type": "snug"})
    paths = toolpaths.support_paths(res["support"], 10.1, 0.2)
    assert paths and {p["type"] for p in paths} == {"support"}
    face = toolpaths.support_paths(res["support"], 29.7, 0.2)
    assert {p["type"] for p in face} == {"interface"}
    tree = printcheck.analyse(V, F, {"supports": True, "support_type": "tree"})["support"]
    rings = toolpaths.support_paths(tree, 10.1, 0.2)
    assert rings and all(p["closed"] and len(p["pts"]) == 16 for p in rings)
    shifted = toolpaths.moved(paths, 5.0, -2.0)
    assert np.allclose(shifted[0]["pts"] - paths[0]["pts"], [5.0, -2.0]) and toolpaths.support_paths(None, 1.0, 0.2) == []


def _ring(r, n=240):
    a = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.c_[r * np.cos(a), r * np.sin(a)]


def test_fit_wall_keeps_a_free_wall_whole():
    lw, h = 0.45, 0.2
    sp = gcode.spacing(lw, h)
    outer = toolpaths.wall_record(_ring(20.0 - lw / 2), True, lw)
    pieces, thin, lost = toolpaths.fit_wall(_ring(20.0 - lw / 2 - sp), [outer], lw, h)
    assert len(pieces) == 1 and pieces[0]["closed"] and pieces[0]["width"] == pytest.approx(lw)
    assert thin == 0.0 and lost == 0.0


def test_fit_wall_fills_a_thin_shell_with_a_narrow_line():
    # tubo con parete da 1,2 mm: due linee intere non riempiono, in mezzo resta posto per una stretta
    lw, h, t = 0.45, 0.2, 1.2
    sp = gcode.spacing(lw, h)
    acc = [toolpaths.wall_record(_ring(20.0 - lw / 2), True, lw),
           toolpaths.wall_record(_ring(20.0 - t + lw / 2), True, lw)]
    mid = _ring(20.0 - lw / 2 - sp)
    pieces, thin, lost = toolpaths.fit_wall(mid, acc, lw, h)
    assert lost == 0.0 and thin > 0.0 and len(pieces) == 1
    w = pieces[0]["width"]
    assert 0.33 < w < 0.42
    # sezioni: due linee intere piu quella stretta riempiono la parete entro il 6 %
    area = 2 * gcode.section(lw, h) + gcode.section(w, h)
    assert area == pytest.approx(t * h, rel=0.06)
    # la seconda candidata (dall'altra faccia) non trova piu posto
    acc.append(toolpaths.wall_record(pieces[0]["pts"], False, w))
    pieces2, _, lost2 = toolpaths.fit_wall(_ring(20.0 - t + lw / 2 + sp), acc, lw, h)
    assert pieces2 == [] and lost2 > 100.0


def test_fit_wall_cuts_only_where_two_walls_meet():
    # due cerchi che si avvicinano in un punto: la parete si interrompe li, non tutta
    lw, h = 0.45, 0.2
    other = toolpaths.wall_record(_ring(10.0) + [20.2, 0.0], True, lw)      # a 0,2 mm nel punto piu vicino
    pieces, thin, lost = toolpaths.fit_wall(_ring(10.0), [other], lw, h, between=False)
    assert pieces and all(not p["closed"] for p in pieces)
    assert 0.0 < lost < 6.0
    total = sum(float(np.hypot(*(p["pts"][1:] - p["pts"][:-1]).T).sum()) for p in pieces)
    assert total + lost == pytest.approx(2 * np.pi * 10.0, rel=0.01)


def test_sparse_rows_join_only_inside_the_part():
    # due rettangoli staccati di 2 mm: le righe si uniscono dentro ciascuno, mai sopra il vuoto in mezzo
    a = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 20.0], [0.0, 20.0]])
    b = a + [12.0, 0.0]
    segs = np.array([[[1.0, y], [9.0, y]] for y in (2.0, 5.0, 8.0)] + [[[13.0, y], [21.0, y]] for y in (2.0, 5.0, 8.0)])
    lines = toolpaths.chain(segs, [a, b], 6.0)
    assert len(lines) == 2 and all(len(l) == 6 for l in lines)
    for l in lines:
        assert (l[:, 0] < 11.0).all() or (l[:, 0] > 11.0).all()
    # senza il controllo (un solo contorno che li contiene) si unirebbero in una riga sola
    box = np.array([[-1.0, -1.0], [23.0, -1.0], [23.0, 21.0], [-1.0, 21.0]])
    assert len(toolpaths.chain(segs, [box], 6.0)) == 1
    assert toolpaths.inside(np.array([[5.0, 5.0], [11.0, 5.0]]), [a, b]).tolist() == [True, False]
