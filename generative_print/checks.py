"""Verifiche di stampabilita FDM su una mesh triangolare (V: Nx3 mm, F: Mx3).

Ogni funzione misura sulla mesh e non si fida dei parametri di generazione.
Convenzione: il piano di stampa e z = 0, l'asse di stampa e +Z.
"""
import math

import numpy as np

BED_TOL = 0.05      # mm: sotto questa quota un vertice e considerato appoggiato al piano
ANGLE_TOL = 0.5     # gradi: tolleranza numerica sul limite di sbalzo


# ---------------------------------------------------------------- geometria di base
def face_normals_areas(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1)
    return n / np.maximum(ln, 1e-30)[:, None], ln / 2.0


def vertex_normals(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    fn = np.cross(b - a, c - a)                     # pesate per area
    out = np.zeros((len(V), 3))
    idx = F.ravel()
    for k in range(3):
        out[:, k] = np.bincount(idx, weights=np.repeat(fn[:, k], 3), minlength=len(V))
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-30)


def volume_centroid(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    six = np.einsum("ij,ij->i", a, np.cross(b, c))
    vol = six.sum() / 6.0
    if abs(vol) < 1e-12:
        return 0.0, V.mean(axis=0)
    cen = ((a + b + c) * six[:, None]).sum(axis=0) / (24.0 * vol)
    return float(vol), cen


def directed_edges(F):
    return np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])


# ---------------------------------------------------------------- topologia
def topology(V, F):
    """Mesh chiusa e orientata: ogni spigolo orientato ha esattamente un opposto."""
    n = len(V)
    e = directed_edges(F).astype(np.int64)
    key = e[:, 0] * n + e[:, 1]
    rkey = e[:, 1] * n + e[:, 0]
    uniq, cnt = np.unique(key, return_counts=True)
    duplicated = int((cnt > 1).sum())
    naked = int((~np.isin(rkey, uniq)).sum())
    _, areas = face_normals_areas(V, F)
    degenerate = int((areas < 1e-9).sum())
    # corpi separati (union-find sui vertici usati)
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    und = np.unique(np.sort(e, axis=1), axis=0)
    for x, y in und.tolist():
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry
    used = np.unique(F)
    bodies = len({find(int(x)) for x in used.tolist()})
    return {"naked_edges": naked, "duplicated_edges": duplicated,
            "degenerate_faces": degenerate, "bodies": bodies,
            "watertight": naked == 0 and duplicated == 0}


# ---------------------------------------------------------------- sbalzi
def overhang_angles(V, F):
    """Angolo di sbalzo per faccia, in gradi dalla verticale: 0 = parete verticale,
    90 = soffitto orizzontale. Vale 0 per le facce rivolte verso l'alto."""
    n, areas = face_normals_areas(V, F)
    ang = np.degrees(np.arcsin(np.clip(-n[:, 2], 0.0, 1.0)))
    z = V[:, 2][F]
    on_bed = (z.max(axis=1) <= BED_TOL) & (n[:, 2] < -0.99)
    return ang, areas, on_bed


def overhang(V, F, limit_deg=43.0):
    ang, areas, on_bed = overhang_angles(V, F)
    free = ~on_bed & (areas > 1e-9)
    over = free & (ang > limit_deg + ANGLE_TOL)
    return {"max_deg": float(ang[free].max()) if free.any() else 0.0,
            "area_over_mm2": float(areas[over].sum()),
            "faces_over": int(over.sum()),
            "bed_contact_mm2": float(areas[on_bed].sum()),
            "limit_deg": float(limit_deg)}


def local_minima(V, F):
    """Vertici piu bassi di tutti i vicini e non appoggiati al piano: da li la stampa
    partirebbe nel vuoto (isola), anche se le facce attorno sono ripide."""
    e = directed_edges(F)
    z = V[:, 2]
    nb_min = np.full(len(V), np.inf)
    np.minimum.at(nb_min, e[:, 0], z[e[:, 1]])
    np.minimum.at(nb_min, e[:, 1], z[e[:, 0]])
    idx = np.where((nb_min > z + 1e-6) & (z > BED_TOL) & np.isfinite(nb_min))[0]
    return idx


# ---------------------------------------------------------------- raggi
def raycast_numpy(V, F, chunk=8):
    """Lancio di raggi a forza bruta (Moller-Trumbore). Lento: solo ripiego e test."""
    a = V[F[:, 0]]
    e1 = V[F[:, 1]] - a
    e2 = V[F[:, 2]] - a

    def cast(origins, dirs):
        out = np.full(len(origins), np.inf)
        for s in range(0, len(origins), chunk):
            o = origins[s:s + chunk, None, :]
            d = dirs[s:s + chunk, None, :]
            p = np.cross(d, e2[None])
            det = np.einsum("rtk,tk->rt", p, e1)
            ok = np.abs(det) > 1e-12
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tv = o - a[None]
            u = np.einsum("rtk,rtk->rt", tv, p) * inv
            q = np.cross(tv, e1[None])
            v = np.einsum("rtk,rtk->rt", np.broadcast_to(d, q.shape), q) * inv
            t = np.einsum("rtk,tk->rt", q, e2) * inv
            hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-6)
            out[s:s + chunk] = np.where(hit, t, np.inf).min(axis=1)
        return out
    return cast


def raycast_trimesh(V, F):
    import trimesh
    mesh = trimesh.Trimesh(V, F, process=False)

    def cast(origins, dirs):
        out = np.full(len(origins), np.inf)
        loc, ray, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=True)
        if len(ray):
            dist = np.linalg.norm(loc - origins[ray], axis=1)
            good = dist > 1e-6
            np.minimum.at(out, ray[good], dist[good])
        return out
    return cast


def default_raycaster(V, F):
    try:
        return raycast_trimesh(V, F)
    except Exception:
        return raycast_numpy(V, F)


# ---------------------------------------------------------------- spessori
def thickness(V, F, raycast, samples=3000, seed=0):
    """Spessore locale: da punti sulla superficie, raggio verso l'interno del materiale
    fino alla parete opposta. Restituisce statistiche in mm."""
    n, areas = face_normals_areas(V, F)
    valid = np.where(areas > 1e-6)[0]
    rng = np.random.RandomState(seed)
    # campioni mirati: le facce piu piccole segnalano pieghe e autointersezioni
    tiny = valid[np.argsort(areas[valid])[:max(1, samples // 10)]]
    if len(valid) > samples:
        valid = rng.choice(valid, samples, replace=False)

    def measure(faces):
        cen = V[F[faces]].mean(axis=1)
        d = -n[faces]
        out = raycast(cen + d * 1e-4, d)
        return out[np.isfinite(out)]
    dist = measure(valid)
    extra = measure(tiny)
    if len(dist) == 0:
        return {"min": 0.0, "p1": 0.0, "p5": 0.0, "median": 0.0, "samples": 0}
    lowest = float(min(dist.min(), extra.min())) if len(extra) else float(dist.min())
    return {"min": lowest, "p1": float(np.percentile(dist, 1)),
            "p5": float(np.percentile(dist, 5)), "median": float(np.median(dist)),
            "samples": int(len(dist))}


# ---------------------------------------------------------------- stabilita
def convex_hull_2d(pts):
    pts = np.unique(np.round(pts, 6), axis=0)
    if len(pts) < 3:
        return pts
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def half(points):
        h = []
        for p in points:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h
    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def stability(V, F):
    """Baricentro del volume rispetto all'impronta di appoggio. margin_mm > 0 = stabile."""
    _, cen = volume_centroid(V, F)
    base = V[V[:, 2] <= BED_TOL][:, :2]
    hull = convex_hull_2d(base) if len(base) else base
    if len(hull) < 3:
        return {"margin_mm": -1.0, "centroid": [float(x) for x in cen], "footprint_points": int(len(base))}
    a, b = hull, np.roll(hull, -1, axis=0)
    edge = b - a
    ln = np.maximum(np.linalg.norm(edge, axis=1), 1e-12)
    # distanza con segno dal lato (positivo = dentro, poligono antiorario)
    sd = (edge[:, 0] * (cen[1] - a[:, 1]) - edge[:, 1] * (cen[0] - a[:, 0])) / ln
    return {"margin_mm": float(sd.min()), "centroid": [float(x) for x in cen],
            "footprint_points": int(len(base))}


# ---------------------------------------------------------------- lampade
def free_radius(raycast, axis_xy, z, directions=36):
    """Raggio libero attorno all'asse alla quota z: minimo su `directions` raggi radiali."""
    th = np.linspace(0, 2 * math.pi, directions, endpoint=False)
    d = np.column_stack([np.cos(th), np.sin(th), np.zeros(directions)])
    o = np.tile([axis_xy[0], axis_xy[1], z], (directions, 1))
    dist = raycast(o, d)
    return float(dist.min())


def fixture(V, F, raycast, cfg, floor_thickness):
    """Sede del portalampada e spazio per la lampadina, misurati con raggi dall'asse."""
    axis = (0.0, 0.0)
    ztop = float(V[:, 2].max())
    hole = 2 * free_radius(raycast, axis, floor_thickness / 2.0)
    z0 = float(cfg.get("socket_height", 60.0))
    z1 = min(z0 + float(cfg.get("bulb_length", 110.0)), ztop - 1.0)
    rb = float(cfg.get("bulb_diameter", 60.0)) / 2.0
    zs = np.linspace(min(z0, z1), z1, 12)
    clear = min(free_radius(raycast, axis, float(z)) for z in zs) - rb
    top = 2 * free_radius(raycast, axis, ztop - 0.5)
    return {"hole_diameter": hole if math.isfinite(hole) else 0.0,
            "bulb_clearance": clear if math.isfinite(clear) else 1e9,
            "top_opening": top if math.isfinite(top) else 1e9,
            "bulb_top_z": z0 + float(cfg.get("bulb_length", 110.0)), "height": ztop}


# ---------------------------------------------------------------- materiale e tempo
def material(V, F, pr):
    vol, _ = volume_centroid(V, F)
    _, areas = face_normals_areas(V, F)
    area = float(areas.sum())
    skin_vol = min(vol, area * float(pr["line_width"]) * int(pr["walls"]))
    est_vol = skin_vol + float(pr["infill"]) * max(0.0, vol - skin_vol)
    rho = float(pr["density_g_cm3"]) / 1000.0
    fil_area = math.pi * (float(pr["filament_diameter"]) / 2.0) ** 2
    return {"volume_cm3": vol / 1000.0, "surface_cm2": area / 100.0,
            "mass_solid_g": vol * rho, "mass_est_g": est_vol * rho,
            "filament_m": est_vol / fil_area / 1000.0,
            "time_h_rough": est_vol / float(pr["flow_mm3_s"]) / 3600.0}


# ---------------------------------------------------------------- vasi
def line_section(width, height):
    """Area della sezione di una linea estrusa (mm2): rettangolo con i due lati a semicerchio."""
    return (float(width) - float(height) * (1.0 - math.pi / 4.0)) * float(height)


def spiral_material(V, F, pr):
    """Materiale in modalita spirale: una linea sul contorno esterno di ogni strato
    piu gli strati pieni del fondo."""
    from .slicer import slice_segments
    vol_solid, _ = volume_centroid(V, F)
    h, lw = float(pr["layer"]), float(pr["line_width"])
    nb = int(pr.get("bottom_layers", 4))
    zs = np.arange(h / 2.0, float(V[:, 2].max()), h)
    sampled = zs[::5]                                   # un contorno ogni 1 mm, poi si scala
    length = 0.0
    for z in sampled:
        seg = slice_segments(V, F, z)
        if len(seg):
            length += float(np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum())
    length *= len(zs) / float(max(1, len(sampled)))
    _, areas, on_bed = overhang_angles(V, F)
    base_area = float(areas[on_bed].sum())              # area d'appoggio = fondo pieno
    # sezione della linea come la calcolano gli slicer (PrusaSlicer, Bambu): rettangolo con i lati
    # arrotondati, (w - h(1 - pi/4)) * h; con w * h il peso esce piu alto di circa il 6% a 0,7 x 0,2 mm
    vol = length * line_section(lw, h) + base_area * h * nb
    rho = float(pr["density_g_cm3"]) / 1000.0
    fil_area = math.pi * (float(pr["filament_diameter"]) / 2.0) ** 2
    return {"volume_cm3": vol_solid / 1000.0, "surface_cm2": 0.0, "mass_solid_g": vol_solid * rho,
            "mass_est_g": vol * rho, "filament_m": vol / fil_area / 1000.0,
            "time_h_rough": vol / float(pr["flow_mm3_s"]) / 3600.0, "spiral_path_m": length / 1000.0}


def vase_checks(V, F, params, result, checks, skin=None):
    """Controlli propri del vaso: capienza, fondo a tenuta, contorno unico in spirale."""
    from . import generators, slicer
    pr, cons = params["print"], params.get("constraints", {})
    spiral = pr.get("mode") == "spiral"
    if skin is not None:
        wall = float(pr["line_width"]) if spiral else float(params["wall"]["thickness"])
        floor = (float(pr["layer"]) * int(pr.get("bottom_layers", 4)) if spiral
                 else float(params["floor"]["thickness"]))
        cap = generators.capacity_ml(skin, wall, floor)
        result["capacity_ml"] = cap
        if "min_capacity_ml" in cons:
            checks.append(_check("capacity", "Capienza", round(cap, 0), "ml",
                                 ">= %g" % cons["min_capacity_ml"], cap >= cons["min_capacity_ml"]))
    if spiral:
        prof = slicer.loops_profile(V, F, float(pr["layer"]), 1.0)
        bad = [z for z, n in prof if n != 1]
        result["spiral_loops"] = {"sampled": len(prof), "bad": len(bad)}
        checks.append(_check("spiral", "Un solo contorno per strato (spirale)", len(bad),
                             "quote fuori regola", "0", len(bad) == 0,
                             "%d quote controllate, una ogni mm" % len(prof)))
        need = bool(cons.get("watertight", False))
        checks.append(_check("watertight", "Tenuta all'acqua" + ("" if need else " (solo informativo)"),
                             "non garantita", "", "richiesta" if need else "non richiesta", not need,
                             "parete singola: per l'acqua serve un rivestimento interno o un inserto"))
    else:
        fl = float(params["floor"]["thickness"])
        checks.append(_check("floor", "Fondo a tenuta", round(fl, 2), "mm", ">= 1.2",
                             fl >= 1.2 - 1e-9, "%d strati pieni" % int(round(fl / float(pr["layer"])))))


# ---------------------------------------------------------------- rapporto
def _check(cid, label, value, unit, limit, ok, note=""):
    return {"id": cid, "label": label, "value": value, "unit": unit, "limit": limit,
            "pass": bool(ok), "note": note}


def run_checks(V, F, params, raycast=None, samples=3000, skin=None):
    """Esegue tutte le verifiche. `params` e il dizionario gia risolto (vedi generators.resolve)."""
    V = np.asarray(V, dtype=float)
    F = np.asarray(F, dtype=np.int64)
    pr = params["print"]
    cons = params.get("constraints", {})
    checks = []

    topo = topology(V, F)
    checks.append(_check("closed", "Mesh chiusa", topo["naked_edges"] + topo["duplicated_edges"],
                         "spigoli aperti", "0", topo["watertight"] and topo["bodies"] == 1,
                         "%d corpo/i, %d facce degeneri" % (topo["bodies"], topo["degenerate_faces"])))

    lim = float(pr["overhang_limit_deg"])
    ov = overhang(V, F, lim)
    checks.append(_check("overhang", "Sbalzo massimo", round(ov["max_deg"], 1), "gradi",
                         "<= %g" % lim, ov["area_over_mm2"] <= 1.0,
                         "%.0f mm2 oltre soglia (%d facce)" % (ov["area_over_mm2"], ov["faces_over"])))

    mins = local_minima(V, F)
    checks.append(_check("islands", "Isole (partenze nel vuoto)", int(len(mins)), "punti", "0",
                         len(mins) == 0))

    bb_min, bb_max = V.min(axis=0), V.max(axis=0)
    size = bb_max - bb_min
    bed = [float(x) for x in pr["bed"]]
    checks.append(_check("bed", "Ingombro", [round(float(s), 1) for s in size], "mm",
                         "<= %g x %g x %g" % tuple(bed),
                         all(size[k] <= bed[k] for k in range(3)) and abs(bb_min[2]) <= BED_TOL))

    checks.append(_check("adhesion", "Appoggio sul piano", round(ov["bed_contact_mm2"], 0), "mm2",
                         ">= 100", ov["bed_contact_mm2"] >= 100.0))

    st = stability(V, F)
    checks.append(_check("stability", "Stabilita (baricentro dentro l'appoggio)",
                         round(st["margin_mm"], 1), "mm", "> 0", st["margin_mm"] > 0))

    result = {"topology": topo, "overhang": ov, "islands": int(len(mins)),
              "bbox": {"min": [float(x) for x in bb_min], "max": [float(x) for x in bb_max],
                       "size": [float(x) for x in size]},
              "stability": st}

    if topo["watertight"]:
        mat = material(V, F, pr)
        result["material"] = mat
        if pr.get("mode") == "spiral":
            mat = spiral_material(V, F, pr)
            result["material"] = mat
        if "max_mass_g" in cons:
            checks.append(_check("mass", "Peso stimato", round(mat["mass_est_g"], 1), "g",
                                 "<= %g" % cons["max_mass_g"], mat["mass_est_g"] <= cons["max_mass_g"],
                                 "pieno al 100%%: %.1f g" % mat["mass_solid_g"]))
        rc = raycast or default_raycaster(V, F)
        if pr.get("mode") == "spiral":       # solido pieno per scelta: la parete e una linea
            lw = float(pr["line_width"])
            th = {"min": lw, "p1": lw, "p5": lw, "median": lw, "samples": 0}
        else:
            th = thickness(V, F, rc, samples=samples)
        min_wall = float(cons.get("min_wall", 2 * float(pr["nozzle"])))
        result["thickness"] = th
        if pr.get("mode") == "spiral":
            lw = float(pr["line_width"])
            checks.append(_check("wall", "Parete in spirale (una linea)", round(lw, 2), "mm",
                                 ">= ugello %g" % float(pr["nozzle"]), lw >= float(pr["nozzle"]),
                                 "lo spessore e la larghezza di linea impostata nello slicer"))
        else:
            checks.append(_check("wall", "Spessore minimo (1 percentile)", round(th["p1"], 2), "mm",
                                 ">= %g" % min_wall,
                                 th["p1"] >= min_wall - 0.02 and th["min"] >= 0.5 * min_wall,
                                 "minimo assoluto %.2f mm (deve essere >= %.2f), %d campioni"
                                 % (th["min"], 0.5 * min_wall, th["samples"])))
        fx_cfg = cons.get("fixture")
        if fx_cfg:
            fx = fixture(V, F, rc, fx_cfg, float(params["floor"]["thickness"]))
            result["fixture"] = fx
            hd, tol = float(fx_cfg.get("hole_diameter", 40.0)), float(fx_cfg.get("hole_tolerance", 1.0))
            checks.append(_check("socket", "Foro portalampada %s" % fx_cfg.get("socket", ""),
                                 round(fx["hole_diameter"], 2), "mm", "%g - %g" % (hd, hd + tol),
                                 hd - 0.05 <= fx["hole_diameter"] <= hd + tol))
            mc = float(fx_cfg.get("min_clearance", 20.0))
            checks.append(_check("clearance", "Distanza lampadina-parete", round(fx["bulb_clearance"], 1),
                                 "mm", ">= %g" % mc, fx["bulb_clearance"] >= mc))
            mo = float(fx_cfg.get("min_top_opening", 50.0))
            checks.append(_check("vent", "Apertura superiore", round(fx["top_opening"], 1), "mm",
                                 ">= %g" % mo, fx["top_opening"] >= mo))
            checks.append(_check("bulb_fit", "Lampadina dentro il diffusore",
                                 round(fx["height"] - fx["bulb_top_z"], 1), "mm", ">= 0",
                                 fx["height"] >= fx["bulb_top_z"]))
        if "target_diameter" in cons:
            # diametro del cerchio circoscritto attorno all'asse: vale anche per forme non simmetriche
            cx, cy = (bb_min[0] + bb_max[0]) / 2.0, (bb_min[1] + bb_max[1]) / 2.0
            dia = float(2.0 * np.hypot(V[:, 0] - cx, V[:, 1] - cy).max())
            result["diameter"] = dia
            tol = float(cons.get("diameter_tolerance", 3.0))
            checks.append(_check("diameter", "Diametro massimo", round(dia, 1), "mm",
                                 "%g +/- %g" % (cons["target_diameter"], tol),
                                 abs(dia - cons["target_diameter"]) <= tol))

    if params.get("object") == "vase":
        vase_checks(V, F, params, result, checks, skin)

    result["checks"] = checks
    result["pass"] = all(c["pass"] for c in checks)
    result["layers"] = int(math.ceil(size[2] / float(pr["layer"])))
    return result


def format_report(result):
    lines = []
    for c in result["checks"]:
        mark = "OK " if c["pass"] else "NO "
        lines.append("%s %-40s %s %s  (limite %s) %s" % (mark, c["label"], c["value"], c["unit"],
                                                        c["limit"], c["note"]))
    lines.append("ESITO: %s  |  %d strati" % ("STAMPABILE" if result["pass"] else "DA CORREGGERE",
                                             result["layers"]))
    if "material" in result:
        m = result["material"]
        lines.append("Materiale: %.1f cm3, %.1f g, %.1f m di filo, circa %.1f h (stima grossolana)"
                     % (m["volume_cm3"], m["mass_est_g"], m["filament_m"], m["time_h_rough"]))
    return "\n".join(lines)
