"""Anteprima a strati: sezione della mesh con piani orizzontali (solo numpy)."""
import math

import numpy as np


def slice_segments(V, F, z):
    """Segmenti (Kx2x3) dell'intersezione tra la mesh e il piano di quota z."""
    zv = V[:, 2][F] - z
    zv = np.where(zv == 0.0, 1e-9, zv)              # evita vertici esattamente sul piano
    above = zv > 0
    cnt = above.sum(axis=1)
    sel = np.where((cnt == 1) | (cnt == 2))[0]
    if len(sel) == 0:
        return np.zeros((0, 2, 3))
    f, zf, ab = F[sel], zv[sel], above[sel]
    # vertice isolato (unico sopra o unico sotto), poi gli altri due in ordine ciclico
    lone = np.where(cnt[sel, None] == 1, ab, ~ab).argmax(axis=1)
    r = np.arange(len(sel))
    i0, i1, i2 = lone, (lone + 1) % 3, (lone + 2) % 3
    p0, p1, p2 = V[f[r, i0]], V[f[r, i1]], V[f[r, i2]]
    z0, z1, z2 = zf[r, i0], zf[r, i1], zf[r, i2]
    a = p0 + (p1 - p0) * (z0 / (z0 - z1))[:, None]
    b = p0 + (p2 - p0) * (z0 / (z0 - z2))[:, None]
    return np.stack([a, b], axis=1)


def layer_info(V, F, z):
    seg = slice_segments(V, F, z)
    length = float(np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum()) if len(seg) else 0.0
    return {"z": float(z), "segments": int(len(seg)), "perimeter_mm": length}


def layer_heights(V, layer=0.2):
    top = float(V[:, 2].max())
    n = int(math.ceil(top / layer))
    return (np.arange(n) + 0.5) * layer


def faces_below(V, F, z):
    """Facce interamente sotto la quota z: il pezzo 'gia stampato' nell'anteprima."""
    return F[(V[:, 2][F].max(axis=1) <= z)]
