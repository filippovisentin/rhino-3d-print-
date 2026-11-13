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
    for obj in list(doc.Objects):
        if (obj.Attributes.Name or "").startswith(prefix):
            doc.Objects.Delete(obj.Id, True)


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
def _capture(view, path, size=(900, 900)):
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
    skin, V, F = generators.build_vessel(params)
    return show_solid(doc, iter_dir, params, skin, V, F, capture)


def show_solid(doc, iter_dir, params, skin, V, F, capture=True):
    remove_named(doc, "gp_")
    nz, nt = skin.shape[:2]
    meta = {"kind": "skin", "rings": int(nz), "segments": int(nt), "params": params}
    skin_mesh = to_rhino_mesh(skin.reshape(-1, 3), generators.skin_faces(nz, nt))
    skin_id = add_mesh(doc, skin_mesh, "gp_pelle", layer="GP::pelle", color=(120, 160, 210), meta=meta)
    doc.Objects.Hide(skin_id, True)
    solid_id = add_mesh(doc, to_rhino_mesh(V, F), "gp_solido", layer="GP::solido")
    mesh_io.write_stl(os.path.join(iter_dir, "model.stl"), V, F)
    out = {"triangles": int(len(F)), "vertices": int(len(V)), "stl": os.path.join(iter_dir, "model.stl")}
    if capture:
        out["views"] = capture_views(doc, os.path.join(iter_dir, "views"), solid_id, V, F)
    doc.Views.Redraw()
    return out


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
