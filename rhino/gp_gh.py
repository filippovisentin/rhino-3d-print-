# r: numpy
"""Calcolo del componente Grasshopper di Generative Print.

Il componente Python 3 della definizione `grasshopper/generative_print.gh` contiene solo
poche righe: trova la cartella del progetto e chiama `solve(ingressi)`, che restituisce
un dizionario con le uscite. Tutta la logica sta qui e nel nucleo `generative_print`.
"""
import json
import math
import os
import time

import numpy as np
import Rhino
import System
import Rhino.Geometry as rg

import gp_rhino
from generative_print import checks, generators, mesh_io, slicer

SHAPES = ("sine", "sharp", "scallop", "flute")
SHAPE_LABELS = ("sinusoide", "cresta viva", "lobi", "scanalature")

# vincoli del brief della demo (lampada E27, Ø 180 mm, max 250 g)
DEFAULT_CONSTRAINTS = {
    "min_wall": 1.2,
    "fixture": {"socket": "E27", "hole_diameter": 40.0, "hole_tolerance": 1.0, "socket_height": 60.0,
                "bulb_diameter": 60.0, "bulb_length": 110.0, "min_clearance": 20.0, "min_top_opening": 50.0},
}

_cache = {"key": None}


def _num(x, default):
    try:
        return default if x is None else float(x)
    except (TypeError, ValueError):
        return default


def params_from_inputs(i):
    """Dizionario dei parametri (stesso formato di runs/*/params.json) dagli ingressi."""
    shape_i = int(_num(i.get("forma"), 0)) % len(SHAPES)
    target = _num(i.get("diametro"), 0.0)
    hole = _num(i.get("foro"), 40.5)
    cons = json.loads(json.dumps(DEFAULT_CONSTRAINTS))
    cons["max_mass_g"] = _num(i.get("peso_max"), 250.0)
    if target > 0:
        cons["target_diameter"] = target
        cons["diameter_tolerance"] = 3.0
    if hole <= 0:
        cons.pop("fixture")                      # vaso: niente portalampada
    else:
        cons["fixture"]["hole_diameter"] = round(hole - 0.5, 2)
    return {
        "object": "lamp_shade" if hole > 0 else "vase",
        "envelope": {"height": _num(i.get("altezza"), 220.0)},
        "profile": {"radii": [_num(i.get("r%d" % k), d) for k, d in enumerate((54.0, 84.5, 81.5, 64.0, 44.0))]},
        "ribs": {"count": int(_num(i.get("nervature"), 16)), "amplitude": _num(i.get("ampiezza"), 0.05),
                 "twist_deg": _num(i.get("torsione"), 110.0), "shape": SHAPES[shape_i],
                 "fade_bottom": _num(i.get("sfuma_basso"), 0.22), "fade_top": _num(i.get("sfuma_alto"), 0.1)},
        "wall": {"thickness": _num(i.get("parete"), 1.35)},
        "floor": {"thickness": _num(i.get("fondo"), 3.0)},
        "fixture": {"hole_diameter": max(0.0, hole)},
        "mesh": {"edge": max(0.8, _num(i.get("risoluzione"), 2.5))},
        "print": {"overhang_limit_deg": _num(i.get("limite"), 45.0)},
        "constraints": cons,
    }


def sub_mesh(V, F, mask):
    if not mask.any():
        return None
    faces = F[mask]
    used = np.unique(faces)
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return gp_rhino.to_rhino_mesh(V[used], remap[faces])


def quick_checks(V, F, p):
    """Controlli veloci, rifatti a ogni movimento di slider: chiusura, sbalzi, isole,
    ingombro, stabilita, peso e diametro. Spessori e portalampada solo con `verifica`."""
    pr = p["print"]
    cons = p["constraints"]
    lim = float(pr["overhang_limit_deg"])
    topo = checks.topology(V, F)
    ov = checks.overhang(V, F, lim)
    mat = checks.material(V, F, pr) if topo["watertight"] else None
    size = V.max(axis=0) - V.min(axis=0)
    dia = float(2 * np.hypot(V[:, 0], V[:, 1]).max())
    st = checks.stability(V, F)
    rows = [("Mesh chiusa", topo["watertight"] and topo["bodies"] == 1, "%d spigoli aperti" % topo["naked_edges"]),
            ("Sbalzo", ov["area_over_mm2"] <= 1.0, "%.1f gradi (limite %g), %.0f mm2 oltre"
             % (ov["max_deg"], lim, ov["area_over_mm2"])),
            ("Isole", len(checks.local_minima(V, F)) == 0, "%d" % len(checks.local_minima(V, F))),
            ("Ingombro", all(size[k] <= pr["bed"][k] for k in range(3)),
             "%.0f x %.0f x %.0f mm" % tuple(size)),
            ("Stabilita", st["margin_mm"] > 0, "margine %.1f mm" % st["margin_mm"])]
    if mat:
        rows.append(("Peso", mat["mass_est_g"] <= cons["max_mass_g"],
                     "%.1f g (max %g)" % (mat["mass_est_g"], cons["max_mass_g"])))
    if "target_diameter" in cons:
        rows.append(("Diametro", abs(dia - cons["target_diameter"]) <= cons["diameter_tolerance"],
                     "%.1f mm (%g +/- %g)" % (dia, cons["target_diameter"], cons["diameter_tolerance"])))
    return rows, ov, mat, dia


def solve(i):
    t0 = time.time()
    out = {}
    p = generators.resolve(params_from_inputs(i))
    key = json.dumps(p, sort_keys=True)
    if _cache["key"] != key:
        skin = generators.vessel_skin(p)
        V, F = generators.shell_from_params(skin, p)
        _cache.update(key=key, skin=skin, V=V, F=F, full=None,
                      solid=gp_rhino.to_rhino_mesh(V, F),
                      pelle=gp_rhino.to_rhino_mesh(skin.reshape(-1, 3), generators.skin_faces(*skin.shape[:2])))
        ang, areas, bed = checks.overhang_angles(V, F)
        lim = float(p["print"]["overhang_limit_deg"])
        free = ~bed & (areas > 1e-9)
        over = free & (ang > lim + checks.ANGLE_TOL)
        warn = free & (ang > lim - 10.0) & ~over
        _cache["oltre"] = sub_mesh(V, F, over)
        _cache["vicino"] = sub_mesh(V, F, warn)
        _cache["quick"] = quick_checks(V, F, p)
    skin, V, F = _cache["skin"], _cache["V"], _cache["F"]
    rows, ov, mat, dia = _cache["quick"]

    lines = ["%s %-11s %s" % ("OK" if ok else "NO", lab, txt) for lab, ok, txt in rows]
    passed = all(ok for _, ok, _ in rows)
    if i.get("verifica"):
        if _cache["full"] is None:
            rc = gp_rhino.raycast_rhino(_cache["solid"])
            _cache["full"] = checks.run_checks(V, F, p, raycast=rc, samples=1200)
        full = _cache["full"]
        lines = checks.format_report(full).splitlines()
        passed = full["pass"]
    else:
        lines.append("(spessori e portalampada: attiva 'verifica')")

    out["pelle"] = _cache["pelle"]
    out["solido"] = _cache["solid"]
    out["oltre"] = _cache["oltre"]
    out["vicino"] = _cache["vicino"]
    out["ok"] = bool(passed)
    out["peso"] = round(mat["mass_est_g"], 1) if mat else None
    out["sbalzo"] = round(ov["max_deg"], 1)
    out["diametro_mis"] = round(dia, 1)

    layer = int(_num(i.get("strato"), 0))
    out["strato"] = None
    if layer > 0:
        h = float(p["print"]["layer"])
        z = min((layer - 0.5) * h, float(V[:, 2].max()) - 1e-3)
        seg = slicer.slice_segments(V, F, z)
        out["strato"] = [rg.Line(rg.Point3d(*a), rg.Point3d(*b)) for a, b in seg.tolist()]
        lines.append("Strato %d / %d  z = %.2f mm" % (layer, int(math.ceil(V[:, 2].max() / h)), z))

    params_txt = json.dumps({k: v for k, v in p.items() if k != "mesh"}, indent=2)
    out["params"] = params_txt

    path = str(i.get("file") or "").strip().strip('"')
    if i.get("esporta"):
        if not path:
            lines.append("ESPORTA: collega un percorso al parametro 'file'")
        else:
            base = os.path.splitext(path)[0]
            mesh_io.write_stl(base + ".stl", V, F)
            with open(base + ".params.json", "w", encoding="utf-8") as fh:
                fh.write(params_txt)
            lines.append("Esportato %s.stl e %s.params.json" % (base, base))
    if i.get("editor"):
        _send_to_editor(p)
        lines.append("Pelle inviata all'editor plastico")

    lines.append("Calcolo %.2f s, %d triangoli, forma %s" % (time.time() - t0, len(F),
                                                             SHAPE_LABELS[SHAPES.index(p["ribs"]["shape"])]))
    out["report"] = "\n".join(lines)
    return out


def _send_to_editor(params):
    """Mette la pelle nel documento di Rhino e apre l'editor al primo momento libero."""
    import gp_editor
    p = json.loads(json.dumps(params))
    p["mesh"] = {"edge": 1.6, "segments": 0, "rings": 0}   # risoluzione piena per scolpire

    import scriptcontext as sc
    state = {"done": False}

    def once(sender, e):
        if state["done"]:
            return
        state["done"] = True
        Rhino.RhinoApp.Idle -= handler
        try:
            doc = Rhino.RhinoDoc.ActiveDoc
            sid = gp_editor.new_vessel(doc, p)
            ed = gp_editor.get_editor()
            ed.open(doc, doc.Objects.FindId(sid))
            ed.show_panel()
            sc.sticky["gp_gh_editor_status"] = "aperto"
        except Exception:
            import traceback
            sc.sticky["gp_gh_editor_status"] = traceback.format_exc()
            Rhino.RhinoApp.WriteLine("Generative Print: " + sc.sticky["gp_gh_editor_status"])
    handler = System.EventHandler(once)
    Rhino.RhinoApp.Idle += handler
