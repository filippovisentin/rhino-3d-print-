"""Test delle misure su solidi dal risultato noto. Esegui con: pytest -q"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import checks, generators, mesh_io, sculpt, slicer  # noqa: E402

trimesh = pytest.importorskip("trimesh")


def cone_skin(r0, r1, height, nz=60, nt=96):
    u = np.linspace(0, 1, nz)
    th = np.linspace(0, 2 * math.pi, nt, endpoint=False)
    uu, tt = np.meshgrid(u, th, indexing="ij")
    r = r0 + (r1 - r0) * uu
    return np.stack([r * np.cos(tt), r * np.sin(tt), uu * height], axis=2)


def box(sx, sy, sz):
    m = trimesh.creation.box(extents=[sx, sy, sz])
    m.apply_translation([0, 0, sz / 2.0])
    return np.array(m.vertices), np.array(m.faces)


def test_box_closed_volume_and_bed():
    V, F = box(20, 30, 10)
    topo = checks.topology(V, F)
    assert topo["watertight"] and topo["bodies"] == 1
    vol, cen = checks.volume_centroid(V, F)
    assert vol == pytest.approx(6000.0, rel=1e-9)
    assert cen == pytest.approx([0, 0, 5], abs=1e-9)
    ov = checks.overhang(V, F, 45)
    assert ov["area_over_mm2"] == 0.0            # la faccia inferiore e sul piano
    assert ov["bed_contact_mm2"] == pytest.approx(600.0)


def test_open_mesh_is_detected():
    V, F = box(10, 10, 10)
    topo = checks.topology(V, F[:-1])
    assert not topo["watertight"] and topo["naked_edges"] == 3


def test_raised_box_bottom_is_overhang():
    V, F = box(10, 10, 10)
    V = V + [0, 0, 5.0]
    ov = checks.overhang(V, F, 45)
    assert ov["max_deg"] == pytest.approx(90.0)
    assert ov["area_over_mm2"] == pytest.approx(100.0)


@pytest.mark.parametrize("lean_deg,should_pass", [(30.0, True), (44.0, True), (50.0, False), (60.0, False)])
def test_cone_lean_angle(lean_deg, should_pass):
    """Tronco di cono che si allarga verso l'alto di `lean_deg` dalla verticale."""
    h = 40.0
    r1 = 30.0 + h * math.tan(math.radians(lean_deg))
    V, F = generators.build_shell(cone_skin(30.0, r1, h), wall=2.0, floor=2.0)
    assert checks.topology(V, F)["watertight"]
    ov = checks.overhang(V, F, 45)
    assert ov["max_deg"] == pytest.approx(lean_deg, abs=0.6)
    assert (ov["area_over_mm2"] <= 1.0) == should_pass


def test_inward_cone_overhangs_on_the_inside():
    """Una parete che si chiude verso l'alto ha lo sbalzo sulla faccia interna."""
    V, F = generators.build_shell(cone_skin(60.0, 60.0 - 40.0 * math.tan(math.radians(55)), 40.0),
                                  wall=2.0, floor=2.0)
    ov = checks.overhang(V, F, 45)
    assert ov["max_deg"] == pytest.approx(55.0, abs=0.6)
    assert ov["area_over_mm2"] > 100.0


@pytest.mark.parametrize("wall", [0.6, 1.2, 2.4])
def test_wall_thickness_is_measured(wall):
    V, F = generators.build_shell(cone_skin(40.0, 40.0, 30.0, nz=30, nt=120), wall=wall, floor=3.0)
    th = checks.thickness(V, F, checks.raycast_trimesh(V, F), samples=800)
    assert th["p1"] == pytest.approx(wall, abs=0.03)
    assert th["median"] == pytest.approx(wall, abs=0.03)


def test_numpy_raycaster_matches_trimesh():
    V, F = generators.build_shell(cone_skin(20.0, 25.0, 15.0, nz=10, nt=32), wall=1.5, floor=2.0)
    a = checks.thickness(V, F, checks.raycast_numpy(V, F), samples=120)
    b = checks.thickness(V, F, checks.raycast_trimesh(V, F), samples=120)
    assert a["min"] == pytest.approx(b["min"], abs=1e-6)
    assert a["median"] == pytest.approx(b["median"], abs=1e-6)


def test_cylinder_shell_volume_matches_formula_and_trimesh():
    ro, wall, floor, h, rh = 40.0, 2.0, 3.0, 50.0, 20.0
    V, F = generators.build_shell(cone_skin(ro, ro, h, nz=40, nt=360), wall=wall, floor=floor,
                                  hole_diameter=2 * rh)
    vol, _ = checks.volume_centroid(V, F)
    exact = math.pi * (ro ** 2 - (ro - wall) ** 2) * h + math.pi * ((ro - wall) ** 2 - rh ** 2) * floor
    assert vol == pytest.approx(exact, rel=2e-3)
    tm = trimesh.Trimesh(V, F, process=False)
    assert tm.is_watertight and tm.is_winding_consistent
    assert tm.volume == pytest.approx(vol, rel=1e-9)


def test_hole_and_clearance_are_measured():
    V, F = generators.build_shell(cone_skin(60.0, 60.0, 180.0, nz=60, nt=180), wall=2.0, floor=3.0,
                                  hole_diameter=40.0)
    fx = checks.fixture(V, F, checks.raycast_trimesh(V, F),
                        {"socket_height": 60, "bulb_length": 110, "bulb_diameter": 60}, 3.0)
    assert fx["hole_diameter"] == pytest.approx(40.0, abs=0.05)
    assert fx["bulb_clearance"] == pytest.approx(58.0 - 30.0, abs=0.1)
    assert fx["top_opening"] == pytest.approx(116.0, abs=0.2)


def test_local_minimum_island():
    V, F = generators.icosphere(3, 10.0)
    V = V + [0, 0, 30.0]                              # sfera sospesa: il polo sud e un'isola
    assert len(checks.local_minima(V, F)) == 1
    Vb, Fb = generators.build_blob({"blob": {"radius": 20, "height": 40, "subdivisions": 3}})
    assert len(checks.local_minima(Vb, Fb)) == 0      # base piana sul piano


def test_stability_margin():
    V, F = box(20, 20, 10)
    assert checks.stability(V, F)["margin_mm"] == pytest.approx(10.0)
    V2 = V.copy()
    V2[V2[:, 2] > 5, 0] += 40.0                       # parte alta traslata fuori dall'appoggio
    assert checks.stability(V2, F)["margin_mm"] < 0


def test_vessel_default_is_closed_and_roundtrips_stl(tmp_path):
    skin, V, F = generators.build_vessel({"mesh": {"edge": 3.0}})
    assert checks.topology(V, F)["watertight"]
    vol, _ = checks.volume_centroid(V, F)
    assert vol > 0
    path = str(tmp_path / "v.stl")
    mesh_io.write_stl(path, V, F)
    V2, F2 = mesh_io.read_stl(path)
    assert checks.topology(V2, F2)["watertight"]
    assert checks.volume_centroid(V2, F2)[0] == pytest.approx(vol, rel=1e-4)
    mesh_io.write_3mf(str(tmp_path / "v.3mf"), V, F)
    tm = trimesh.load(str(tmp_path / "v.3mf"), force="mesh")
    assert tm.volume == pytest.approx(vol, rel=1e-4)


def test_slice_perimeter_of_cylinder():
    V, F = generators.build_shell(cone_skin(30.0, 30.0, 20.0, nz=20, nt=360), wall=2.0, floor=0.0)
    info = slicer.layer_info(V, F, 10.1)
    assert info["perimeter_mm"] == pytest.approx(2 * math.pi * (30 + 28), rel=1e-3)


def test_brush_moves_only_inside_radius_and_respects_bed():
    V, F = generators.build_blob({"blob": {"radius": 20, "height": 40, "subdivisions": 3}})
    before = V.copy()
    n = checks.vertex_normals(V, F)
    top = V[np.argmax(V[:, 2])].copy()
    idx = sculpt.dab(V, n, sculpt.edge_list(F), top, 8.0, 3.0, "draw")
    moved = np.where(np.abs(V - before).sum(axis=1) > 0)[0]
    assert set(moved) <= set(idx)
    assert np.linalg.norm(before[idx] - top, axis=1).max() < 8.0
    assert V[:, 2].max() == pytest.approx(before[:, 2].max() + 3.0, abs=1e-6)
    low = V[np.argmin(V[:, 2])].copy()
    sculpt.dab(V, n, sculpt.edge_list(F), low, 10.0, 5.0, "draw", direction=[0, 0, -1])
    sculpt.constrain(V)
    assert V[:, 2].min() >= 0.0
