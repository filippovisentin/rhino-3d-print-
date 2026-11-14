"""Percorsi di uno strato oltre alle pareti: riempimento pieno e rado, supporti.

Le pareti nascono in Rhino (spostamento dei contorni). Qui, in solo numpy: dove lo strato deve
essere pieno (poco materiale sopra o sotto, dal campionamento verticale `supports.ldi`), il
tratteggio diviso in pieno e rado, e i supporti trasformati in percorsi per `gcode`.
"""
import math

import numpy as np

from . import gcode, slicer, supports


def above(L, cell, z):
    """Gemella di `supports.below`: l'attraversamento piu basso sopra la quota.
    Restituisce (trovato, quota, faccia rivolta in basso)."""
    cell = np.asarray(cell, dtype=np.int64)
    n = len(L["z"])
    if n == 0:
        f = np.zeros(len(cell), dtype=bool)
        return f, np.zeros(len(cell)), f
    pos = np.searchsorted(L["keys"], cell + supports._frac(L, z), side="right")
    safe = np.clip(pos, 0, n - 1)
    found = (pos < n) & (L["cell"][safe] == cell)
    return found, np.where(found, L["z"][safe], 0.0), found & L["down"][safe]


def fill_resolution(V):
    """Passo della griglia per gli strati pieni: 0,5 mm sui pezzi piccoli, cresce con l'ingombro."""
    size = V[:, :2].max(axis=0) - V[:, :2].min(axis=0)
    return float(np.clip(math.sqrt(max(float(size[0] * size[1]), 1.0) / 60000.0), 0.5, 2.0))


def exterior(L):
    """Solo gli attraversamenti della pelle vera del pezzo: tolti quelli interni (corpi che si
    toccano o si compenetrano, come due oggetti stampati insieme). A pari quota conta prima l'ingresso."""
    order = np.lexsort((~L["down"], np.round(L["z"], 6), L["cell"]))
    cell, z, down, face = L["cell"][order], L["z"][order], L["down"][order], L["face"][order]
    if len(z) == 0:
        return L
    step = np.where(down, 1, -1)
    cum = np.cumsum(step)
    first = np.r_[True, cell[1:] != cell[:-1]]
    start = np.maximum.accumulate(np.where(first, np.arange(len(z)), 0))
    inside = cum - (cum - step)[start]                       # corpi attraversati dopo questo punto
    keep = (down & (inside == 1)) | (~down & (inside == 0))
    out = dict(L, cell=cell[keep], z=z[keep], down=down[keep], face=face[keep])
    out["keys"] = out["cell"] + supports._frac(out, out["z"])
    return out


def fill_grid(V, F):
    """Campionamento verticale pronto per `fill_paths` (pezzo appoggiato al piano)."""
    return exterior(supports.ldi(V, F, fill_resolution(V)))


def solid_cells(L, z, h, bottom, top):
    """Griglia (nx, ny) delle celle da riempire in pieno alla quota z, e quante di queste sono nel pezzo.
    Una cella e piena se sotto ha meno di `bottom` strati di materiale o sopra meno di `top`;
    la zona piena si allarga di una cella, e fuori dal pezzo vale come piena (bordi dei contorni)."""
    nx, ny = L["shape"]
    cells = np.arange(nx * ny)
    fb, zb, down_b = supports.below(L, cells, z)
    fa, za, down_a = above(L, cells, z)
    inside = down_b & fa & ~down_a
    deep = inside & (z - zb >= bottom * h) & (za - z >= top * h)
    skin = (inside & ~deep).reshape(nx, ny)
    grown = skin.copy()
    grown[1:, :] |= skin[:-1, :]
    grown[:-1, :] |= skin[1:, :]
    grown[:, 1:] |= skin[:, :-1]
    grown[:, :-1] |= skin[:, 1:]
    return grown | ~inside.reshape(nx, ny), int(skin.sum())


def clip_segments(segs, L, mask, keep=True, min_length=0.0):
    """Parti dei segmenti (K, 2, 2) che stanno sulle celle dove mask == keep (passo: un quarto di cella)."""
    segs = np.asarray(segs, dtype=float)
    if len(segs) == 0:
        return np.zeros((0, 2, 2))
    d = segs[:, 1] - segs[:, 0]
    n = np.maximum(np.ceil(np.hypot(d[:, 0], d[:, 1]) / (L["res"] / 4.0)).astype(np.int64), 1)
    sid = np.repeat(np.arange(len(segs)), n)
    loc = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    t = (loc + 0.5) / n[sid]
    i, j = supports.cell_of(L, segs[sid, 0] + t[:, None] * d[sid])
    nx, ny = mask.shape
    ok = (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
    val = np.zeros(len(sid), dtype=bool)
    val[ok] = mask[i[ok], j[ok]] == keep
    if not keep:
        val[~ok] = False
    prev = np.r_[False, val[:-1] & (sid[1:] == sid[:-1])]
    nxt = np.r_[val[1:] & (sid[1:] == sid[:-1]), False]
    a, b = np.where(val & ~prev)[0], np.where(val & ~nxt)[0]
    t0, t1 = loc[a] / n[sid[a]], (loc[b] + 1.0) / n[sid[b]]
    out = np.stack([segs[sid[a], 0] + t0[:, None] * d[sid[a]], segs[sid[a], 0] + t1[:, None] * d[sid[a]]], axis=1)
    if min_length > 0 and len(out):
        out = out[np.hypot(*(out[:, 1] - out[:, 0]).T) >= min_length]
    return out


def fill_paths(loops, L, z, index, h, line_width, infill, bottom, top):
    """Riempimento di uno strato dentro `loops` (contorni orientati: pieni antiorari, fori orari).
    Righe piene a distanza `gcode.spacing` dove serve, rade a larghezza / densita altrove; la
    direzione si alterna a ogni strato (45 e 135 gradi)."""
    if not loops:
        return []
    angle = 45.0 if index % 2 == 0 else 135.0
    mask, skin = solid_cells(L, z, h, bottom, top)
    dense = infill >= 0.99
    out = []
    if skin or dense:
        seg = slicer.hatch(loops, angle, gcode.spacing(line_width, h), min_length=line_width, winding=True)
        if not dense:
            seg = clip_segments(seg, L, mask, True, min_length=line_width)
        out += [{"type": "solid", "closed": False, "pts": s, "link": 2.0 * line_width} for s in seg]
    if infill > 0.001 and not dense:
        seg = slicer.hatch(loops, angle, line_width / infill, min_length=2.0 * line_width, winding=True)
        if skin:
            seg = clip_segments(seg, L, mask, False, min_length=2.0 * line_width)
        out += [{"type": "sparse", "closed": False, "pts": s, "link": min(1.6 * line_width / infill, 8.0)} for s in seg]
    return out


def support_paths(sup, z, h, sides=16):
    """Supporti alla quota z come percorsi: righe di base e di interfaccia, o anelli dei rami."""
    if sup is None or len(sup["columns"]) == 0:
        return []
    if sup["type"] == "tree":
        a = np.linspace(0.0, 2.0 * math.pi, sides, endpoint=False)
        ring = np.c_[np.cos(a), np.sin(a)]
        return [{"type": "support", "closed": True, "pts": np.array([x, y]) + r * ring}
                for x, y, r in supports.tree_sections(sup["segments"], z).tolist() if r > 0.3]
    base, face = supports.layer_pattern(sup, z, h)
    return ([{"type": "support", "closed": False, "pts": s, "link": 1.6 * sup["spacing"]} for s in base]
            + [{"type": "interface", "closed": False, "pts": s, "link": 1.6 * sup["res"]} for s in face])


def moved(paths, dx, dy):
    """Gli stessi percorsi spostati sul piatto."""
    off = np.array([dx, dy], dtype=float)
    return [dict(p, pts=np.asarray(p["pts"], dtype=float)[:, :2] + off) for p in paths]
