"""Pennelli di modellazione plastica su array di vertici (solo numpy).

Ogni pennello sposta i vertici entro un raggio dal centro, con decadimento morbido.
La topologia non cambia: cambiano solo le posizioni.
"""
import math

import numpy as np

BRUSHES = ("draw", "inflate", "smooth", "flatten", "pinch", "grab")


def falloff(dist, radius):
    x = np.clip(dist / radius, 0.0, 1.0)
    return (1.0 - x * x) ** 2


def edge_list(F):
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    return e


def neighbor_mean(V, edges):
    n = len(V)
    i = np.concatenate([edges[:, 0], edges[:, 1]])
    j = np.concatenate([edges[:, 1], edges[:, 0]])
    deg = np.bincount(i, minlength=n).astype(float)
    out = np.empty_like(V)
    for k in range(3):
        out[:, k] = np.bincount(i, weights=V[j, k], minlength=n)
    return out / np.maximum(deg, 1.0)[:, None]


def radial_copies(point, vector, count):
    """Copie del colpo ruotate attorno all'asse Z (simmetria radiale)."""
    out = []
    for s in range(max(1, int(count))):
        a = 2 * math.pi * s / max(1, int(count))
        c, sn = math.cos(a), math.sin(a)
        rot = np.array([[c, -sn, 0.0], [sn, c, 0.0], [0.0, 0.0, 1.0]])
        out.append((rot.dot(point), rot.dot(vector) if vector is not None else None))
    return out


def symmetry_copies(point, vector, count=1, mirror=None, centre=(0.0, 0.0)):
    """Copie del colpo: 'count' copie ruotate attorno all'asse verticale che passa per 'centre', poi
    (mirror = "x" oppure "y") le speculari rispetto al piano verticale x = centre[0] o y = centre[1].
    Un colpo che sta sul piano di specchio non viene raddoppiato."""
    c3 = np.array([centre[0], centre[1], 0.0])
    point = np.asarray(point, dtype=float)
    out = [(p + c3, v) for p, v in radial_copies(point - c3, vector, count)]
    if mirror in ("x", "y"):
        k = 0 if mirror == "x" else 1
        flip = np.ones(3)
        flip[k] = -1.0
        extra = []
        for p, v in out:
            q = (p - c3) * flip + c3
            if all(np.linalg.norm(q - o) > 1e-6 for o, _ in out):
                extra.append((q, v * flip if v is not None else None))
        out += extra
    return out


def dab(V, normals, edges, center, radius, strength, brush="draw", direction=None,
        locked=None, grab_delta=None):
    """Applica un colpo di pennello in-place. Restituisce gli indici dei vertici mossi.

    strength: spostamento massimo in mm (negativo = scava). Per "smooth" e "flatten"
    e una frazione 0..1. `direction`: normale media del colpo (per draw/flatten).
    """
    center = np.asarray(center, dtype=float)
    d = np.linalg.norm(V - center, axis=1)
    idx = np.where(d < radius)[0]
    if locked is not None and len(idx):
        idx = idx[~locked[idx]]
    if len(idx) == 0:
        return idx
    w = falloff(d[idx], radius)[:, None]
    if brush == "draw":
        if direction is None:
            direction = normals[idx].mean(axis=0)
        direction = np.asarray(direction, dtype=float)
        direction = direction / max(np.linalg.norm(direction), 1e-12)
        V[idx] += direction * strength * w
    elif brush == "inflate":
        V[idx] += normals[idx] * strength * w
    elif brush == "smooth":
        mean = neighbor_mean(V, edges)[idx]
        V[idx] += (mean - V[idx]) * min(abs(strength), 1.0) * w
    elif brush == "flatten":
        if direction is None:
            direction = normals[idx].mean(axis=0)
        direction = np.asarray(direction, dtype=float)
        direction = direction / max(np.linalg.norm(direction), 1e-12)
        origin = (V[idx] * w).sum(axis=0) / max(w.sum(), 1e-12)
        height = (V[idx] - origin).dot(direction)[:, None]
        V[idx] -= direction * height * min(abs(strength), 1.0) * w
    elif brush == "pinch":
        V[idx] += (center - V[idx]) * min(abs(strength), 1.0) * w * 0.5
    elif brush == "grab":
        V[idx] += np.asarray(grab_delta, dtype=float) * w
    else:
        raise ValueError("pennello sconosciuto: %s" % brush)
    return idx


def constrain(V, base_ring=None, floor_z=0.0):
    """Vincoli di stampa durante la modellazione: niente sotto il piano, base a z = 0."""
    np.maximum(V[:, 2], floor_z, out=V[:, 2])
    if base_ring is not None:
        V[base_ring, 2] = floor_z


def relax_overhang(skin, solid_fn, limit_deg, target_margin=0.3, strength=0.5, grow=2, max_iter=60):
    """Correzione automatica degli sbalzi: liscia la pelle solo dove il solido supera il limite.

    skin: griglia (anelli, segmenti, 3). solid_fn(skin) -> (V, F) del solido (guscio compreso, cosi
    contano anche le facce interne). Le facce oltre limit_deg - target_margin vengono riportate al
    punto della pelle piu vicino; la zona si allarga di 'grow' passi di griglia e lì ogni punto si
    avvicina alla media dei quattro vicini, solo in orizzontale (le quote restano fisse). Il primo e
    l'ultimo anello non si toccano. Si ripete finche il massimo rientra o si esaurisce max_iter.
    Restituisce (pelle corretta, registro delle passate).
    """
    from .checks import overhang_angles
    skin = np.array(skin, dtype=float)
    nz, nt = skin.shape[:2]
    flat = skin.reshape(-1, 3)
    target = float(limit_deg) - float(target_margin)
    log = []
    for it in range(max_iter + 1):
        V, F = solid_fn(skin)
        ang, areas, bed = overhang_angles(V, F)
        free = ~bed & (areas > 1e-9)
        bad = free & (ang > target)
        log.append({"pass": it, "max_deg": round(float(ang[free].max()), 2),
                    "faces_over": int(bad.sum()), "area_over_mm2": round(float(areas[bad].sum()), 1)})
        if not bad.any() or it == max_iter:
            break
        C = V[F[bad]].mean(axis=1)
        # punto della pelle piu vicino a ogni faccia oltre il limite (solo anelli vicini in z)
        mask = np.zeros((nz, nt), dtype=bool)
        zr = skin[:, :, 2].mean(axis=1)
        for c in C:
            r0 = int(np.clip(np.searchsorted(zr, c[2]), 0, nz - 1))
            lo, hi = max(0, r0 - 3), min(nz, r0 + 4)
            d = np.linalg.norm(skin[lo:hi] - c, axis=2)
            k = np.unravel_index(np.argmin(d), d.shape)
            mask[lo + k[0], k[1]] = True
        for _ in range(grow):                                   # allarga la zona sulla griglia
            m = mask.copy()
            m[1:] |= mask[:-1]
            m[:-1] |= mask[1:]
            m |= np.roll(mask, 1, axis=1) | np.roll(mask, -1, axis=1)
            mask = m
        mask[0] = False
        mask[-1] = False
        avg = 0.25 * (np.roll(skin, 1, axis=1) + np.roll(skin, -1, axis=1)
                      + np.vstack([skin[:1], skin[:-1]]) + np.vstack([skin[1:], skin[-1:]]))
        new = skin.copy()
        new[mask, :2] += strength * (avg[mask, :2] - skin[mask, :2])
        skin = new
    return skin, log


def relax_overhang_mesh(V, F, edges, limit_deg, region=None, two_sided=False, horizontal=False,
                        fixed=None, target_margin=0.3, strength=0.5, grow=1, max_iter=40):
    """Riporta sotto il limite di sbalzo una mesh qualsiasi, in-place, lisciando solo dove serve.

    region: indici dei vertici appena scolpiti; si correggono solo le facce che li toccano (gli
    sbalzi che c'erano gia altrove non vengono toccati). two_sided: parete sottile, conta
    l'inclinazione in entrambi i versi (pelle di lampade e vasi). horizontal: sposta solo in x e y.
    fixed: maschera dei vertici che non si muovono (di base quelli appoggiati al piano).
    Restituisce {"moved": indici mossi, "passes", "max_deg", "ok"} riferiti alla zona corretta.
    """
    from .checks import BED_TOL, face_normals_areas
    target = float(limit_deg) - float(target_margin)
    locked = (V[:, 2] <= BED_TOL) if fixed is None else np.asarray(fixed, dtype=bool)
    near = None
    if region is not None:
        near = np.zeros(len(V), dtype=bool)
        near[np.asarray(region, dtype=np.int64)] = True
        for _ in range(2):                                   # la zona scolpita e due anelli attorno
            m = near.copy()
            m[edges[:, 0]] |= near[edges[:, 1]]
            m[edges[:, 1]] |= near[edges[:, 0]]
            near = m
    moved = np.zeros(len(V), dtype=bool)
    worst, passes = 0.0, 0
    for it in range(max_iter + 1):
        n, areas = face_normals_areas(V, F)
        lean = np.abs(n[:, 2]) if two_sided else np.clip(-n[:, 2], 0.0, 1.0)
        if not two_sided:
            lean = np.where(V[:, 2][F].max(axis=1) <= BED_TOL, 0.0, lean)
        ang = np.degrees(np.arcsin(np.clip(lean, 0.0, 1.0)))
        cand = areas > 1e-9
        if near is not None:
            cand &= near[F].any(axis=1)
        bad = cand & (ang > target)
        worst = float(ang[cand].max()) if cand.any() else 0.0
        passes = it
        if not bad.any() or it == max_iter:
            break
        sel = np.zeros(len(V), dtype=bool)
        sel[np.unique(F[bad])] = True
        for _ in range(grow):
            m = sel.copy()
            m[edges[:, 0]] |= sel[edges[:, 1]]
            m[edges[:, 1]] |= sel[edges[:, 0]]
            sel = m
        sel &= ~locked
        if not sel.any():
            break
        avg = neighbor_mean(V, edges)
        k = 2 if horizontal else 3
        V[sel, :k] += strength * (avg[sel, :k] - V[sel, :k])
        moved |= sel
    return {"moved": np.where(moved)[0], "passes": passes, "max_deg": worst, "ok": worst <= float(limit_deg) + 0.5}
