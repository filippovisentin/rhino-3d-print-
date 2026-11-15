# r: numpy
"""Pezzi di prova per lo slicer: forme tecniche e oggetti veri, non cubi.

Ogni funzione restituisce la geometria di un pezzo (Brep di Rhino oppure mesh numpy) gia nella
posizione di stampa. `build(doc)` li mette tutti nel documento, in fila, con il nome `prova_<pezzo>`.

    staffa       squadretta a L con fori, asola e nervatura
    ingranaggio  ruota dentata a 18 denti con mozzo, foro e sede della linguetta
    scatola      contenitore per elettronica: pareti da 1,6 mm, colonnine, feritoie, finestra (ponte)
    gancio       gancio da muro in piedi: braccio curvo a sbalzo (vuole i supporti)
    vite         vite M16 a testa esagonale con filetto vero (passo 2 mm), testa in giu
    vaso         vaso esagonale ritorto con parete da 1,2 mm (forma organica, pareti inclinate)
"""
import math
import os
import sys

import numpy as np
import Rhino
import Rhino.Geometry as rg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (REPO, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from generative_print import generators  # noqa: E402
import gp_rhino  # noqa: E402

TOL = 0.01


def _box(x0, y0, z0, x1, y1, z1):
    return rg.Brep.CreateFromBox(rg.BoundingBox(rg.Point3d(x0, y0, z0), rg.Point3d(x1, y1, z1)))


def _cyl(x, y, z, r, h, axis=(0, 0, 1)):
    plane = rg.Plane(rg.Point3d(x, y, z), rg.Vector3d(*axis))
    return rg.Cylinder(rg.Circle(plane, r), h).ToBrep(True, True)


def _one(parts, what):
    if not parts or len(parts) != 1:
        raise ValueError("%s: l'operazione booleana ha dato %d pezzi" % (what, len(parts) if parts else 0))
    return parts[0]


def _union(breps, what="unione"):
    return _one(rg.Brep.CreateBooleanUnion(breps, TOL), what)


def _cut(body, tools, what="taglio"):
    return _one(rg.Brep.CreateBooleanDifference([body], tools, TOL), what)


def _prism(points, z0, h):
    """Prisma verticale da un contorno chiuso di punti (x, y)."""
    pts = [rg.Point3d(float(x), float(y), z0) for x, y in points]
    pts.append(pts[0])
    crv = rg.PolylineCurve(pts)
    ext = rg.Extrusion.Create(crv, h, True)
    if ext is None:                                   # verso del contorno: si riprova al contrario
        ext = rg.Extrusion.Create(crv, -h, True)
    b = ext.ToBrep()
    if b.GetBoundingBox(True).Min.Z < z0 - 1e-6:
        b.Translate(0, 0, h)
    return b


def staffa():
    """Squadretta 60 x 40 x 50, spessore 5: due fori nella base, asola nell'ala, nervatura a triangolo."""
    body = _union([_box(0, 0, 0, 60, 40, 5), _box(0, 0, 0, 5, 40, 50)], "staffa")
    pts = [rg.Point3d(5, 18, 5), rg.Point3d(40, 18, 5), rg.Point3d(5, 18, 40), rg.Point3d(5, 18, 5)]
    rib = rg.Extrusion.Create(rg.PolylineCurve(pts), 4.0, True).ToBrep()
    if rib.GetBoundingBox(True).Min.Y < 18 - 1e-6:
        rib.Translate(0, 4, 0)
    body = _union([body, rib], "nervatura")
    holes = [_cyl(45, 9, -1, 2.6, 7), _cyl(45, 31, -1, 2.6, 7),
             _cyl(45, 9, 3, 5.0, 3), _cyl(45, 31, 3, 5.0, 3)]                      # fori con sede della testa
    slot = _union([_cyl(-1, 9, 30, 2.6, 7, (1, 0, 0)), _cyl(-1, 9, 40, 2.6, 7, (1, 0, 0)),
                   _box(-1, 6.4, 30, 6, 11.6, 40)], "asola")
    hole2 = _cyl(-1, 31, 35, 4.0, 7, (1, 0, 0))
    return _cut(body, holes + [slot, hole2], "fori della staffa")


def gear_outline(teeth=18, module=2.0):
    """Contorno di una ruota dentata (denti a fianchi dritti raccordati alla base)."""
    rp, ra, rf = module * teeth / 2.0, module * (teeth + 2) / 2.0, module * (teeth - 2.5) / 2.0
    step = 2.0 * math.pi / teeth
    pts = []
    for i in range(teeth):
        a0 = i * step
        for da, r in ((-0.36, rf), (-0.27, rf + 0.25 * module), (-0.25, rp), (-0.11, ra), (0.11, ra),
                      (0.25, rp), (0.27, rf + 0.25 * module), (0.36, rf), (0.5, rf)):
            pts.append((r * math.cos(a0 + da * step), r * math.sin(a0 + da * step)))
    return pts


def ingranaggio():
    """Ruota a 18 denti, modulo 2 (diametro esterno 40), spessore 10; mozzo 18 x 18; foro 8 con linguetta."""
    body = _union([_prism(gear_outline(), 0, 10), _cyl(0, 0, 0, 9, 18)], "ingranaggio")
    bore = _union([_cyl(0, 0, -1, 4.0, 20), _box(-1.5, 0, -1, 1.5, 5.6, 19)], "foro con linguetta")
    light = [_cyl(11.6 * math.cos(a), 11.6 * math.sin(a), -1, 2.2, 12) for a in np.arange(6) * math.pi / 3]
    return _cut(body, [bore] + light, "fori dell'ingranaggio")


def scatola():
    """Contenitore 70 x 50 x 25, pareti e fondo da 1,6: quattro colonnine forate, feritoie, finestra."""
    w, body = 1.6, _box(0, 0, 0, 70, 50, 25)
    body = _cut(body, [_box(w, w, w, 70 - w, 50 - w, 26)], "vano della scatola")
    posts = [_cyl(x, y, w - 0.2, 3.0, 6.2) for x in (7, 63) for y in (7, 43)]
    body = _union([body] + posts, "colonnine")
    tools = [_cyl(x, y, w, 1.25, 7) for x in (7, 63) for y in (7, 43)]
    tools += [_box(20 + 6 * i, -1, 8, 22 + 6 * i, 3, 20) for i in range(6)]          # feritoie verticali
    tools.append(_box(68, 17, 6, 72, 33, 13))                                        # finestra: ponte da 16 mm
    tools.append(_cyl(35, 51, 14, 4.0, 4, (0, -1, 0)))                               # foro tondo per un cavo
    return _cut(body, tools, "aperture della scatola")


def gancio():
    """Gancio da muro in piedi: piastra 30 x 5 x 60 con due fori svasati e braccio curvo di diametro 9."""
    plate = _box(-15, 0, 0, 15, 5, 60)
    rail = rg.ArcCurve(rg.Arc(rg.Point3d(0, 4, 26), rg.Point3d(0, 22, 14), rg.Point3d(0, 34, 30)))
    arm = _one(rg.Brep.CreatePipe(rail, 4.5, False, rg.PipeCapMode.Round, True, TOL, 0.02), "braccio")
    body = _union([plate, arm], "gancio")
    holes = []
    for z in (10, 50):
        holes.append(_cyl(0, -1, z, 2.2, 7, (0, 1, 0)))
        cone = rg.Cone(rg.Plane(rg.Point3d(0, 2.4, z), rg.Vector3d(0, 1, 0)), 2.7, 4.6).ToBrep(True)
        holes.append(cone)
    return _cut(body, holes, "fori del gancio")


def vite(d=16.0, pitch=2.0, length=30.0, head=10.0, across=24.0, segments=120, dz=0.1):
    """Vite a testa esagonale, testa sul piano: mesh numpy (V, F) con il filetto vero a 60 gradi."""
    depth = 0.6134 * pitch
    th = np.arange(segments) * 2.0 * math.pi / segments
    hexr = (across / 2.0) / np.cos(((th + math.pi / 6.0) % (math.pi / 3.0)) - math.pi / 6.0)
    rings = [(0.0, hexr - 0.6), (0.6, hexr), (head, hexr)]                 # smusso sotto la testa
    zs = np.arange(0.0, length + 1e-9, dz)
    for z in zs:
        t = (z / pitch - th / (2.0 * math.pi)) % 1.0
        r = d / 2.0 - depth + depth * (1.0 - np.abs(2.0 * t - 1.0))
        fade = min(1.0, (length - z) / 1.5 + 0.35)                         # punta smussata
        rings.append((head + z, np.minimum(r, (d / 2.0) * fade)))
    V = [np.c_[r * np.cos(th), r * np.sin(th), np.full(segments, z)] for z, r in rings]
    n = len(rings)
    V = np.vstack(V + [[[0, 0, 0.0]], [[0, 0, head + length]]])
    i = np.arange(segments)
    j = (i + 1) % segments
    F = []
    for k in range(n - 1):
        a, b = k * segments, (k + 1) * segments
        F += [np.c_[a + i, a + j, b + j], np.c_[a + i, b + j, b + i]]
    c0, c1, top = n * segments, n * segments + 1, (n - 1) * segments
    F += [np.c_[np.full(segments, c0), j, i], np.c_[np.full(segments, c1), top + i, top + j]]
    return V, np.vstack(F).astype(np.int64)


def vaso():
    """Vaso esagonale ritorto alto 110 mm, parete vera da 1,2 mm (mesh numpy)."""
    params = {"object": "vase", "envelope": {"height": 110.0},
              "profile": {"radii": [26.0, 34.0, 38.0, 34.0, 29.0]},
              "ribs": {"count": 0, "amplitude": 0.0, "twist_deg": 0.0},
              "section": {"sides": 6, "depth": 0.18, "twist_deg": 120.0, "ease": True, "style": "polygon",
                          "corner": 0.12},
              "wall": {"thickness": 1.2}, "floor": {"thickness": 1.2}, "fixture": {"hole_diameter": 0.0},
              "mesh": {"segments": 180, "edge": 1.2}, "print": {"mode": "walls"}}
    _, V, F = generators.build_vase(params)
    return V, F


PIECES = (("staffa", staffa), ("ingranaggio", ingranaggio), ("scatola", scatola), ("gancio", gancio),
          ("vite", vite), ("vaso", vaso))


def build(doc, names=None, gap=20.0):
    """Mette i pezzi nel documento, in fila lungo x, con il nome `prova_<pezzo>`. Chi esiste gia viene
    sostituito. Restituisce {nome: id dell'oggetto}; i pezzi falliti stanno in ['errors']."""
    out, x, errors = {}, 0.0, {}
    for name, fn in PIECES:
        if names and name not in names:
            continue
        gp_rhino.remove_named(doc, "prova_" + name)
        try:
            geo = fn()
            if isinstance(geo, tuple):
                geo = gp_rhino.to_rhino_mesh(*geo)
        except Exception as exc:
            errors[name] = str(exc)
            continue
        bb = geo.GetBoundingBox(True)
        geo.Translate(x - bb.Min.X, -bb.Min.Y, -bb.Min.Z)
        x += bb.Max.X - bb.Min.X + gap
        at = Rhino.DocObjects.ObjectAttributes()
        at.Name = "prova_" + name
        out[name] = doc.Objects.Add(geo, at)
    doc.Views.Redraw()
    out["errors"] = errors
    return out


SUPPORTS = {"gancio": "tree"}            # pezzi che si provano con i supporti (e di che tipo)


def run(doc, out_dir, names=None, settings=None):
    """Scrive il nostro G-code di ogni pezzo di prova in out_dir/<pezzo>/nostro.gcode, insieme alla
    mesh affettata (pezzo.stl) e ai parametri (params.json) per rifare lo stesso lavoro con Bambu.
    Restituisce per ogni pezzo: peso, tempo, strati, esito del controllo e gli strati sospetti
    (vuoti, con contorni aperti, con parete esterna persa) piu i metri di parete persi o stretti."""
    import json
    import time
    import gp_slicer
    from generative_print import mesh_io
    sl = gp_slicer.get_slicer()
    st = Rhino.DocObjects.ObjectEnumeratorSettings()
    st.HiddenObjects = True
    objs = dict((o.Name, o) for o in doc.Objects.GetObjectList(st) if (o.Name or "").startswith("prova_"))
    out = {}
    for name, _ in PIECES:
        if (names and name not in names) or "prova_" + name not in objs:
            continue
        folder = os.path.join(out_dir, name)
        os.makedirs(folder, exist_ok=True)
        sl.open(doc, [objs["prova_" + name]])
        kind = SUPPORTS.get(name)
        over = dict(settings or {})
        over.update({"supports": bool(kind), "support_type": kind or "grid"})
        sl.set(**over)
        j, s, r = sl.job, sl.settings, sl.result
        mesh_io.write_stl(os.path.join(folder, "pezzo.stl"), j.Vw, j.F)
        pr = dict((k, s[k]) for k in ("layer", "line_width", "walls", "infill", "top_layers", "bottom_layers",
                                      "overhang_limit_deg"))
        pr["generic"] = True
        with open(os.path.join(folder, "params.json"), "w", encoding="utf-8") as fh:
            json.dump({"object": "solid", "print": pr, "support": kind}, fh)
        t0 = time.time()
        sl.gcode_result = None
        g = sl.write_gcode(os.path.join(folder, "nostro.gcode"))
        row = {"triangles": int(len(j.F)), "closed": bool(r["closed"]), "fits": bool(r["fits"]),
               "volume_cm3": round(float(r.get("volume_cm3", 0.0)), 2), "support": kind,
               "messages": [l for l in sl.extra if l.startswith("G-code")], "seconds": round(time.time() - t0, 1)}
        if g:
            stats = np.array(sl.layer_stats or np.zeros((0, 5)), dtype=float).reshape(-1, 5)
            row.update({"ok": bool(g["check"]["ok"]), "errors": g["check"]["errors"][:5],
                        "weight_g": round(g["weight_g"], 2), "print_min": round(g["time_s"] / 60.0, 1),
                        "layers": g["layers"], "slowed_layers": g.get("slowed_layers"),
                        "retractions": g["retractions"],
                        "by_type_m": dict((k, round(v / 1000.0, 2)) for k, v in g["by_type"].items()),
                        "contours": int(stats[:, 0].sum()),
                        "empty_layers": np.flatnonzero(stats[:, 0] == 0).tolist()[:30],
                        "open_loop_layers": np.flatnonzero(stats[:, 1] > 0).tolist()[:30],
                        "outer_lost_m": round(float(stats[:, 2].sum()) / 1000.0, 3),
                        "outer_lost_layers": np.flatnonzero(stats[:, 2] > 0.5).tolist()[:30],
                        "inner_lost_m": round(float(stats[:, 3].sum()) / 1000.0, 3),
                        "thin_wall_m": round(float(stats[:, 4].sum()) / 1000.0, 3)})
        out[name] = row
    sl.close(close_form=False)
    with open(os.path.join(out_dir, "nostro.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out
