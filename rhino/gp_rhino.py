# r: numpy
"""Ponte tra il nucleo numpy e Rhino 8 (Python 3 dello ScriptEditor).

Rhino qui fa tre cose: mostra la geometria, cattura le viste fisse e ospita l'editor.
Le misure restano nel nucleo `generative_print`.
"""
import json
import os
import sys

import numpy as np
import Rhino
import Rhino.Geometry as rg
import System
import System.Drawing as sd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from generative_print import checks, generators, mesh_io  # noqa: E402

USER_KEY = "generative_print"
REF_BOX = ((-135.0, -135.0, 0.0), (135.0, 135.0, 260.0))   # inquadratura fissa per tutte le viste


# ------------------------------------------------------------------ conversioni
def to_rhino_mesh(V, F):
    m = rg.Mesh()
    m.Vertices.UseDoublePrecisionVertices = True
    add_v = m.Vertices.Add
    for x, y, z in np.asarray(V, dtype=float).tolist():
        add_v(x, y, z)
    add_f = m.Faces.AddFace
    for a, b, c in np.asarray(F).tolist():
        add_f(a, b, c)
    m.Normals.ComputeNormals()
    return m


def from_rhino_mesh(mesh):
    """Mesh di Rhino -> (V, F) triangolata."""
    m = mesh
    if m.Faces.QuadCount > 0:
        m = mesh.DuplicateMesh()
        m.Faces.ConvertQuadsToTriangles()
    V = np.array([[p.X, p.Y, p.Z] for p in m.Vertices.ToPoint3dArray()], dtype=float)
    F = np.array([[f.A, f.B, f.C] for f in m.Faces], dtype=np.int64)
    return V, F


def raycast_rhino(mesh):
    """Lancio di raggi con il motore di Rhino (veloce): stessa firma del nucleo."""
    ray_hit = rg.Intersect.Intersection.MeshRay

    def cast(origins, dirs):
        out = np.full(len(origins), np.inf)
        for k, (o, d) in enumerate(zip(origins.tolist(), dirs.tolist())):
            t = ray_hit(mesh, rg.Ray3d(rg.Point3d(o[0], o[1], o[2]), rg.Vector3d(d[0], d[1], d[2])))
            if t > 1e-6:
                out[k] = t
        return out
    return cast


# ------------------------------------------------------------------ documento
def setup_doc(doc):
    if doc.ModelUnitSystem != Rhino.UnitSystem.Millimeters:
        doc.AdjustModelUnitSystem(Rhino.UnitSystem.Millimeters, False)


def ensure_layer(doc, name, color):
    idx = doc.Layers.FindByFullPath(name, -1)
    if idx < 0:
        layer = Rhino.DocObjects.Layer()
        layer.Name = name
        layer.Color = sd.Color.FromArgb(*color)
        idx = doc.Layers.Add(layer)
    return idx


def remove_named(doc, prefix):
    """Elimina gli oggetti il cui nome inizia con `prefix`, compresi quelli nascosti."""
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.HiddenObjects = True
    settings.LockedObjects = True
    for obj in list(doc.Objects.GetObjectList(settings)):
        if (obj.Attributes.Name or "").startswith(prefix):
            doc.Objects.Delete(obj, True, True)


def add_mesh(doc, mesh, name, layer="GP", color=(225, 222, 214), meta=None):
    attr = Rhino.DocObjects.ObjectAttributes()
    attr.Name = name
    attr.LayerIndex = ensure_layer(doc, layer, color)
    if meta is not None:
        attr.SetUserString(USER_KEY, json.dumps(meta))
    return doc.Objects.AddMesh(mesh, attr)


def read_meta(obj):
    raw = obj.Attributes.GetUserString(USER_KEY)
    return json.loads(raw) if raw else None


# ------------------------------------------------------------------ viste fisse
def _settle(view):
    """Attende che la vista abbia finito di ridisegnare (altrimenti la cattura puo uscire
    a meta, con i fili della maglia al posto della superficie)."""
    for _ in range(3):
        view.Redraw()
        Rhino.RhinoApp.Wait()


def _capture(view, path, size=(900, 900)):
    _settle(view)
    vc = Rhino.Display.ViewCapture()
    vc.Width, vc.Height = size
    vc.ScaleScreenItems = False
    vc.DrawAxes = False
    vc.DrawGrid = False
    vc.DrawGridAxes = False
    vc.TransparentBackground = False
    bmp = vc.CaptureToBitmap(view)
    bmp.Save(path, sd.Imaging.ImageFormat.Png)
    bmp.Dispose()


def _set_camera(vp, direction, parallel, magnify=1.0):
    """Camera che guarda il centro dell'inquadratura fissa lungo `direction`."""
    box = rg.BoundingBox(rg.Point3d(*REF_BOX[0]), rg.Point3d(*REF_BOX[1]))
    if parallel:
        vp.ChangeToParallelProjection(True)
    else:
        vp.ChangeToPerspectiveProjection(True, 50.0)
    target = box.Center
    d = rg.Vector3d(*direction)
    d.Unitize()
    vp.SetCameraLocations(target, target - d * 800.0)
    vp.CameraUp = rg.Vector3d.ZAxis if abs(d.Z) < 0.99 else rg.Vector3d.YAxis
    vp.ZoomBoundingBox(box)
    if magnify != 1.0:
        vp.Magnify(magnify, False)


def capture_views(doc, out_dir, solid_id, V, F, mode="Rendered", size=(900, 900)):
    """Quattro viste a inquadratura costante: fronte, tre quarti, alto, sezione."""
    os.makedirs(out_dir, exist_ok=True)
    view = doc.Views.ActiveView
    vp = view.ActiveViewport
    dm = Rhino.Display.DisplayModeDescription.FindByName(mode)
    if dm is not None:
        vp.DisplayMode = dm
    shots = [("front", (0, 1, 0), True), ("persp", (-0.55, 0.75, -0.42), False), ("top", (0, 0, -1), True)]
    paths = {}
    for name, direction, parallel in shots:
        _set_camera(vp, direction, parallel, 1.0 if parallel else 1.45)
        view.Redraw()
        paths[name] = os.path.join(out_dir, name + ".png")
        _capture(view, paths[name], size)
    # sezione: meta posteriore del solido, vista di fronte
    half = F[(V[:, 1][F].min(axis=1) >= 0.0)]
    sec_id = add_mesh(doc, to_rhino_mesh(V, half), "gp_tmp_section", color=(205, 120, 70))
    doc.Objects.Hide(solid_id, True)
    _set_camera(vp, (0, 1, 0), True)
    view.Redraw()
    paths["section"] = os.path.join(out_dir, "section.png")
    _capture(view, paths["section"], size)
    doc.Objects.Delete(sec_id, True)
    doc.Objects.Show(solid_id, True)
    _set_camera(vp, (-0.55, 0.75, -0.42), False, 1.45)
    view.Redraw()
    return paths


# ------------------------------------------------------------------ iterazione del ciclo
def run_iteration(doc, iter_dir, capture=True):
    """Legge params.json, genera pelle e solido, li mette nel documento, esporta
    model.stl e cattura le viste. Le metriche si calcolano poi fuori da Rhino."""
    setup_doc(doc)
    with open(os.path.join(iter_dir, "params.json"), encoding="utf-8") as fh:
        params = json.load(fh)
    skin, V, F = generators.build(params)
    return show_solid(doc, iter_dir, params, skin, V, F, capture)


def show_solid(doc, iter_dir, params, skin, V, F, capture=True):
    remove_named(doc, "gp_")
    nz, nt = skin.shape[:2]
    meta = {"kind": "skin", "rings": int(nz), "segments": int(nt), "params": params}
    skin_mesh = to_rhino_mesh(skin.reshape(-1, 3), generators.skin_faces(nz, nt))
    skin_id = add_mesh(doc, skin_mesh, "gp_pelle", layer="GP::pelle", color=(120, 160, 210), meta=meta)
    doc.Objects.Hide(skin_id, True)
    Vv, Fv = display_solid(skin, params, V, F)
    solid_id = add_mesh(doc, to_rhino_mesh(Vv, Fv), "gp_solido", layer="GP::solido")
    mesh_io.write_stl(os.path.join(iter_dir, "model.stl"), V, F)
    np.save(os.path.join(iter_dir, "skin.npy"), skin)
    out = {"triangles": int(len(F)), "vertices": int(len(V)), "stl": os.path.join(iter_dir, "model.stl")}
    if capture:
        out["views"] = capture_views(doc, os.path.join(iter_dir, "views"), solid_id, Vv, Fv)
    doc.Views.Redraw()
    return out


def display_solid(skin, params, V, F):
    """Geometria da mostrare: per il vaso in spirale il pezzo come uscira dalla stampante
    (parete di una linea, aperto in alto), non il solido pieno che si esporta per lo slicer."""
    p = generators.resolve(params)
    if p.get("object") == "vase" and p["print"].get("mode") == "spiral":
        pr = p["print"]
        return generators.build_shell(skin, wall=float(pr["line_width"]),
                                      floor=float(pr["layer"]) * int(pr.get("bottom_layers", 4)))
    return V, F


def skin_from_doc(doc):
    """Recupera la pelle modificabile dal documento: (oggetto, skin, params)."""
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.HiddenObjects = True
    for obj in doc.Objects.GetObjectList(settings):
        if obj.Attributes.Name == "gp_pelle":
            meta = read_meta(obj)
            V, _ = from_rhino_mesh(obj.Geometry)
            return obj, V.reshape(meta["rings"], meta["segments"], 3), meta["params"]
    return None, None, None


# ------------------------------------------------------------------ passo del ciclo guidato dall'agente
def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def external_verify(iter_dir):
    """Lancia la verifica indipendente (Python esterno a Rhino) sull'STL esportato."""
    import subprocess
    exe = os.path.join(REPO, ".venv", "Scripts", "python.exe")
    if not os.path.exists(exe):
        exe = os.path.join(REPO, ".venv", "bin", "python")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    proc = subprocess.run([exe, "-m", "generative_print.verify", iter_dir], cwd=REPO, env=env,
                          capture_output=True, text=True, creationflags=0x08000000 if os.name == "nt" else 0)
    return proc.stdout + proc.stderr


def step(doc, run_dir, n, overrides=None):
    """Iterazione n: parametri = iterazione precedente + modifiche; genera, cattura, verifica."""
    iter_dir = os.path.join(run_dir, "iter_%02d" % n)
    os.makedirs(iter_dir, exist_ok=True)
    ppath = os.path.join(iter_dir, "params.json")
    if overrides is not None or not os.path.exists(ppath):
        with open(os.path.join(run_dir, "iter_%02d" % (n - 1), "params.json"), encoding="utf-8") as fh:
            params = deep_merge(json.load(fh), overrides)
        with open(ppath, "w", encoding="utf-8") as fh:
            json.dump(params, fh, indent=2)
    run_iteration(doc, iter_dir)
    return external_verify(iter_dir)


def sculpt_step(doc, run_dir, n, session, note=""):
    """Registra come iterazione n la forma scolpita nell'editor (sessione su una pelle)."""
    iter_dir = os.path.join(run_dir, "iter_%02d" % n)
    os.makedirs(os.path.join(iter_dir, "views"), exist_ok=True)
    params = dict(session.params)
    params["sculpt"] = {"note": note, "strokes": session.log}
    with open(os.path.join(iter_dir, "params.json"), "w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2)
    skin = session.V.reshape(session.grid[0], session.grid[1], 3).copy()
    np.save(os.path.join(iter_dir, "skin.npy"), skin)
    V, F = generators.solid_from_skin(skin, params)
    _capture(doc.Views.ActiveView, os.path.join(iter_dir, "views", "editor.png"))   # mappa degli sbalzi
    return iter_dir, params, skin, V, F


def relax_step(doc, run_dir, n, limit_deg=None, note="", capture=True):
    """Iterazione n: pelle dell'iterazione n-1 corretta con sculpt.relax_overhang (lisciatura
    automatica solo dove il solido supera il limite di sbalzo), poi viste e verifica esterna."""
    from generative_print import sculpt
    prev = os.path.join(run_dir, "iter_%02d" % (n - 1))
    iter_dir = os.path.join(run_dir, "iter_%02d" % n)
    os.makedirs(os.path.join(iter_dir, "views"), exist_ok=True)
    with open(os.path.join(prev, "params.json"), encoding="utf-8") as fh:
        params = json.load(fh)
    if limit_deg is not None:
        params.setdefault("print", {})["overhang_limit_deg"] = float(limit_deg)
    p = generators.resolve(params)
    lim = float(p["print"]["overhang_limit_deg"])
    skin0 = np.load(os.path.join(prev, "skin.npy"))
    skin, log = sculpt.relax_overhang(skin0, lambda s: generators.solid_from_skin(s, p), lim)
    moved = np.linalg.norm(skin - skin0, axis=2)
    params["relax_overhang"] = {"note": note, "limit_deg": lim, "passes": log,
                                "points_moved": int((moved > 1e-9).sum()), "points_total": int(moved.size),
                                "max_move_mm": round(float(moved.max()), 3)}
    with open(os.path.join(iter_dir, "params.json"), "w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2, ensure_ascii=False)
    V, F = generators.solid_from_skin(skin, p)
    show_solid(doc, iter_dir, params, skin, V, F, capture=capture)
    return external_verify(iter_dir), params["relax_overhang"]
