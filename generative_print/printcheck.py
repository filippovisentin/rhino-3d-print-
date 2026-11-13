"""Analisi di stampa di un solido qualsiasi (non solo lampade e vasi): la usa lo slicer interno.

`analyse(V, F, settings)` appoggia il pezzo al piano (z minima = 0), poi misura chiusura della
mesh, ingombro rispetto al volume della stampante, sbalzi, isole, appoggio, stabilita e stima
materiale e tempo dalle impostazioni di stampa. Solo numpy.

Le stime di peso e tempo sono geometriche (gusci + riempimento), non un percorso vero:
per i numeri precisi si affetta con uno slicer (vedi generative_print.bambu).
"""
import math

import numpy as np

from . import checks

DEFAULTS = {"layer": 0.2, "line_width": 0.45, "walls": 2, "infill": 0.15, "top_layers": 5, "bottom_layers": 4,
            "density_g_cm3": 1.24, "filament_diameter": 1.75, "flow_mm3_s": 6.0,
            "bed": [256.0, 256.0, 256.0], "overhang_limit_deg": 43.0, "supports": False}


def settings(over=None):
    s = dict(DEFAULTS)
    s.update(over or {})
    return s


def material(V, F, s):
    """Volume estruso stimato (mm3): pareti sulle facce ripide, strati pieni su quelle piane,
    riempimento nel resto. Mai piu del volume del solido."""
    vol, _ = checks.volume_centroid(V, F)
    n, areas = checks.face_normals_areas(V, F)
    flat = np.abs(n[:, 2]) > math.cos(math.radians(45.0))
    up = flat & (n[:, 2] > 0)
    down = flat & (n[:, 2] < 0)
    h = float(s["layer"])
    shell = (float(areas[~flat].sum()) * float(s["line_width"]) * int(s["walls"])
             + float((areas[up] * np.abs(n[up, 2])).sum()) * h * int(s["top_layers"])
             + float((areas[down] * np.abs(n[down, 2])).sum()) * h * int(s["bottom_layers"]))
    shell = min(vol, shell)
    return vol, shell + float(s["infill"]) * max(0.0, vol - shell)


def support_volume(V, F, over_mask, density=0.12):
    """Stima grossolana del volume dei supporti: colonna sotto ogni faccia oltre soglia, fino al piano."""
    if not over_mask.any():
        return 0.0
    n, areas = checks.face_normals_areas(V, F)
    zc = V[:, 2][F[over_mask]].mean(axis=1)
    return float((areas[over_mask] * np.abs(n[over_mask, 2]) * zc).sum()) * density


SUPPORT_DENSITY = 0.12          # frazione di materiale dentro il volume dei supporti (ipotesi)


def support_columns(V, F, over_mask, raycast, spacing=3.0):
    """Dove andrebbero i supporti: una colonna per ogni punto di una griglia orizzontale (passo
    'spacing') che cade sotto una faccia oltre soglia. Ogni colonna scende dalla faccia fino al piano
    oppure fino al primo pezzo di modello che incontra. Restituisce (N x 4): x, y, z alto, z basso.
    raycast(origini, direzioni) -> distanze, come checks.raycast_numpy(V, F)."""
    pts = []
    for a, b, c in V[F[over_mask]]:
        lo = np.minimum(np.minimum(a, b), c)[:2]
        hi = np.maximum(np.maximum(a, b), c)[:2]
        xs = np.arange(math.ceil(lo[0] / spacing), math.floor(hi[0] / spacing) + 1) * spacing
        ys = np.arange(math.ceil(lo[1] / spacing), math.floor(hi[1] / spacing) + 1) * spacing
        if len(xs) == 0 or len(ys) == 0:
            continue
        gx, gy = np.meshgrid(xs, ys)
        p = np.c_[gx.ravel(), gy.ravel()]
        v0, v1 = (b - a)[:2], (c - a)[:2]
        den = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(den) < 1e-12:
            continue
        d = p - a[:2]
        u = (d[:, 0] * v1[1] - d[:, 1] * v1[0]) / den
        w = (v0[0] * d[:, 1] - v0[1] * d[:, 0]) / den
        inside = (u >= 0) & (w >= 0) & (u + w <= 1)
        if inside.any():
            z = a[2] + u[inside] * (b[2] - a[2]) + w[inside] * (c[2] - a[2])
            pts.append(np.c_[p[inside], z])
    if not pts:
        return np.zeros((0, 4))
    P = np.vstack(pts)
    P = P[np.unique(np.round(P[:, :3], 3), axis=0, return_index=True)[1]]
    P = P[P[:, 2] > 0.05]
    if len(P) == 0:
        return np.zeros((0, 4))
    down = np.tile([0.0, 0.0, -1.0], (len(P), 1))
    dist = np.asarray(raycast(P - [0.0, 0.0, 0.02], down), dtype=float)
    bottom = np.where(np.isfinite(dist), np.maximum(0.0, P[:, 2] - 0.02 - dist), 0.0)
    return np.c_[P, bottom]


def analyse(V, F, over=None, raycast=None, support_spacing=3.0):
    """raycast (facoltativo): se dato e i supporti sono attivi, li stima a colonne (support_columns)."""
    s = settings(over)
    V = np.asarray(V, dtype=float)
    lift = float(V[:, 2].min())
    V = V - np.array([0.0, 0.0, lift])
    lim = float(s["overhang_limit_deg"])
    topo = checks.topology(V, F)
    closed = bool(topo["watertight"]) and topo["bodies"] >= 1
    size = V.max(axis=0) - V.min(axis=0)
    bed = [float(b) for b in s["bed"]]
    fits = (size[2] <= bed[2] + 1e-6) and ((size[0] <= bed[0] + 1e-6 and size[1] <= bed[1] + 1e-6)
                                           or (size[1] <= bed[0] + 1e-6 and size[0] <= bed[1] + 1e-6))
    # di quanto andrebbe ridotto per entrare (1 = entra gia), provando i due versi sul piano
    fit_scale = min(1.0, max(min(bed[0] / size[0], bed[1] / size[1]), min(bed[1] / size[0], bed[0] / size[1])),
                    bed[2] / size[2])
    ang, areas, on_bed = checks.overhang_angles(V, F)
    free = ~on_bed & (areas > 1e-9)
    over_mask = free & (ang > lim + checks.ANGLE_TOL)
    warn_mask = free & (ang > lim - 10.0) & ~over_mask
    max_deg = float(ang[free].max()) if free.any() else 0.0
    over_area = float(areas[over_mask].sum())
    islands = len(checks.local_minima(V, F))
    contact = float(areas[on_bed].sum())
    needs_support = over_area > 1.0 or islands > 0
    sup_on = bool(s["supports"])

    rows = [("Mesh chiusa", closed, "%d spigoli aperti, %d corpi" % (topo["naked_edges"], topo["bodies"])),
            ("Ingombro", bool(fits), "%.1f x %.1f x %.1f mm (stampante %g x %g x %g)" % (tuple(size) + tuple(bed))),
            ("Sbalzi", (not over_area > 1.0) or sup_on,
             "max %.1f gradi (limite %g), %.0f mm2 oltre%s" % (max_deg, lim, over_area,
                                                              "  -> con supporti" if over_area > 1.0 and sup_on else "")),
            ("Isole", islands == 0 or sup_on, "%d partenze nel vuoto" % islands),
            ("Appoggio", contact >= 100.0, "%.0f mm2 sul piano" % contact)]
    out = {"settings": s, "lift_mm": lift, "fit_scale": float(fit_scale), "size": [float(x) for x in size], "closed": closed, "fits": bool(fits),
           "overhang_max_deg": max_deg, "overhang_area_mm2": over_area, "islands": islands,
           "contact_mm2": contact, "needs_support": bool(needs_support),
           "over_mask": over_mask, "warn_mask": warn_mask,
           "layers": int(math.ceil(float(size[2]) / float(s["layer"])))}
    if closed:
        st = checks.stability(V, F)
        rows.append(("Stabilita", st["margin_mm"] > 0, "baricentro a %.1f mm dal bordo dell'appoggio" % st["margin_mm"]))
        vol, ext = material(V, F, s)
        sup = 0.0
        if sup_on and needs_support:
            if raycast is not None:
                cols = support_columns(V, F, over_mask, raycast, support_spacing)
                out["support_columns"] = cols
                sup = float((cols[:, 2] - cols[:, 3]).sum()) * support_spacing ** 2 * SUPPORT_DENSITY
            else:
                sup = support_volume(V, F, over_mask, SUPPORT_DENSITY)
        rho = float(s["density_g_cm3"]) / 1000.0
        fil = math.pi * (float(s["filament_diameter"]) / 2.0) ** 2
        out.update({"volume_cm3": vol / 1000.0, "extruded_cm3": (ext + sup) / 1000.0,
                    "support_cm3": sup / 1000.0, "mass_g": (ext + sup) * rho,
                    "filament_m": (ext + sup) / fil / 1000.0,
                    "time_h": (ext + sup) / float(s["flow_mm3_s"]) / 3600.0})
    out["rows"] = rows
    out["ok"] = all(ok for _, ok, _ in rows)
    return out


def report(res):
    """Testo a larghezza fissa per il pannello."""
    L = ["%s %-12s %s" % ("OK" if ok else "NO", lab, txt) for lab, ok, txt in res["rows"]]
    L.append("")
    if res["lift_mm"] > 0.01:
        L.append("Il pezzo e sollevato di %.2f mm: qui e considerato appoggiato al piano." % res["lift_mm"])
    if "mass_g" in res:
        s = res["settings"]
        L.append("Strati %d da %g mm | pareti %d da %g mm | riempimento %d%%"
                 % (res["layers"], s["layer"], s["walls"], s["line_width"], round(100 * s["infill"])))
        L.append("Volume del solido %.1f cm3 | materiale estruso %.1f cm3" % (res["volume_cm3"], res["extruded_cm3"]))
        L.append("Peso %.1f g | filo %.1f m | tempo circa %.1f h (stime geometriche)"
                 % (res["mass_g"], res["filament_m"], res["time_h"]))
        if res.get("support_cm3", 0) > 0:
            cols = res.get("support_columns")
            L.append("di cui supporti circa %.1f cm3%s (stima: riempiti al %d%%)"
                     % (res["support_cm3"], (", %d colonne in anteprima" % len(cols)) if cols is not None else "",
                        round(100 * SUPPORT_DENSITY)))
    else:
        L.append("Mesh aperta: volume, peso e tempo non calcolabili. Chiudi il solido.")
    if not res["fits"]:
        L.append("NON ENTRA nella stampante: andrebbe ridotto al %d%% (oppure prova un altro orientamento)."
                 % int(math.floor(100 * res["fit_scale"])))
    if res["needs_support"] and not res["settings"]["supports"]:
        L.append("ATTENZIONE: servono supporti (zone rosse) oppure va cambiato l'orientamento.")
    L.append("ESITO: %s" % ("STAMPABILE" if res["ok"] else "DA CORREGGERE"))
    return "\n".join(L)


# ---------------------------------------------------------------- orientamento
def rotation_to_down(d):
    """Matrice 3x3 che porta la direzione d (del modello) a puntare verso il basso (0, 0, -1)."""
    d = np.asarray(d, dtype=float)
    d = d / np.linalg.norm(d)
    t = np.array([0.0, 0.0, -1.0])
    c = float(np.dot(d, t))
    if c > 1.0 - 1e-12:
        return np.eye(3)
    if c < -1.0 + 1e-12:
        return np.diag([1.0, -1.0, -1.0])               # mezzo giro attorno a x
    v = np.cross(d, t)
    K = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    return np.eye(3) + K + K @ K / (1.0 + c)


def rotate(V, R):
    """Ruota attorno al centro dell'ingombro e riappoggia alla quota minima di partenza."""
    V = np.asarray(V, dtype=float)
    c = (V.max(axis=0) + V.min(axis=0)) / 2.0
    W = (V - c) @ np.asarray(R, dtype=float).T + c
    W[:, 2] += V[:, 2].min() - W[:, 2].min()
    return W


def candidate_directions(V, F, max_faces=10):
    """Direzioni 'giu' da provare: i sei assi e le normali dei gruppi di facce piane piu estesi."""
    n, areas = checks.face_normals_areas(V, F)
    dirs = [np.array(a, dtype=float) for a in ((0, 0, -1), (0, 0, 1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))]
    key = np.round(n * 20.0).astype(np.int64)            # gruppi di normali entro circa 3 gradi
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = np.asarray(inv).ravel()
    tot = np.bincount(inv, weights=areas, minlength=len(uniq))
    for g in np.argsort(-tot)[:max_faces]:
        if tot[g] < 0.02 * float(areas.sum()):
            break
        m = inv == g
        d = (n[m] * areas[m, None]).sum(axis=0)
        d = d / max(np.linalg.norm(d), 1e-12)
        if all(float(np.dot(d, e)) < 0.995 for e in dirs):
            dirs.append(d)
    return dirs


def orientations(V, F, over=None):
    """Prova le direzioni candidate e le ordina dalla migliore: meno area a sbalzo, poi nessuna isola,
    poi piu appoggio, poi pezzo piu basso. Ogni voce ha la matrice R da passare a rotate()."""
    s = settings(over)
    lim = float(s["overhang_limit_deg"])
    bed = [float(b) for b in s["bed"]]
    out = []
    for d in candidate_directions(V, F):
        R = rotation_to_down(d)
        W = rotate(V, R)
        W[:, 2] -= W[:, 2].min()
        ang, areas, on_bed = checks.overhang_angles(W, F)
        free = ~on_bed & (areas > 1e-9)
        over_area = float(areas[free & (ang > lim + checks.ANGLE_TOL)].sum())
        size = W.max(axis=0) - W.min(axis=0)
        fits = size[2] <= bed[2] + 1e-6 and ((size[0] <= bed[0] + 1e-6 and size[1] <= bed[1] + 1e-6)
                                             or (size[1] <= bed[0] + 1e-6 and size[0] <= bed[1] + 1e-6))
        out.append({"down": [round(float(x), 4) for x in d], "R": R, "overhang_area_mm2": over_area,
                    "contact_mm2": float(areas[on_bed].sum()), "height": float(size[2]), "fits": bool(fits),
                    "islands": len(checks.local_minima(W, F))})
    out.sort(key=lambda o: (not o["fits"], round(o["overhang_area_mm2"], 0), o["islands"],
                            -round(o["contact_mm2"], 0), o["height"]))
    return out
