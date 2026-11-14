"""Generazione dei supporti di stampa: dove servono e in tre tipologie (solo numpy).

- "snug"  normale aderente: colonne esattamente sotto le zone a sbalzo;
- "grid"  normale a griglia: come sopra, allargato di circa 1,5 mm (blocchi piu stabili);
- "tree"  ad albero: punte sotto le zone a sbalzo, rami che scendono inclinati, si uniscono
          ed evitano il pezzo cercando una via libera fino al piano.

Il pezzo deve essere appoggiato al piano (z minima = 0). Il calcolo usa un campionamento
verticale della mesh su una griglia orizzontale (passo `res`): per ogni cella, le quote in cui
la verticale attraversa la superficie e il verso della faccia attraversata. Da li si ricava
quanta aria c'e sotto ogni faccia a sbalzo, senza lanciare raggi.

Sono supporti di ANTEPRIMA e di stima (dove vanno, quanto materiale): il percorso vero di
stampa lo genera lo slicer.
"""
import math

import numpy as np

TYPES = ("grid", "snug", "tree")
DEFAULTS = {"support_type": "grid", "support_bed_only": False, "support_z_gap": 0.2,
            "support_spacing": 2.5, "support_interface_layers": 2, "support_expand": 1.5,
            "tree_branch_angle": 40.0, "tree_tip_spacing": 5.0, "tree_tip_radius": 0.6,
            "tree_max_radius": 6.0}
BASE_DENSITY = 0.13          # materiale dentro il volume di un supporto normale (ipotesi)
INTERFACE_DENSITY = 0.8      # strati di interfaccia sotto il pezzo
PAD = 8                      # celle vuote attorno all'ingombro


def _opt(s, key):
    return (s or {}).get(key, DEFAULTS[key])


def grid_resolution(V, target_cells=40000.0):
    size = V[:, :2].max(axis=0) - V[:, :2].min(axis=0)
    return float(np.clip(math.sqrt(max(float(size[0] * size[1]), 1.0) / target_cells), 1.0, 3.0))


# ---------------------------------------------------------------- campionamento verticale
def ldi(V, F, res):
    """Attraversamenti verticali della mesh su una griglia di passo res.
    Restituisce un dizionario con, ordinati per (cella, quota): cell, z, face, down (faccia rivolta
    in basso); piu origin, res, shape (nx, ny) e keys (per le ricerche)."""
    V = np.asarray(V, dtype=float)
    T = V[F]
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    nz = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    ok = np.abs(nz) > 1e-12
    # centri di cella leggermente sfalsati: non cadono su vertici o spigoli a coordinate tonde
    origin = V[:, :2].min(axis=0) - res * PAD - np.array([0.0137, 0.0071]) * res
    size = V[:, :2].max(axis=0) - origin
    shape = (int(math.ceil(size[0] / res)) + PAD, int(math.ceil(size[1] / res)) + PAD)
    lo = (T[:, :, :2].min(axis=1) - origin) / res - 0.5
    hi = (T[:, :, :2].max(axis=1) - origin) / res - 0.5
    i0 = np.ceil(lo).astype(np.int64)
    n = np.clip(np.floor(hi).astype(np.int64) - i0 + 1, 0, None)
    cnt = n[:, 0] * n[:, 1]
    cnt[~ok] = 0
    total = int(cnt.sum())
    tid = np.repeat(np.arange(len(F)), cnt)
    loc = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    ny_t = np.maximum(n[tid, 1], 1)
    ii = i0[tid, 0] + loc // ny_t
    jj = i0[tid, 1] + loc % ny_t
    px = origin[0] + (ii + 0.5) * res
    py = origin[1] + (jj + 0.5) * res
    at = a[tid]
    v0, v1 = (b - a)[tid], (c - a)[tid]
    d0, d1 = px - at[:, 0], py - at[:, 1]
    den = nz[tid]
    u = (d0 * v1[:, 1] - d1 * v1[:, 0]) / den
    w = (v0[:, 0] * d1 - v0[:, 1] * d0) / den
    inside = (u >= 0) & (w >= 0) & (u + w <= 1)
    z = at[inside, 2] + u[inside] * v0[inside, 2] + w[inside] * v1[inside, 2]
    cell = ii[inside] * shape[1] + jj[inside]
    face = tid[inside]
    down = den[inside] < 0
    order = np.lexsort((z, cell))
    cell, z, face, down = cell[order], z[order], face[order], down[order]
    if len(z) > 1:                                   # punto su uno spigolo comune: contato due volte
        dup = np.r_[False, (cell[1:] == cell[:-1]) & (np.abs(z[1:] - z[:-1]) < 1e-7) & (down[1:] == down[:-1])]
        cell, z, face, down = cell[~dup], z[~dup], face[~dup], down[~dup]
    top = float(V[:, 2].max())
    L = {"cell": cell, "z": z, "face": face, "down": down, "origin": origin, "res": float(res),
         "shape": shape, "top": top}
    L["keys"] = cell + _frac(L, z)
    return L


def _frac(L, z):
    return (np.clip(np.asarray(z, dtype=float), -0.5, L["top"] + 1.0) + 1.0) / (L["top"] + 3.0)


def below(L, cell, z):
    """Per ogni (cella, quota): l'attraversamento piu alto sotto quella quota.
    Restituisce (trovato, quota, faccia rivolta in basso)."""
    cell = np.asarray(cell, dtype=np.int64)
    pos = np.searchsorted(L["keys"], cell + _frac(L, z), side="left") - 1
    safe = np.clip(pos, 0, max(len(L["z"]) - 1, 0))
    if len(L["z"]) == 0:
        f = np.zeros(len(cell), dtype=bool)
        return f, np.zeros(len(cell)), f
    found = (pos >= 0) & (L["cell"][safe] == cell)
    return found, np.where(found, L["z"][safe], 0.0), found & L["down"][safe]


def cell_of(L, xy):
    ij = np.floor((np.asarray(xy, dtype=float) - L["origin"]) / L["res"]).astype(np.int64)
    return ij[:, 0], ij[:, 1]


def cell_xy(L, cell):
    ny = L["shape"][1]
    return np.c_[L["origin"][0] + (cell // ny + 0.5) * L["res"], L["origin"][1] + (cell % ny + 0.5) * L["res"]]


# ---------------------------------------------------------------- supporti normali
def _unique_columns(cell, zt, zb):
    """Una colonna sola per vuoto d'aria (stessa cella e stesso fondo): quella che arriva piu in alto."""
    if len(cell) == 0:
        return cell, zt, zb
    order = np.argsort(-zt, kind="stable")
    cell, zt, zb = cell[order], zt[order], zb[order]
    key = cell.astype(np.float64) * 1e4 + np.round(zb, 2)
    keep = np.unique(key, return_index=True)[1]
    return cell[keep], zt[keep], zb[keep]


def overhang_columns(L, over_mask, island_points=None):
    """Colonne aderenti: sotto ogni attraversamento di una faccia oltre soglia (e sotto ogni isola)
    fino al pezzo sottostante o al piano. Restituisce (cella, z alto, z basso)."""
    sel = np.where(L["down"] & np.asarray(over_mask, dtype=bool)[L["face"]] & (L["z"] > 0.05))[0]
    prev = np.maximum(sel - 1, 0)
    same = (sel > 0) & (L["cell"][prev] == L["cell"][sel])
    cell, zt, zb = L["cell"][sel], L["z"][sel], np.where(same, L["z"][prev], 0.0)
    if island_points is not None and len(island_points):
        P = np.asarray(island_points, dtype=float)
        i, j = cell_of(L, P[:, :2])
        c = i * L["shape"][1] + j
        found, z, inside = below(L, c, P[:, 2] - 1e-3)
        cell = np.r_[cell, c[~inside]]
        zt = np.r_[zt, P[~inside, 2]]
        zb = np.r_[zb, z[~inside]]
    return _unique_columns(cell, zt, zb)


def expand_columns(L, cell, zt, zb, cells):
    """Allarga le colonne di 'cells' celle tutt'attorno (stile a griglia). Una colonna nuova si
    accetta solo se la sua cima e in aria; scende fino al pezzo sottostante o al piano."""
    if cells < 1 or len(cell) == 0:
        return cell, zt, zb
    ny = L["shape"][1]
    r = np.arange(-cells, cells + 1)
    di, dj = [x.ravel() for x in np.meshgrid(r, r, indexing="ij")]
    keep = (di != 0) | (dj != 0)
    off = (di * ny + dj)[keep]
    nc = (cell[:, None] + off[None, :]).ravel()
    nt = np.repeat(zt, len(off))
    order = np.argsort(-nt, kind="stable")           # per cella basta la cima piu alta a ogni quota
    nc, nt = nc[order], nt[order]
    k = np.unique(nc.astype(np.float64) * 1e4 + np.round(nt, 1), return_index=True)[1]
    nc, nt = nc[k], nt[k]
    found, z, inside = below(L, nc, nt - 1e-6)
    ok = ~inside
    return _unique_columns(np.r_[cell, nc[ok]], np.r_[zt, nt[ok]], np.r_[zb, z[ok]])


def layer_pattern(sup, z, layer=0.2):
    """Tratti di supporto normale alla quota z: (base, interfaccia), ciascuno (N, 2, 2).
    Base: linee lungo X ogni 'spacing'. Interfaccia (ultimi strati sotto il pezzo): linee fitte lungo Y."""
    cols = sup.get("columns")
    empty = np.zeros((0, 2, 2))
    if cols is None or len(cols) == 0 or sup["type"] == "tree":
        return empty, empty
    res, ij = sup["res"], sup["cells"]
    act = (cols[:, 3] <= z) & (cols[:, 2] >= z)
    face = act & (cols[:, 2] - z < sup["interface_layers"] * layer)
    base = act & ~face
    out = []
    for mask, along, spacing in ((base, 0, sup["spacing"]), (face, 1, res)):
        if not mask.any():
            out.append(empty)
            continue
        run, row = ij[mask, along], ij[mask, 1 - along]
        pos = cols[mask, 1 - along]                        # coordinata trasversale del centro cella
        line = np.round(pos / spacing)
        on = np.abs(pos - line * spacing) <= res / 2.0 + 1e-9
        run, row, pos = run[on], row[on], (line[on] * spacing if spacing > res else pos[on])
        coord = cols[mask, along][on]
        order = np.lexsort((run, row))
        run, row, pos, coord = run[order], row[order], pos[order], coord[order]
        if len(run) == 0:
            out.append(empty)
            continue
        brk = np.r_[True, (row[1:] != row[:-1]) | (run[1:] - run[:-1] != 1)]
        first = np.where(brk)[0]
        last = np.r_[first[1:] - 1, len(run) - 1]
        seg = np.zeros((len(first), 2, 2))
        seg[:, 0, along], seg[:, 1, along] = coord[first] - res / 2.0, coord[last] + res / 2.0
        seg[:, 0, 1 - along] = seg[:, 1, 1 - along] = pos[first]
        out.append(seg)
    return out[0], out[1]


# ---------------------------------------------------------------- supporti ad albero
def voxels(L, dz):
    """Griglia piena/vuota del pezzo (nx, ny, nk) ricavata dagli attraversamenti."""
    nx, ny = L["shape"]
    nk = int(math.ceil(L["top"] / dz)) + 2
    delta = np.zeros((nx * ny, nk + 1), dtype=np.int16)
    d = L["down"]
    np.add.at(delta, (L["cell"][d], np.clip(np.floor(L["z"][d] / dz).astype(np.int64), 0, nk)), 1)
    np.add.at(delta, (L["cell"][~d], np.clip(np.ceil(L["z"][~d] / dz).astype(np.int64), 0, nk)), -1)
    return (np.cumsum(delta, axis=1)[:, :nk] > 0).reshape(nx, ny, nk)


def _dilate_xy(a):
    b = a.copy()
    b[1:] |= a[:-1]
    b[:-1] |= a[1:]
    c = b.copy()
    c[:, 1:] |= b[:, :-1]
    c[:, :-1] |= b[:, 1:]
    return c


def _distance_to(free, limit=30):
    """Distanza in celle (di Chebyshev, fino a 'limit') dalla cella libera piu vicina."""
    D = np.where(free, 0, limit + 1).astype(np.int32)
    reach = free.copy()
    for it in range(1, limit + 1):
        grown = _dilate_xy(reach)
        new = grown & ~reach
        if not new.any():
            break
        D[new] = it
        reach = grown
    return D


def tree_tips(L, cell, zt, zb, spacing):
    """Punte dell'albero: una per ogni riquadro di lato 'spacing' delle zone da sostenere."""
    if len(cell) == 0:
        return np.zeros((0, 3))
    xy = cell_xy(L, cell)
    m = max(1, int(round(spacing / L["res"])))
    ny = L["shape"][1]
    key = ((cell // ny) // m) * 100003.0 + ((cell % ny) // m) + np.round(zt / spacing) * 1e10
    uniq, inv = np.unique(key, return_inverse=True)
    inv = np.asarray(inv).ravel()
    cnt = np.bincount(inv).astype(float)
    cx = np.bincount(inv, weights=xy[:, 0]) / cnt
    cy = np.bincount(inv, weights=xy[:, 1]) / cnt
    d = (xy[:, 0] - cx[inv]) ** 2 + (xy[:, 1] - cy[inv]) ** 2
    order = np.lexsort((d, inv))
    first = order[np.r_[True, inv[order][1:] != inv[order][:-1]]]
    return np.c_[xy[first], zt[first]]


def grow_tree(L, tips, s=None, dz=None):
    """Fa scendere i rami dalle punte fino al piano (o al pezzo). Restituisce (segmenti, statistiche):
    segmenti (N x 8) = x0, y0, z0, r0 (alto), x1, y1, z1, r1 (basso)."""
    res = L["res"]
    dz = float(dz or res)
    nx, ny = L["shape"]
    blocked = _dilate_xy(voxels(L, dz))
    nk = blocked.shape[2]
    clear = np.cumsum(blocked, axis=2) == 0              # via libera in verticale fino al piano
    move = dz * math.tan(math.radians(float(_opt(s, "tree_branch_angle"))))
    r_tip, r_max = float(_opt(s, "tree_tip_radius")), float(_opt(s, "tree_max_radius"))
    ang = np.arange(8) * (math.pi / 4.0)
    ring = np.c_[np.cos(ang), np.sin(ang)] * move
    merge_tol = max(res, 1.0) * 0.9

    def radius(dist, weight):
        return np.minimum(r_max, r_tip + 0.05 * dist + 0.25 * (np.sqrt(weight) - 1.0))

    tips = np.asarray(tips, dtype=float).reshape(-1, 3)
    tip_k = np.clip(np.ceil(tips[:, 2] / dz).astype(int), 1, nk)
    P, Z = np.zeros((0, 2)), np.zeros(0)
    W, Dist, Grace = np.zeros(0), np.zeros(0), np.zeros(0, dtype=int)
    segs, stats = [], {"tips": len(tips), "on_bed": 0, "on_model": 0}
    for k in range(int(tip_k.max()) if len(tips) else 0, 0, -1):
        new = tip_k == k
        if new.any():
            P = np.vstack([P, tips[new, :2]])
            Z = np.r_[Z, tips[new, 2]]
            W = np.r_[W, np.ones(int(new.sum()))]
            Dist = np.r_[Dist, np.zeros(int(new.sum()))]
            Grace = np.r_[Grace, np.full(int(new.sum()), 2)]
        n = len(P)
        if n == 0:
            continue
        target = P.copy()                                # il vicino piu prossimo, se raggiungibile
        if n > 1:
            dm = np.linalg.norm(P[:, None, :] - P[None, :, :], axis=2)
            np.fill_diagonal(dm, np.inf)
            nn = dm.argmin(axis=1)
            near = dm[np.arange(n), nn] < min(25.0, 2.0 * move * k)
            target[near] = P[nn[near]]
        to = target - P
        dist_t = np.linalg.norm(to, axis=1)
        step = to * (np.minimum(move, dist_t / 2.0) / np.maximum(dist_t, 1e-9))[:, None]
        cand = np.concatenate([P[:, None, :], (P + step)[:, None, :], P[:, None, :] + ring[None, :, :]], axis=1)
        ci = np.floor((cand[:, :, 0] - L["origin"][0]) / res).astype(np.int64)
        cj = np.floor((cand[:, :, 1] - L["origin"][1]) / res).astype(np.int64)
        inside = (ci >= 0) & (ci < nx) & (cj >= 0) & (cj < ny)
        si, sj = np.clip(ci, 0, nx - 1), np.clip(cj, 0, ny - 1)
        kk = min(k - 1, nk - 1)
        blk = inside & blocked[si, sj, kk]
        free = clear[:, :, kk]
        cost = np.linalg.norm(cand - target[:, None, :], axis=2) + 1e6 * blk
        if not (free[si[:, 0], sj[:, 0]] | ~inside[:, 0]).all():
            cost = cost + 100.0 * np.where(inside, _distance_to(free)[si, sj], 0)
        g = Grace > 0                                    # appena sotto il pezzo: giu dritti
        cost[g, 1:] = np.inf
        cost[g, 0] = 0.0
        best = cost.argmin(axis=1)
        chosen = cand[np.arange(n), best]
        landed = (cost[np.arange(n), best] >= 1e6) & ~g  # nessuna via: il ramo poggia sul pezzo
        stats["on_model"] += int(landed.sum())
        z1 = (k - 1) * dz
        go = ~landed
        length = np.sqrt(((chosen - P) ** 2).sum(axis=1) + (Z - z1) ** 2)
        r0 = radius(Dist, W)
        Dist = Dist + length
        r1 = radius(Dist, W)
        if go.any():
            segs.append(np.c_[P[go], Z[go], r0[go], chosen[go], np.full(int(go.sum()), z1), r1[go]])
        P, Z, W, Dist, Grace = chosen[go], np.full(int(go.sum()), z1), W[go], Dist[go], np.maximum(Grace[go] - 1, 0)
        if len(P) > 1:                                   # rami che si toccano diventano uno solo
            dm = np.linalg.norm(P[:, None, :] - P[None, :, :], axis=2)
            alive = np.ones(len(P), dtype=bool)
            for i in range(len(P)):
                if not alive[i]:
                    continue
                close = np.where(alive & (dm[i] < merge_tol) & (np.arange(len(P)) > i))[0]
                if len(close):
                    W[i] += W[close].sum()
                    Dist[i] = max(Dist[i], Dist[close].max())
                    Grace[i] = max(Grace[i], Grace[close].max())
                    alive[close] = False
            P, Z, W, Dist, Grace = P[alive], Z[alive], W[alive], Dist[alive], Grace[alive]
    stats["on_bed"] = len(P)
    stats["trunks"] = len(P)
    return (np.vstack(segs) if segs else np.zeros((0, 8))), stats


def tree_volume(segs, line_width=0.45, walls=1.0):
    """Materiale dei rami: guscio vuoto di 'walls' linee, mai piu del ramo pieno (mm3)."""
    if len(segs) == 0:
        return 0.0
    length = np.linalg.norm(segs[:, 4:7] - segs[:, 0:3], axis=1)
    r = (segs[:, 3] + segs[:, 7]) / 2.0
    return float((length * np.minimum(math.pi * r * r, 2.0 * math.pi * r * line_width * walls)).sum())


def tree_sections(segs, z):
    """Sezioni dei rami alla quota z: (N x 3) x, y, raggio."""
    if len(segs) == 0:
        return np.zeros((0, 3))
    m = (segs[:, 6] <= z) & (segs[:, 2] > z)
    s = segs[m]
    t = ((s[:, 2] - z) / np.maximum(s[:, 2] - s[:, 6], 1e-9))[:, None]
    return np.c_[s[:, 0:2] + t * (s[:, 4:6] - s[:, 0:2]), s[:, 3] + t[:, 0] * (s[:, 7] - s[:, 3])]


def tree_mesh(segs, sides=8):
    """Mesh dei rami (un tronco di cono per segmento) per il disegno e l'esportazione: (V, F)."""
    n = len(segs)
    if n == 0:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    a = np.arange(sides) * (2.0 * math.pi / sides)
    circle = np.c_[np.cos(a), np.sin(a), np.zeros(sides)]
    top = segs[:, None, 0:3] + circle[None] * segs[:, 3][:, None, None]
    bot = segs[:, None, 4:7] + circle[None] * segs[:, 7][:, None, None]
    V = np.concatenate([top, bot], axis=1).reshape(-1, 3)
    i = np.arange(sides)
    j = (i + 1) % sides
    quad = np.concatenate([np.c_[i, i + sides, j + sides], np.c_[i, j + sides, j]])
    F = (quad[None] + (np.arange(n) * 2 * sides)[:, None, None]).reshape(-1, 3)
    return V, F


# ---------------------------------------------------------------- ingresso unico
def generate(V, F, over_mask, s=None, island_points=None, res=None):
    """Calcola i supporti dove servono. s: impostazioni (vedi DEFAULTS, piu layer e line_width).
    Restituisce un dizionario: type, res, columns (x, y, z alto, z basso), cells, on_model,
    segments e stats (solo albero), volume_mm3 (materiale stimato), envelope_mm3 (volume occupato)."""
    V = np.asarray(V, dtype=float)
    kind = _opt(s, "support_type")
    if kind not in TYPES:
        raise ValueError("tipo di supporto sconosciuto: %s" % kind)
    res = float(res or (s or {}).get("support_res") or grid_resolution(V))
    layer = float((s or {}).get("layer", 0.2))
    lw = float((s or {}).get("line_width", 0.45))
    gap = float(_opt(s, "support_z_gap"))
    L = ldi(V, F, res)
    cell, zt, zb = overhang_columns(L, over_mask, island_points)
    if kind == "grid":
        cell, zt, zb = expand_columns(L, cell, zt, zb, max(1, int(round(float(_opt(s, "support_expand")) / res))))
    if _opt(s, "support_bed_only"):
        keep = zb <= 1e-9
        cell, zt, zb = cell[keep], zt[keep], zb[keep]
    on_model = zb > 1e-9
    zt, zb = zt - gap, zb + np.where(on_model, gap, 0.0)
    keep = zt - zb > max(gap, 0.05)
    cell, zt, zb, on_model = cell[keep], zt[keep], zb[keep], on_model[keep]
    ny = L["shape"][1]
    out = {"type": kind, "res": res, "spacing": float(_opt(s, "support_spacing")),
           "interface_layers": int(_opt(s, "support_interface_layers")),
           "cells": np.c_[cell // ny, cell % ny], "on_model": on_model,
           "columns": np.c_[cell_xy(L, cell), zt, zb] if len(cell) else np.zeros((0, 4)),
           "segments": np.zeros((0, 8)), "stats": {}}
    height = zt - zb
    out["envelope_mm3"] = float(height.sum()) * res * res
    if kind == "tree":
        tips = tree_tips(L, cell, zt, zb, float(_opt(s, "tree_tip_spacing")))
        segs, stats = grow_tree(L, tips, s)
        out["segments"], out["stats"], out["tips"] = segs, stats, tips
        out["volume_mm3"] = tree_volume(segs, lw)
    else:
        face = np.minimum(height, out["interface_layers"] * layer)
        out["volume_mm3"] = float((BASE_DENSITY * (height - face) + INTERFACE_DENSITY * face).sum()) * res * res
    return out


def describe(sup):
    """Una riga per il rapporto."""
    names = {"grid": "normali a griglia", "snug": "normali aderenti", "tree": "ad albero"}
    n = len(sup["columns"])
    if n == 0:
        return "Supporti %s: nessuno necessario con queste impostazioni." % names[sup["type"]]
    txt = "Supporti %s: %.1f cm3 di materiale" % (names[sup["type"]], sup["volume_mm3"] / 1000.0)
    if sup["type"] == "tree":
        st = sup["stats"]
        txt += ", %d punte -> %d tronchi sul piano" % (st["tips"], st["on_bed"])
        if st["on_model"]:
            txt += ", %d rami poggiano sul pezzo" % st["on_model"]
    else:
        txt += ", area sostenuta %.0f mm2" % (n * sup["res"] ** 2)
        k = int(sup["on_model"].sum())
        if k:
            txt += ", %d %% poggia sul pezzo" % round(100.0 * k / n)
    return txt + " (stima)"
