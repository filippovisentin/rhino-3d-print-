"""Test del generatore di vasi su casi dal risultato noto."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import checks, generators, slicer  # noqa: E402

CYL = {"object": "vase", "envelope": {"height": 100.0}, "profile": {"radii": [40.0, 40.0]},
       "ribs": {"count": 0, "amplitude": 0.0}, "mesh": {"segments": 360, "edge": 2.0},
       "wall": {"thickness": 2.0}, "floor": {"thickness": 3.0}}


def merged(base, **over):
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in over.items():
        p.setdefault(k, {}).update(v)
    return p


def test_cylinder_vase_capacity_matches_formula():
    skin, V, F = generators.build_vase(CYL)
    assert checks.topology(V, F)["watertight"]
    cap = generators.capacity_ml(skin, 2.0, 3.0)
    exact = math.pi * 38.0 ** 2 * (100.0 - 3.0) / 1000.0
    assert cap == pytest.approx(exact, rel=2e-3)


def test_vase_has_full_floor_and_two_loops_above_it():
    skin, V, F = generators.build_vase(CYL)
    assert slicer.count_loops(V, F, 1.5) == 1        # fondo pieno: un solo contorno
    assert slicer.count_loops(V, F, 50.0) == 2       # parete: contorno esterno e interno
    res = checks.run_checks(V, F, generators.vase_params(CYL), skin=skin, samples=300)
    ids = {c["id"]: c for c in res["checks"]}
    assert ids["floor"]["pass"] and "socket" not in ids


def test_polygon_section_corners_and_sides():
    p = merged(CYL, section={"sides": 4, "depth": 0.2, "twist_deg": 0.0})
    skin = generators.vessel_skin(p)
    r = np.hypot(skin[10, :, 0], skin[10, :, 1])
    assert r.max() == pytest.approx(40.0, abs=1e-6)          # spigoli al raggio del profilo
    assert r.min() == pytest.approx(40.0 * 0.8, abs=0.05)    # meta dei lati rientra del 20%
    peaks = np.sum((r > np.roll(r, 1)) & (r >= np.roll(r, -1)))
    assert peaks == 4


def test_section_twist_rotates_top_only():
    p = merged(CYL, section={"sides": 3, "depth": 0.15, "twist_deg": 60.0, "ease": False})
    skin = generators.vessel_skin(p)
    def corner_angle(ring):
        r = np.hypot(ring[:, 0], ring[:, 1])
        k = np.argmax(r)
        return math.degrees(math.atan2(ring[k, 1], ring[k, 0])) % 120.0
    def circ(a, b):                                   # distanza su un periodo di 120 gradi
        d = abs(a - b) % 120.0
        return min(d, 120.0 - d)
    # la sezione ruota di +60 gradi: lo spigolo si sposta a -60, cioe a 60 modulo 120
    assert circ(corner_angle(skin[0]), 0.0) < 1.0
    assert circ(corner_angle(skin[-1]), 60.0) < 1.0


def test_spiral_mode_is_a_filled_solid_with_one_loop_per_layer():
    p = merged(CYL, print={"mode": "spiral"})
    skin, V, F = generators.build_vase(p)
    assert checks.topology(V, F)["watertight"]
    vol, _ = checks.volume_centroid(V, F)
    assert vol == pytest.approx(math.pi * 40.0 ** 2 * 100.0, rel=2e-3)
    assert all(n == 1 for _, n in slicer.loops_profile(V, F, 0.2, 5.0))
    res = checks.run_checks(V, F, generators.vase_params(p), skin=skin, samples=100)
    ids = {c["id"]: c for c in res["checks"]}
    assert ids["spiral"]["pass"]
    # una linea da 0,45 mm per 500 strati + 4 strati pieni del fondo
    lw, h = 0.45, 0.2
    section = (lw - h * (1 - math.pi / 4)) * h          # sezione della linea come negli slicer
    expected = (2 * math.pi * 40.0 * (100.0 / h) * section + math.pi * 40.0 ** 2 * h * 4) * 1.24 / 1000.0
    assert res["material"]["mass_est_g"] == pytest.approx(expected, rel=0.01)


def test_spiral_mode_detects_two_islands():
    """Due corpi alla stessa quota danno due contorni: in modalita spirale non si stampa."""
    # due cilindri separati: alla stessa quota ci sono due contorni
    a = generators.cap_skin(generators.vessel_skin(merged(CYL, envelope={"height": 20.0},
                                                         profile={"radii": [10.0, 10.0]})))
    b = (a[0] + [40.0, 0.0, 0.0], a[1] + len(a[0]))
    V3, F3 = np.vstack([a[0], b[0]]), np.vstack([a[1], b[1]])
    assert slicer.count_loops(V3, F3, 10.0) == 2


def test_dispatcher_and_lamp_unchanged():
    skin, V, F = generators.build({"object": "vase", **{k: v for k, v in CYL.items() if k != "object"}})
    assert slicer.count_loops(V, F, 1.5) == 1
    skin2, V2, F2 = generators.build({"mesh": {"edge": 4.0}})
    assert slicer.count_loops(V2, F2, 1.5) == 2      # lampada: fondo con foro


@pytest.mark.parametrize("sides", [3, 4, 6, 8])
def test_rounded_polygon_sharp_is_exact_polygon(sides):
    ang = np.linspace(0, 2 * math.pi, 3600, endpoint=False)
    r = generators.rounded_polygon(ang, sides, 0.0)
    assert r.max() == pytest.approx(1.0, abs=1e-9)                      # spigoli
    assert r.min() == pytest.approx(math.cos(math.pi / sides), abs=1e-6)  # apotema
    # i punti di un lato stanno su una retta: distanza costante dalla retta del lato
    half = math.pi / sides
    side = (ang > 0.1 * half) & (ang < 1.9 * half)
    x, y = r[side] * np.cos(ang[side] - half), r[side] * np.sin(ang[side] - half)
    assert np.ptp(x) < 1e-9


def test_rounded_polygon_corner_radius_and_limits():
    ang = np.linspace(0, 2 * math.pi, 7200, endpoint=False)
    assert np.allclose(generators.rounded_polygon(ang, 6, 1.0), 1.0)      # raccordo totale = cerchio
    r = generators.rounded_polygon(ang, 6, 0.2)
    assert r.max() == pytest.approx(1.0, abs=1e-6)
    # apotema = (1 - rc) cos(30) + rc
    assert r.min() == pytest.approx(0.8 * math.cos(math.pi / 6) + 0.2, abs=1e-6)
    # contorno continuo: nessun salto tra tratto diritto e raccordo
    assert np.max(np.abs(np.diff(r))) < 1e-3
