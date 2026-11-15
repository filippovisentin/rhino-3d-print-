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
        # le righe rade si uniscono qui, e non nel G-code: il raccordo e lungo e va controllato che
        # resti dentro il riempimento (tra due denti o attorno a un foro passerebbe sul vuoto)
        out += [{"type": "sparse", "closed": False, "pts": s}
                for s in chain(seg, loops, min(1.6 * line_width / infill, 8.0))]
    return out


def inside(points, loops):
    """Vero per i punti dentro i contorni orientati (regola degli avvolgimenti, come `slicer.hatch`)."""
    P = np.asarray(points, dtype=float)
    wind = np.zeros(len(P), dtype=np.int64)
    for loop in loops:
        A = np.asarray(loop, dtype=float)[:, :2]
        B = np.roll(A, -1, axis=0)
        ax, ay, bx, by = A[:, 0][None], A[:, 1][None], B[:, 0][None], B[:, 1][None]
        px, py = P[:, 0][:, None], P[:, 1][:, None]
        left = (bx - ax) * (py - ay) - (px - ax) * (by - ay)
        up = (ay <= py) & (by > py) & (left > 0)
        down = (ay > py) & (by <= py) & (left < 0)
        wind += up.sum(axis=1) - down.sum(axis=1)
    return wind != 0


def chain(segs, loops, max_link):
    """Unisce a zig-zag i segmenti (K, 2, 2) vicini: da ogni capo si passa al capo libero piu vicino
    entro max_link, ma solo se il raccordo resta dentro `loops`. Restituisce spezzate di punti."""
    segs = np.asarray(segs, dtype=float)
    if len(segs) == 0:
        return []
    free = np.ones(len(segs), dtype=bool)
    ends = segs.reshape(-1, 2)                                   # capo 2k = inizio, 2k + 1 = fine
    out = []
    for first in range(len(segs)):
        if not free[first]:
            continue
        free[first] = False
        line = [segs[first, 0], segs[first, 1]]
        while True:
            d = np.hypot(ends[:, 0] - line[-1][0], ends[:, 1] - line[-1][1])
            d[~np.repeat(free, 2)] = np.inf
            nxt = None
            for e in np.argsort(d)[:4]:
                if d[e] > max_link:
                    break
                mid = line[-1] + np.array([[0.25], [0.5], [0.75]]) * (ends[e] - line[-1])
                if d[e] < 1e-6 or inside(mid, loops).all():
                    nxt = int(e)
                    break
            if nxt is None:
                break
            k = nxt // 2
            free[k] = False
            line += [ends[nxt], ends[nxt ^ 1]]
        out.append(np.array(line))
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


# ----------------------------------------------------------------- pareti che si adattano allo spazio
W_MIN = 0.2          # mm: linea piu stretta che si stampa (ugello da 0,4)


def wall_record(pts, closed, width):
    """Una parete gia accettata, pronta per le misure di distanza di `fit_wall`."""
    P = np.asarray(pts, dtype=float)[:, :2]
    A, B = (P, np.roll(P, -1, axis=0)) if closed else (P[:-1], P[1:])
    return {"A": A, "B": B, "width": float(width), "lo": P.min(axis=0), "hi": P.max(axis=0)}


def _distance(Q, A, B, budget=1000000):
    """Distanza di ogni punto Q (n, 2) dalla spezzata di segmenti A-B (m, 2)."""
    ax, ay = A[:, 0][None], A[:, 1][None]
    dx, dy = (B[:, 0] - A[:, 0])[None], (B[:, 1] - A[:, 1])[None]
    L2 = np.maximum(dx * dx + dy * dy, 1e-18)
    out = np.empty(len(Q))
    step = max(1, budget // max(len(A), 1))
    for s in range(0, len(Q), step):
        px, py = Q[s:s + step, 0][:, None] - ax, Q[s:s + step, 1][:, None] - ay
        t = np.clip((px * dx + py * dy) / L2, 0.0, 1.0)
        px -= t * dx
        py -= t * dy
        out[s:s + step] = np.sqrt((px * px + py * py).min(axis=1))
    return out


def _near_distance(Q, a, reach):
    """Come `_distance`, ma solo dove serve: i punti di Q entro `reach` dall'ingombro della parete `a`
    e i suoi segmenti entro `reach` da quei punti. Altrove restituisce infinito."""
    out = np.full(len(Q), np.inf)
    qm = np.all((Q >= a["lo"] - reach) & (Q <= a["hi"] + reach), axis=1)
    if not qm.any():
        return out
    Qn = Q[qm]
    lo, hi = Qn.min(axis=0) - reach, Qn.max(axis=0) + reach
    A, B = a["A"], a["B"]
    sm = np.all((np.maximum(A, B) >= lo) & (np.minimum(A, B) <= hi), axis=1)
    if sm.any():
        out[qm] = _distance(Qn, A[sm], B[sm])
    return out


def fit_wall(pts, accepted, line_width, h, step=0.4, w_min=W_MIN, tol=0.05, spread=0.1, between=True):
    """Adatta una parete candidata (anello chiuso di punti) allo spazio lasciato da quelle gia accettate.

    Due linee di larghezza w1 e w2 riempiono senza sovrapporsi se i loro assi distano
    (w1 + w2) / 2 - h (1 - pi / 4): da qui, punto per punto, la larghezza che ci sta. Dove ci sta la
    linea intera la parete resta com'e; dove c'e meno spazio (pareti sottili: l'esterno e l'interno si
    incontrano) diventa una linea piu stretta che riempie il vuoto; sotto w_min sparisce.
    between=True (pareti interne): la linea sta tra due vicine, quella del suo stesso contorno e
    un'altra, e prende tutto lo spazio tra le due anche se non e proprio al centro (fino a 0,1 mm
    di scarto). between=False (parete esterna): da un lato c'e il bordo del pezzo, che non si sposta.
    Restituisce (pezzi, mm stretti, mm scartati); ogni pezzo e {"pts", "closed", "width"}."""
    P = np.asarray(pts, dtype=float)[:, :2]
    if len(P) < 3:
        return [], 0.0, 0.0
    c = h * (1.0 - math.pi / 4.0)
    near = [a for a in accepted if np.all(a["lo"] - line_width <= P.max(axis=0))
            and np.all(a["hi"] + line_width >= P.min(axis=0))]
    if not near:
        return [{"pts": P, "closed": True, "width": line_width}], 0.0, 0.0
    nxt = np.roll(P, -1, axis=0)
    edge = np.hypot(*(nxt - P).T)
    n = np.maximum(np.ceil(edge / step).astype(np.int64), 1)
    sid = np.repeat(np.arange(len(P)), n)
    loc = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    Q = P[sid] + (loc / n[sid])[:, None] * (nxt - P)[sid]          # anello infittito; loc == 0: vertice vero
    v1, v2 = np.full(len(Q), np.inf), np.full(len(Q), np.inf)     # spazio libero verso le due piu vicine
    for a in near:
        v = _near_distance(Q, a, line_width + a["width"]) - a["width"] / 2.0
        v2 = np.minimum(v2, np.maximum(v1, v))
        v1 = np.minimum(v1, v)
    w = (v1 + np.minimum(v2, v1 + 0.2) + 2.0 * c) if between else 2.0 * (v1 + c)
    w = np.minimum(w, float(line_width))
    w[w >= line_width - tol] = line_width
    if np.all(w >= line_width):
        return [{"pts": P, "closed": True, "width": line_width}], 0.0, 0.0
    ok = w >= w_min
    seg = np.hypot(*(np.roll(Q, -1, axis=0) - Q).T)
    lost = float(seg[~ok].sum())
    if not ok.any():
        return [], 0.0, lost
    m = len(Q)
    start = int(np.flatnonzero(~ok)[0]) if not ok.all() else int(np.argmin(w))
    order = (np.arange(m + (1 if ok.all() else 0)) + start) % m    # giro che parte da un punto di taglio
    pieces, thin, k = [], 0.0, 0
    while k < len(order):
        if not ok[order[k]]:
            k += 1
            continue
        e, lo, hi = k, w[order[k]], w[order[k]]
        while e + 1 < len(order) and ok[order[e + 1]]:
            lo2, hi2 = min(lo, w[order[e + 1]]), max(hi, w[order[e + 1]])
            if hi2 - lo2 > spread:
                break
            e, lo, hi = e + 1, lo2, hi2
        idx = order[k:e + 1]
        if e + 1 < len(order) and ok[order[e + 1]]:
            idx = order[k:e + 2]                                    # il pezzo dopo riparte dallo stesso punto
        keep = np.ones(len(idx), dtype=bool)
        keep[1:-1] = loc[idx[1:-1]] == 0                            # dentro restano solo i vertici veri
        pts_k = Q[idx][keep]
        length = float(np.hypot(*(pts_k[1:] - pts_k[:-1]).T).sum()) if len(pts_k) > 1 else 0.0
        width = float(np.clip(w[order[k:e + 1]].mean(), w_min, line_width))
        if length >= 2.0 * w_min:
            pieces.append({"pts": pts_k, "closed": False, "width": width})
            if width < line_width - 1e-9:
                thin += length
        else:
            lost += length
        k = e + 1
    return pieces, thin, lost
