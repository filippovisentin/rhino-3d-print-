"""Generatori parametrici: restituiscono mesh come array numpy (V: Nx3 mm, F: Mx3).

Due famiglie:
- "vessel": lampade e vasi. Si modella la PELLE esterna (griglia anelli x segmenti);
  il solido stampabile si ricava dalla pelle con `build_shell` (spessore, fondo, foro).
- "blob": solido chiuso con base piana, punto di partenza per la modellazione libera.
"""
import copy
import math

import numpy as np

DEFAULTS = {
    "object": "lamp_shade",
    "units": "mm",
    "envelope": {"height": 200.0},
    # raggi (mm) a quote equidistanti dal basso verso l'alto, interpolati (Catmull-Rom)
    "profile": {"radii": [55.0, 80.0, 90.0, 75.0, 50.0]},
    "ribs": {"count": 24, "amplitude": 0.05, "twist_deg": 60.0, "shape": "sine",
             "fade_bottom": 0.08, "fade_top": 0.0},
    "wall": {"thickness": 1.6},
    "floor": {"thickness": 3.0},
    "fixture": {"hole_diameter": 40.0},
    "mesh": {"segments": 0, "rings": 0, "edge": 1.6},
    "print": {"material": "PLA", "density_g_cm3": 1.24, "nozzle": 0.4, "layer": 0.2,
              "line_width": 0.45, "walls": 2, "infill": 0.15,
              "bed": [256.0, 256.0, 256.0], "overhang_limit_deg": 45.0,
              "flow_mm3_s": 6.0, "filament_diameter": 1.75},
    "constraints": {},
}


def resolve(params):
    """Unisce i parametri dati ai valori predefiniti (un livello di annidamento)."""
    out = copy.deepcopy(DEFAULTS)
    for key, val in (params or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key].update(val)
        else:
            out[key] = val
    return out


def catmull_rom(u, pts):
    """Interpola i valori `pts` (equidistanti su [0,1]) nei punti u."""
    pts = np.asarray(pts, dtype=float)
    n = len(pts)
    if n == 1:
        return np.full(np.shape(u), pts[0])
    x = np.clip(np.asarray(u, dtype=float), 0.0, 1.0) * (n - 1)
    i = np.minimum(x.astype(int), n - 2)
    t = x - i
    m = np.empty(n)
    m[0] = pts[1] - pts[0]
    m[-1] = pts[-1] - pts[-2]
    if n > 2:
        m[1:-1] = (pts[2:] - pts[:-2]) / 2.0
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * pts[i] + (t3 - 2 * t2 + t) * m[i]
            + (-2 * t3 + 3 * t2) * pts[i + 1] + (t3 - t2) * m[i + 1])


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def rib_wave(phase, shape):
    """Onda periodica in [-1, 1] con cresta in phase = 0."""
    if shape == "sharp":
        f = (phase / (2 * math.pi)) % 1.0
        return 2 * np.abs(2 * f - 1) - 1
    if shape == "scallop":   # lobi convessi, gole vive
        return 2 * np.abs(np.cos(phase / 2)) - 1
    if shape == "flute":     # scanalature concave, creste vive
        return 1 - 2 * np.abs(np.sin(phase / 2))
    return np.cos(phase)


def vessel_skin(params):
    """Pelle esterna: array (anelli, segmenti, 3). L'anello 0 sta sul piano z = 0."""
    p = resolve(params)
    height = float(p["envelope"]["height"])
    radii = p["profile"]["radii"]
    ribs = p["ribs"]
    count = int(ribs["count"])
    edge = float(p["mesh"]["edge"])
    nt = int(p["mesh"]["segments"]) or max(96, count * 12)
    nz = int(p["mesh"]["rings"]) or max(24, int(round(height / edge)) + 1)

    u = np.linspace(0.0, 1.0, nz)
    theta = np.linspace(0.0, 2 * math.pi, nt, endpoint=False)
    base_r = catmull_rom(u, radii)
    env = np.ones(nz)
    if ribs.get("fade_bottom", 0) > 0:
        env *= _smoothstep(u / ribs["fade_bottom"])
    if ribs.get("fade_top", 0) > 0:
        env *= _smoothstep((1 - u) / ribs["fade_top"])
    amp = float(ribs["amplitude"]) * env
    twist = math.radians(float(ribs["twist_deg"])) * u

    uu, tt = np.meshgrid(u, theta, indexing="ij")
    wave = rib_wave(count * (tt + twist[:, None]), ribs.get("shape", "sine")) if count > 0 else 0.0
    r = base_r[:, None] * (1.0 + amp[:, None] * wave)
    skin = np.empty((nz, nt, 3))
    skin[..., 0] = r * np.cos(tt)
    skin[..., 1] = r * np.sin(tt)
    skin[..., 2] = (u * height)[:, None]
    return skin


def grid_normals(skin):
    """Normali esterne ai vertici della griglia (periodica in theta, aperta in z)."""
    du = np.roll(skin, -1, axis=1) - np.roll(skin, 1, axis=1)
    dv = np.empty_like(skin)
    dv[1:-1] = skin[2:] - skin[:-2]
    dv[0] = skin[1] - skin[0]
    dv[-1] = skin[-1] - skin[-2]
    n = np.cross(du, dv)
    ln = np.linalg.norm(n, axis=2, keepdims=True)
    return n / np.maximum(ln, 1e-12)


def skin_faces(nz, nt):
    """Triangoli della sola pelle (superficie aperta), normali verso l'esterno."""
    j, i = np.meshgrid(np.arange(nz - 1), np.arange(nt), indexing="ij")
    a = (j * nt + i).ravel()
    b = (j * nt + (i + 1) % nt).ravel()
    c = ((j + 1) * nt + (i + 1) % nt).ravel()
    d = ((j + 1) * nt + i).ravel()
    return np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])


def _ring_quads(ring_a, ring_b):
    """Fascia tra due anelli di indici: triangoli (a_i, a_i+1, b_i+1), (a_i, b_i+1, b_i)."""
    a0, a1 = ring_a, np.roll(ring_a, -1)
    b0, b1 = ring_b, np.roll(ring_b, -1)
    return np.concatenate([np.stack([a0, a1, b1], 1), np.stack([a0, b1, b0], 1)])


def build_shell(skin, wall=1.6, floor=3.0, hole_diameter=0.0, floor_rings=0):
    """Solido chiuso a partire dalla pelle esterna.

    wall: spessore misurato lungo la normale (mm). floor: spessore del fondo (0 = senza
    fondo, tubo aperto). hole_diameter: foro centrale nel fondo (0 = fondo pieno).
    Restituisce (V, F) con normali verso l'esterno del materiale.
    """
    skin = np.array(skin, dtype=float)
    nz, nt = skin.shape[:2]
    skin[0, :, 2] = 0.0
    nrm = grid_normals(skin)
    # anello di base: spostamento solo orizzontale, cosi l'interno resta sul piano
    h = nrm[0].copy()
    h[:, 2] = 0.0
    nrm[0] = h / np.maximum(np.linalg.norm(h, axis=1, keepdims=True), 1e-12)
    inner = skin - wall * nrm

    verts = [skin.reshape(-1, 3)]
    outer_idx = np.arange(nz * nt).reshape(nz, nt)
    inner_idx = (nz * nt + np.arange(nz * nt)).reshape(nz, nt).copy()
    faces = []

    # parete esterna
    faces.append(skin_faces(nz, nt))

    if floor > 0:
        zin = inner[..., 2]
        above = zin >= floor
        if not above.any(axis=0).all():
            raise ValueError("fondo piu alto della parete interna: riduci floor o alza il pezzo")
        k = np.argmax(above, axis=0)                 # primo anello sopra il fondo, per colonna
        k = np.maximum(k, 1)
        cols = np.arange(nt)
        lo, hi = inner[k - 1, cols], inner[k, cols]
        dz = hi[:, 2] - lo[:, 2]
        t = np.where(np.abs(dz) > 1e-12, (floor - lo[:, 2]) / np.where(np.abs(dz) > 1e-12, dz, 1), 0.0)
        t = np.clip(t, 0.0, 1.0)
        cross = lo + (hi - lo) * t[:, None]
        cross[:, 2] = floor
        inner[k - 1, cols] = cross
        # i vertici sotto l'incrocio collassano su quello dell'incrocio (stesso indice)
        jj = np.maximum(np.arange(nz)[:, None], (k - 1)[None, :])
        inner_idx = (nz * nt + jj * nt + cols[None, :])
    verts.append(inner.reshape(-1, 3))

    # parete interna (verso l'asse): triangoli invertiti
    j, i = np.meshgrid(np.arange(nz - 1), np.arange(nt), indexing="ij")
    a = inner_idx[j, i].ravel()
    b = inner_idx[j, (i + 1) % nt].ravel()
    c = inner_idx[j + 1, (i + 1) % nt].ravel()
    d = inner_idx[j + 1, i].ravel()
    faces.append(np.concatenate([np.stack([a, c, b], 1), np.stack([a, d, c], 1)]))

    # bordo superiore
    faces.append(_ring_quads(outer_idx[-1], inner_idx[-1]))

    nvert = 2 * nz * nt
    extra = []

    def add_ring(points):
        nonlocal nvert
        idx = nvert + np.arange(len(points))
        extra.append(points)
        nvert += len(points)
        return idx

    theta = np.linspace(0.0, 2 * math.pi, nt, endpoint=False)
    if floor > 0:
        top_edge = inner_idx[0]                      # anello di incrocio a z = floor
        top_pts = inner.reshape(-1, 3)[top_edge - nz * nt]
        bot_edge = outer_idx[0]
        bot_pts = skin[0]
        rh = hole_diameter / 2.0
        r_edge = float(np.mean(np.hypot(top_pts[:, 0], top_pts[:, 1])))
        if rh >= r_edge - 1.0:
            raise ValueError("foro del fondo troppo grande rispetto alla base")
        span = r_edge - rh
        m = floor_rings or max(1, int(span / max(2 * math.pi * r_edge / nt * 2.0, 1e-6)))
        if rh > 0:
            hole_xy = np.stack([rh * np.cos(theta), rh * np.sin(theta)], 1)
            hole_top = np.column_stack([hole_xy, np.full(nt, floor)])
            hole_bot = np.column_stack([hole_xy, np.zeros(nt)])
            prev = top_edge
            for s in range(1, m):                    # faccia superiore del fondo
                ring = add_ring(top_pts + (hole_top - top_pts) * (s / m))
                faces.append(_ring_quads(prev, ring))
                prev = ring
            ht = add_ring(hole_top)
            faces.append(_ring_quads(prev, ht))
            hb = add_ring(hole_bot)
            faces.append(_ring_quads(ht, hb))         # parete del foro
            prev = hb
            for s in range(1, m):                    # faccia inferiore, dal foro verso fuori
                ring = add_ring(hole_bot + (bot_pts - hole_bot) * (s / m))
                faces.append(_ring_quads(prev, ring))
                prev = ring
            faces.append(_ring_quads(prev, bot_edge))
        else:
            ct = np.array([[0.0, 0.0, floor]])
            cb = np.array([[0.0, 0.0, 0.0]])
            prev = top_edge
            for s in range(1, m):
                ring = add_ring(top_pts + (ct - top_pts) * (s / m))
                faces.append(_ring_quads(prev, ring))
                prev = ring
            ict = add_ring(ct)[0]
            faces.append(np.stack([prev, np.roll(prev, -1), np.full(nt, ict)], 1))
            icb = add_ring(cb)[0]
            prev = None
            rings = [add_ring(cb + (bot_pts - cb) * (s / m)) for s in range(1, m)] + [bot_edge]
            first = rings[0]
            faces.append(np.stack([np.full(nt, icb), np.roll(first, -1), first], 1))
            for ra, rb in zip(rings[:-1], rings[1:]):
                faces.append(_ring_quads(ra, rb))
    else:
        faces.append(_ring_quads(inner_idx[0], outer_idx[0]))  # anello di appoggio

    V = np.vstack(verts + extra)
    F = np.concatenate(faces).astype(np.int64)
    # elimina i triangoli degeneri nati dal collasso e i vertici non usati
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])
    F = F[ok]
    used = np.zeros(len(V), dtype=bool)
    used[F.ravel()] = True
    remap = np.cumsum(used) - 1
    return V[used], remap[F]


def shell_from_params(skin, params):
    p = resolve(params)
    return build_shell(skin, wall=float(p["wall"]["thickness"]),
                       floor=float(p["floor"]["thickness"]),
                       hole_diameter=float(p["fixture"].get("hole_diameter", 0.0)))


def build_vessel(params):
    """Restituisce (skin, V, F): pelle esterna e solido stampabile."""
    skin = vessel_skin(params)
    V, F = shell_from_params(skin, params)
    return skin, V, F


def icosphere(subdivisions=4, radius=1.0):
    t = (1.0 + math.sqrt(5.0)) / 2.0
    V = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t],
                  [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], float)
    F = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9],
                  [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2],
                  [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10],
                  [8, 6, 7], [9, 8, 1]], dtype=np.int64)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    for _ in range(subdivisions):
        n = len(V)
        e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
        key = e[:, 0] * n + e[:, 1]
        uniq, inv = np.unique(key, return_inverse=True)
        mid = V[uniq // n] + V[uniq % n]
        mid /= np.linalg.norm(mid, axis=1, keepdims=True)
        V = np.vstack([V, mid])
        m = n + inv.reshape(3, -1)          # m[0]=ab, m[1]=bc, m[2]=ca
        a, b, c = F[:, 0], F[:, 1], F[:, 2]
        F = np.concatenate([np.stack([a, m[0], m[2]], 1), np.stack([b, m[1], m[0]], 1),
                            np.stack([c, m[2], m[1]], 1), np.stack([m[0], m[1], m[2]], 1)])
    return V * radius, F


def build_blob(params):
    """Solido chiuso per la modellazione libera: ellissoide con base piana sul piano."""
    b = (params or {}).get("blob", {})
    r = float(b.get("radius", 40.0))
    h = float(b.get("height", 2 * r))
    sink = float(b.get("sink", 0.15))        # frazione di altezza tagliata dal piano
    V, F = icosphere(int(b.get("subdivisions", 5)), 1.0)
    full = h / (1.0 - sink)
    # la calotta sotto il piano diventa la base piana: si apre a disco senza sottosquadri
    z_cut = -(1.0 - 2.0 * sink)
    below = V[:, 2] < z_cut
    phi_cut = math.acos(-z_cut)
    phi = np.arccos(np.clip(-V[below, 2], -1.0, 1.0))
    rho = np.hypot(V[below, 0], V[below, 1])
    scale = np.where(rho > 1e-12, math.sin(phi_cut) * (phi / phi_cut) / np.maximum(rho, 1e-12), 0.0)
    V[below, 0] *= scale
    V[below, 1] *= scale
    V[below, 2] = z_cut
    # i vertici subito sopra il taglio vanno sul bordo della base: nessuna faccia a cavallo
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    rim = np.unique(e[below[e[:, 1]] & ~below[e[:, 0]], 0])
    rr = np.hypot(V[rim, 0], V[rim, 1])
    V[rim, 0] *= math.sin(phi_cut) / rr
    V[rim, 1] *= math.sin(phi_cut) / rr
    V[rim, 2] = z_cut
    V = V * np.array([r, r, full / 2.0])
    V[:, 2] += full / 2.0 - sink * full
    V[:, 2] = np.maximum(V[:, 2], 0.0)
    return V, F
