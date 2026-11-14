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


def slice_edges(V, F, z):
    """Come slice_segments, ma per ogni segmento restituisce gli spigoli della mesh su cui
    cadono i due estremi: servono a ricostruire i contorni senza confrontare coordinate."""
    zv = V[:, 2][F] - z
    zv = np.where(zv == 0.0, 1e-9, zv)
    above = zv > 0
    cnt = above.sum(axis=1)
    sel = np.where((cnt == 1) | (cnt == 2))[0]
    if len(sel) == 0:
        return np.zeros((0, 2), dtype=np.int64)
    f, ab = F[sel], above[sel]
    lone = np.where(cnt[sel, None] == 1, ab, ~ab).argmax(axis=1)
    r = np.arange(len(sel))
    v0, v1, v2 = f[r, lone], f[r, (lone + 1) % 3], f[r, (lone + 2) % 3]
    n = len(V)
    ea = np.minimum(v0, v1) * n + np.maximum(v0, v1)
    eb = np.minimum(v0, v2) * n + np.maximum(v0, v2)
    return np.stack([ea, eb], axis=1)


def count_loops(V, F, z):
    """Numero di contorni nella sezione alla quota z."""
    e = slice_edges(V, F, z)
    if len(e) == 0:
        return 0
    keys, inv = np.unique(e.ravel(), return_inverse=True)
    inv = np.asarray(inv).reshape(-1, 2)
    parent = np.arange(len(keys))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in inv.tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(i) for i in range(len(keys))})


def loops_profile(V, F, layer=0.2, step_mm=1.0):
    """Contorni per strato, campionati ogni `step_mm`: lista di (z, n_contorni)."""
    top = float(V[:, 2].max())
    zs = np.arange(layer / 2.0, top - layer / 2.0, max(step_mm, layer))
    return [(float(z), count_loops(V, F, z)) for z in zs]


def hatch(loops, angle_deg, spacing, min_length=0.0, winding=False):
    """Tratteggio di riempimento dentro una regione piana (regola pari/dispari).

    loops: lista di poligoni chiusi (N x 2, senza ripetere il primo punto) che delimitano la regione,
    fori compresi. Restituisce i segmenti (K x 2 x 2) di rette parallele inclinate di angle_deg e
    distanti 'spacing', tagliate dentro la regione. I tratti piu corti di min_length si scartano.
    Con winding=True conta il verso dei contorni (pieni antiorari, fori orari) e tiene dove il
    numero di avvolgimenti e positivo: regge anche contorni che si accavallano.
    """
    segs = []
    for p in loops:
        p = np.asarray(p, dtype=float)[:, :2]
        if len(p) >= 3:
            segs.append(np.stack([p, np.roll(p, -1, axis=0)], axis=1))
    if not segs or spacing <= 0:
        return np.zeros((0, 2, 2))
    S = np.concatenate(segs)
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    R = np.array([[c, s], [-s, c]])                     # porta la direzione del tratteggio sull'asse x
    T = S @ R.T
    y0, y1 = T[:, 0, 1], T[:, 1, 1]
    lo, hi = float(min(y0.min(), y1.min())), float(max(y0.max(), y1.max()))
    out = []
    first = (math.floor(lo / spacing) + 0.5) * spacing
    for y in np.arange(first, hi, spacing):
        m = (y0 > y) != (y1 > y)
        if not m.any():
            continue
        t = (y - y0[m]) / (y1[m] - y0[m])
        xs = T[m, 0, 0] + t * (T[m, 1, 0] - T[m, 0, 0])
        if winding:
            o = np.argsort(xs, kind="stable")
            xs = xs[o]
            w = np.cumsum(np.where(y0[m][o] > y1[m][o], 1, -1))      # lato sinistro di un antiorario: scende
            on = np.r_[False, w > 0]
            for k in np.where(on[1:] & ~on[:-1])[0]:
                e = k + int(np.argmax(w[k:] <= 0)) if (w[k:] <= 0).any() else None
                if e is not None and xs[e] - xs[k] >= min_length:
                    out.append([[xs[k], y], [xs[e], y]])
            continue
        xs = np.sort(xs)
        for k in range(0, len(xs) - 1, 2):
            if xs[k + 1] - xs[k] >= min_length:
                out.append([[xs[k], y], [xs[k + 1], y]])
    if not out:
        return np.zeros((0, 2, 2))
    return np.array(out) @ R                             # torna nel sistema originale
