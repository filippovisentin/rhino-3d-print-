"""Test dei pennelli di scultura: zona locale, pareti sottili, maschera e pennelli nuovi."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generative_print import checks, sculpt  # noqa: E402


def plate(n=41, size=40.0, thickness=None):
    """Piastra quadrata (griglia n x n a z = 10); con thickness una seconda pelle staccata sotto."""
    g = np.linspace(0.0, size, n)
    X, Y = np.meshgrid(g, g, indexing="ij")
    V = np.c_[X.ravel(), Y.ravel(), np.full(n * n, 10.0)]
    i, j = np.meshgrid(np.arange(n - 1), np.arange(n - 1), indexing="ij")
    a = (i * n + j).ravel()
    F = np.vstack([np.c_[a, a + n, a + n + 1], np.c_[a, a + n + 1, a + 1]])
    if thickness:
        V = np.vstack([V, V - [0, 0, thickness]])
        F = np.vstack([F, F[:, ::-1] + n * n])
    return V, F


C = np.array([20.0, 20.0, 10.0])


def test_topology_local_mean_and_normals_match_the_global_ones():
    V, F = plate()
    V[:, 2] += 0.3 * np.sin(V[:, 0]) * np.cos(V[:, 1])
    topo = sculpt.Topology(F, len(V))
    idx = np.array([0, 5, 400, 860, len(V) - 1])
    assert np.allclose(topo.mean(V, idx), sculpt.neighbor_mean(V, sculpt.edge_list(F))[idx])
    cross = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    assert np.allclose(topo.vertex_normals(cross, idx), checks.vertex_normals(V, F)[idx])
    assert set(topo.faces_of(np.array([0]))) == set(np.where((F == 0).any(axis=1))[0])


def test_connected_brush_does_not_cross_a_thin_wall():
    V, F = plate(thickness=1.0)
    n = len(V) // 2
    topo, nrm = sculpt.Topology(F, len(V)), checks.vertex_normals(V, F)
    seed = int(np.argmin(np.linalg.norm(V - C, axis=1)))
    both = sculpt.dab(V.copy(), nrm, sculpt.edge_list(F), C, 6.0, 1.0, "draw")
    top = sculpt.dab(V, nrm, None, C, 6.0, 1.0, "draw", topo=topo, seed=seed)
    assert (both >= n).any() and (top < n).all() and len(top) == (both < n).sum()
    assert V[seed, 2] == pytest.approx(11.0) and np.allclose(V[n:, 2], 9.0)     # sotto non si muove


def test_mask_protects_half_of_the_dab():
    V, F = plate()
    topo, nrm = sculpt.Topology(F, len(V)), checks.vertex_normals(V, F)
    mask = (V[:, 0] > 20.0).astype(float)
    W = V.copy()
    sculpt.dab(W, nrm, None, C, 8.0, 2.0, "draw", topo=topo, mask=mask)
    assert np.allclose(W[mask > 0], V[mask > 0]) and W[:, 2].max() == pytest.approx(12.0)


def test_layer_brush_never_exceeds_its_height():
    V, F = plate()
    topo, nrm = sculpt.Topology(F, len(V)), checks.vertex_normals(V, F)
    W = V.copy()
    for _ in range(12):
        sculpt.dab(W, nrm, None, C, 8.0, 1.5, "layer", topo=topo, base=V)
    assert W[:, 2].max() == pytest.approx(11.5) and (W[:, 2] > 11.49).sum() > 20


def test_crease_is_narrow_scrape_removes_only_peaks_and_clay_only_adds():
    V, F = plate()
    topo, nrm = sculpt.Topology(F, len(V)), checks.vertex_normals(V, F)
    W = V.copy()
    sculpt.dab(W, nrm, None, C, 8.0, 2.0, "crease", topo=topo)
    assert W[:, 2].min() == pytest.approx(10.0 - 1.4)
    assert (W[:, 2] < 9.3).sum() < (np.linalg.norm(V - C, axis=1) < 4).sum()
    W = V.copy()
    W[np.linalg.norm(V - C, axis=1) < 2.0, 2] += 1.0
    for _ in range(8):
        sculpt.dab(W, checks.vertex_normals(W, F), None, C, 8.0, 0.8, "scrape", topo=topo)
    assert W[:, 2].max() < 10.2 and W[:, 2].min() > 9.99
    W = V.copy()
    for _ in range(5):
        sculpt.dab(W, checks.vertex_normals(W, F), None, C, 8.0, 1.0, "clay", topo=topo)
    assert 10.3 < W[:, 2].max() < 12.0 and W[:, 2].min() >= 10.0
