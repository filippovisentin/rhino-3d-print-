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
