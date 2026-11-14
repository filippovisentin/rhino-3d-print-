# r: numpy
"""Slicer interno per Rhino 8: finestra con i parametri di stampa 3D di cio che hai disegnato.

Avvio: esegui `rhino/avvia_slicer.py` con _ScriptEditor oppure _-RunPythonScript.

Lavora sugli oggetti selezionati (mesh, polisuperfici, estrusioni, SubD; piu oggetti = un piatto
solo). Mostra nella vista il piano della stampante, la mappa degli sbalzi (arancio = vicino alla
soglia, rosso = oltre) e, strato per strato, i percorsi: pareti e riempimento.

Nella finestra: stampante, materiale, strato, pareti, riempimento, strati pieni, supporti; esito dei
controlli (mesh chiusa, ingombro, sbalzi, isole, appoggio, stabilita) e stime di peso, filo e tempo.
I percorsi e le stime sono un'ANTEPRIMA geometrica: il pulsante "Affetta con Bambu Studio" lancia lo
slicer vero con le stesse impostazioni e riporta peso e tempo calcolati da lui.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import traceback

import numpy as np
import Rhino
import Rhino.Geometry as rg
import System
import System.Drawing as sd
import scriptcontext as sc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "rhino")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gp_panel  # noqa: E402
import gp_rhino  # noqa: E402
from generative_print import bambu, checks, mesh_io, printcheck, slicer, supports  # noqa: E402

STICKY = "generative_print_slicer"
MATERIALS = [("PLA", 1.24), ("PETG", 1.27), ("ABS", 1.04), ("ASA", 1.07), ("TPU", 1.21)]
PRINTABLE = (rg.Mesh, rg.Brep, rg.Extrusion, rg.SubD, rg.Surface)
CLAY = (214, 208, 198)
WALL_COLORS = [(235, 95, 25), (245, 170, 40), (250, 205, 90)]      # esterna, interne
INFILL_COLOR = (60, 140, 215)
SUPPORT_COLOR = (40, 160, 90)
SUPPORT_FACE_COLOR = (20, 110, 60)
PAINT_MODES = [(None, "Spenta"), (1, "Forza i supporti"), (-1, "Vieta i supporti"), (0, "Cancella")]
PAINT_COLORS = {1: (55, 110, 230), -1: (95, 70, 110)}
SUPPORT_TYPES = [(None, "Nessuno"), ("grid", "Normali a griglia"), ("snug", "Normali aderenti"),
                 ("tree", "Ad albero")]


def int_array(values):
    """Array di interi .NET da numpy, copiando la memoria in blocco."""
    a = np.ascontiguousarray(values, dtype=np.int32)
    out = System.Array.CreateInstance(System.Int32, len(a))
    if len(a):
        try:
            System.Runtime.InteropServices.Marshal.Copy(System.IntPtr(System.Int64(a.ctypes.data)), out, 0, len(a))
        except Exception:
            return System.Array[System.Int32](a.tolist())
    return out


def mesh_from_geometry(geo):
    """Mesh triangolata e saldata da un oggetto di Rhino; None se il tipo non e stampabile."""
    if isinstance(geo, rg.Mesh):
        m = geo.DuplicateMesh()
    elif isinstance(geo, rg.SubD):
        m = rg.Mesh.CreateFromSubD(geo, 3)
    else:
        brep = geo if isinstance(geo, rg.Brep) else (geo.ToBrep() if hasattr(geo, "ToBrep") else None)
        if brep is None:
            return None
        mp = rg.MeshingParameters.QualityRenderMesh
        # triangoli non troppo grandi: la pittura dei supporti lavora per faccia
        mp.MaximumEdgeLength = max(2.0, brep.GetBoundingBox(False).Diagonal.Length / 80.0)
        mp.SimplePlanes = False
        mp.RefineGrid = True
        parts = rg.Mesh.CreateFromBrep(brep, mp)
        if not parts:
            return None
        m = rg.Mesh()
        for part in parts:
            m.Append(part)
    if m is None:
        return None
    m.Faces.ConvertQuadsToTriangles()
    m.Vertices.CombineIdentical(True, True)
    m.Compact()
    return m


def display_mesh(mesh):
    """Copia per il disegno: vertici separati sugli spigoli vivi, cosi l'ombreggiatura non si spalma.
    Gli indici delle facce restano quelli della mesh di calcolo."""
    m = mesh.DuplicateMesh()
    m.Unweld(math.radians(40.0), True)
    m.Normals.ComputeNormals()
    return m


def as_polyline(curve):
    ok, pl = curve.TryGetPolyline()
    if ok:
        return pl
    pc = curve.ToPolyline(0.01, 0.05, 0.05, 0.0)
    if pc is None:
        return None
    ok, pl = pc.TryGetPolyline()
    return pl if ok else None


def poly_xy(pl):
    pts = np.array([[p.X, p.Y] for p in pl], dtype=float)
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts


def poly_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


class Job(object):
    """Gli oggetti da stampare, uniti in una mesh sola (posizione del modello) e appoggiati al piano."""

    def __init__(self, doc, objs):
        self.doc = doc
        self.obj_ids = [o.Id for o in objs]
        names = [o.Attributes.Name or o.ObjectType.ToString() for o in objs]
        self.name = names[0] if len(names) == 1 else "%d oggetti" % len(names)
        joined = rg.Mesh()
        for o in objs:
            m = mesh_from_geometry(o.Geometry)
            if m is not None:
                joined.Append(m)
        self.V0, self.F = gp_rhino.from_rhino_mesh(joined)        # come disegnato
        if len(self.F) == 0:
            raise ValueError("Nessuna geometria stampabile negli oggetti scelti")
        self.paint = np.zeros(len(self.F), dtype=np.int8)     # supporti dipinti: +1 forza, -1 vieta
        self.orient(np.eye(3))

    def orient(self, R):
        """Orienta il pezzo per la stampa (R ruota attorno al centro dell'ingombro disegnato; il pezzo
        resta appoggiato alla quota di partenza). Il modello di Rhino non cambia finche non si applica."""
        self.R = np.asarray(R, dtype=float)
        self.V = printcheck.rotate(self.V0, self.R)
        self.z0 = float(self.V[:, 2].min())
        self.height = float(self.V[:, 2].max()) - self.z0
        self.mesh = gp_rhino.to_rhino_mesh(self.V, self.F)       # stessi indici di faccia di F
        self.show = display_mesh(self.mesh)                       # copia a spigoli vivi per il disegno
        self.Vw = self.V - np.array([0.0, 0.0, self.z0])          # appoggiato al piano
        c = (self.V.max(axis=0) + self.V.min(axis=0)) / 2.0
        self.centre = (float(c[0]), float(c[1]))
        T = self.V[self.F]
        self.face_centre = T.mean(axis=1)
        self.face_normal = checks.face_normals_areas(self.V, self.F)[0]
        zmax = self.Vw[:, 2][self.F].max(axis=1)                  # facce ordinate per quota massima:
        self.zorder = np.argsort(zmax, kind="stable")             # il pezzo 'gia stampato' sotto uno strato
        self.zsorted = zmax[self.zorder]                          # e un prefisso di questo elenco
        self.face_sets = None
        self.layer_faces = None
        self.ray_mesh = None
        self.support = None       # supporti generati (generative_print.supports), pezzo appoggiato al piano
        self.sup_mesh = None      # rami dell'albero, mesh di Rhino con i segmenti ordinati per quota
        self.sup_faces = None
        self.sup_lines, self.sup_base, self.sup_face, self.sup_circles = [], [], [], []
        self.walls = []           # (indice di parete, rg.Polyline)
        self.infill = []          # rg.Line
        self.open_loops = []      # rg.Polyline non chiuse: mesh aperta a quella quota

    def layer_count(self, h):
        return max(1, int(math.ceil(self.height / h)))


class Conduit(Rhino.Display.DisplayConduit):
    def __init__(self, owner):
        super().__init__()
        self.sl = owner

    def CalculateBoundingBox(self, e):
        j = self.sl.job
        if j is not None:
            e.IncludeBoundingBox(j.mesh.GetBoundingBox(False))

    def PostDrawObjects(self, e):
        sl = self.sl
        j = sl.job
        if j is None:
            return
        try:
            bed = sl.settings["bed"]
            fits = sl.result is None or sl.result["fits"]
            plane = rg.Plane(rg.Point3d(j.centre[0], j.centre[1], j.z0), rg.Vector3d.ZAxis)
            rect = rg.Rectangle3d(plane, rg.Interval(-bed[0] / 2.0, bed[0] / 2.0),
                                  rg.Interval(-bed[1] / 2.0, bed[1] / 2.0))
            col = sd.Color.FromArgb(90, 110, 150) if fits else sd.Color.FromArgb(215, 45, 40)
            e.Display.DrawPolyline(rect.ToPolyline(), col, 2)
            if j.support is not None:
                green = sd.Color.FromArgb(*SUPPORT_COLOR)
                if j.sup_mesh is not None and j.sup_faces is not None and j.sup_faces.Length:
                    e.Display.DrawMeshShaded(j.sup_mesh, sl.mat_sup, j.sup_faces)
                if j.sup_lines:
                    e.Display.DrawLines(j.sup_lines, green, 1)
                if j.sup_base:
                    e.Display.DrawLines(j.sup_base, green, 2)
                if j.sup_face:
                    e.Display.DrawLines(j.sup_face, sd.Color.FromArgb(*SUPPORT_FACE_COLOR), 2)
                for c in j.sup_circles:
                    e.Display.DrawCircle(c, green, 2)
            if sl.layer_on:
                if j.layer_faces is not None and j.layer_faces.Length:
                    e.Display.DrawMeshShaded(j.show, sl.mat, j.layer_faces)
                for k, pl in j.walls:
                    c = WALL_COLORS[min(k, len(WALL_COLORS) - 1)]
                    e.Display.DrawPolyline(pl, sd.Color.FromArgb(*c), 3 if k == 0 else 2)
                if j.infill:
                    e.Display.DrawLines(j.infill, sd.Color.FromArgb(*INFILL_COLOR), 1)
                for pl in j.open_loops:
                    e.Display.DrawPolyline(pl, sd.Color.FromArgb(215, 45, 40), 4)
            elif j.face_sets is not None:
                heat = (sl.mat, sl.mat_warn, sl.mat_over) if sl.heat else (sl.mat, sl.mat, sl.mat)
                for faces, mat in zip(j.face_sets, heat + (sl.mat_force, sl.mat_block)):
                    if faces.Length:
                        e.Display.DrawMeshShaded(j.show, mat, faces)
            else:
                e.Display.DrawMeshShaded(j.show, sl.mat)
            if sl.paint_cursor is not None and sl.paint_mode is not None and not sl.layer_on:
                p, n = sl.paint_cursor
                col = PAINT_COLORS.get(sl.paint_mode, (120, 120, 120))
                e.Display.DrawCircle(rg.Circle(rg.Plane(rg.Point3d(p[0], p[1], p[2]),
                                                        rg.Vector3d(n[0], n[1], n[2])), sl.paint_radius),
                                     sd.Color.FromArgb(*col), 2)
        except Exception:
            sl.fail()


class PaintMouse(Rhino.UI.MouseCallback):
    """Pittura dei supporti col mouse: trascina col sinistro sul pezzo (Ctrl: cancella)."""

    def __init__(self, owner):
        super().__init__()
        self.sl = owner
        self.down = False

    def _hit(self, e):
        sl = self.sl
        j = sl.job
        if j is None or sl.paint_mode is None or sl.layer_on or e.View is None:
            return None
        line = e.View.ActiveViewport.ClientToWorld(e.ViewportPoint)
        d = line.From - line.To
        d.Unitize()
        t = rg.Intersect.Intersection.MeshRay(j.mesh, rg.Ray3d(line.To, d))
        if t < 0:
            return None
        p = line.To + d * t
        return np.array([p.X, p.Y, p.Z]), sl.face_at(p)

    def OnMouseDown(self, e):
        try:
            if e.MouseButton != Rhino.UI.MouseButton.Left:
                return
            hit = self._hit(e)
            if hit is None:
                return
            e.Cancel = True
            self.down = True
            self.sl.paint_at(hit[0], hit[1], erase=bool(e.CtrlKeyDown))
            e.View.Redraw()
        except Exception:
            self.sl.fail()

    def OnMouseMove(self, e):
        try:
            sl = self.sl
            if sl.job is None or sl.paint_mode is None:
                return
            hit = self._hit(e)
            if hit is None:
                if sl.paint_cursor is not None:
                    sl.paint_cursor = None
                    e.View.Redraw()
                return
            n = sl.job.face_normal[hit[1]] if hit[1] is not None else np.array([0.0, 0.0, 1.0])
            sl.paint_cursor = (hit[0], n)
            if self.down:
                sl.paint_at(hit[0], hit[1], erase=bool(e.CtrlKeyDown))
            e.View.Redraw()
        except Exception:
            self.sl.fail()

    def OnMouseUp(self, e):
        try:
            if self.down:
                self.down = False
                e.Cancel = True
                self.sl.guard(self.sl.analyse)          # i supporti si ricalcolano a fine pennellata
        except Exception:
            self.sl.fail()


class Slicer(object):
    def __init__(self):
        self.job = None
        self.form = None
        self.result = None
        self.error = ""
        self.loading = False
        self.heat = True
        self.layer_on = False
        self.layer_index = 0
        self.layer_text = ""
        self.extra = []               # righe aggiunte al rapporto (spessori, Bambu)
        self.proc = None
        self.timer = None
        self.printer = bambu.default_printer()
        self.settings = printcheck.settings({"bed": bambu.printer_volume(self.printer)})
        self.material_name = "PLA"
        self.conduit = Conduit(self)

        def mat(rgb):
            m = Rhino.Display.DisplayMaterial(sd.Color.FromArgb(*rgb))
            m.IsTwoSided = True
            m.BackDiffuse = m.Diffuse
            return m
        self.mat, self.mat_warn, self.mat_over = mat(CLAY), mat((240, 175, 50)), mat((215, 45, 40))
        self.mat_sup = mat(SUPPORT_COLOR)
        self.mat_force, self.mat_block = mat(PAINT_COLORS[1]), mat(PAINT_COLORS[-1])
        self.paint_mode = None        # None = spenta, 1 forza, -1 vieta, 0 cancella
        self.paint_radius = 6.0
        self.paint_cursor = None
        self.mouse = PaintMouse(self)
        self.docked = False

    def fail(self):
        self.error = traceback.format_exc()
        self.conduit.Enabled = False
        self.mouse.Enabled = False
        Rhino.RhinoApp.WriteLine("Generative Print: errore nello slicer\n" + self.error)

    # ----------------------------------------------------------- oggetti
    def find_targets(self, doc):
        objs = [o for o in doc.Objects.GetObjectList(Rhino.DocObjects.ObjectEnumeratorSettings())
                if isinstance(o.Geometry, PRINTABLE)]
        sel = [o for o in objs if o.IsSelected(False) > 0]
        if sel:
            return sel
        return objs if len(objs) == 1 else []

    def open(self, doc, objs=None):
        self.close(close_form=False)
        objs = objs if objs is not None else self.find_targets(doc)
        if not objs:
            self.refresh()
            return None
        self.job = Job(doc, objs)
        for oid in self.job.obj_ids:
            doc.Objects.Hide(oid, True)
        doc.Objects.UnselectAll()
        self.extra = []
        self.layer_index = 0
        self.conduit.Enabled = True
        self.mouse.Enabled = self.paint_mode is not None
        self.analyse()
        return self.job

    def close(self, close_form=True):
        j, self.job = self.job, None
        self.conduit.Enabled = False
        self.mouse.Enabled = False
        self.result = None
        if j is not None:
            for oid in j.obj_ids:
                j.doc.Objects.Show(oid, True)
            j.doc.Views.Redraw()
        if close_form and self.form is not None:
            form, self.form = self.form, None
            if self.docked:
                gp_panel.undock("slicer")
            else:
                form.Close()

    # ----------------------------------------------------------- calcolo
    def set(self, **over):
        """Cambia impostazioni da script (stesse chiavi di printcheck.DEFAULTS, piu printer e material)."""
        if "printer" in over:
            self.printer = over.pop("printer")
            self.settings["bed"] = bambu.printer_volume(self.printer)
        if "material" in over:
            self.material_name = over.pop("material")
            self.settings["density_g_cm3"] = dict(MATERIALS)[self.material_name]
        self.settings.update(over)
        self.analyse()

    def analyse(self):
        j = self.job
        if j is None:
            self.refresh()
            return None
        res = printcheck.analyse(j.Vw, j.F, self.settings, support_paint=j.paint if j.paint.any() else None)
        j.support, j.sup_mesh = res.get("support"), None
        self.result = res
        self._face_sets()
        if self.layer_on:
            self.set_layer(self.layer_index)
        else:
            self._support_draw()
        self.refresh()
        j.doc.Views.Redraw()
        return res

    def set_layer(self, index):
        """Calcola e mostra lo strato 'index' (0 = primo): pezzo gia stampato sotto, pareti e riempimento."""
        j, s = self.job, self.settings
        if j is None:
            return
        h, lw = float(s["layer"]), float(s["line_width"])
        n = j.layer_count(h)
        index = int(max(0, min(n - 1, index)))
        self.layer_index, self.layer_on = index, True
        zw = min((index + 0.5) * h, j.height - 1e-3)
        j.layer_faces = int_array(j.zorder[:int(np.searchsorted(j.zsorted, index * h + 1e-9, side="right"))])
        zworld = j.z0 + zw
        plane = rg.Plane(rg.Point3d(0, 0, zworld), rg.Vector3d.ZAxis)
        polys = rg.Intersect.Intersection.MeshPlane(j.mesh, plane) or []
        closed = [p for p in polys if p.IsClosed and p.Count >= 4]
        j.open_loops = [p for p in polys if not p.IsClosed]
        walls, inner = self._walls(closed, plane, lw, int(s["walls"]))
        j.walls = walls
        solid = index < int(s["bottom_layers"]) or index >= n - int(s["top_layers"])
        spacing = lw if solid else (lw / float(s["infill"]) if float(s["infill"]) > 0.001 else 0.0)
        j.infill = []
        fill_len = 0.0
        if spacing > 0 and inner:
            seg = slicer.hatch(inner, 45.0 if index % 2 == 0 else 135.0, spacing, min_length=2.0 * lw)
            j.infill = [rg.Line(rg.Point3d(a[0], a[1], zworld), rg.Point3d(b[0], b[1], zworld))
                        for a, b in seg.tolist()]
            fill_len = float(np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum()) if len(seg) else 0.0
        wall_len = sum(pl.Length for _, pl in walls)
        self._support_draw()
        self.layer_text = ("Strato %d / %d   z = %.2f mm   contorni %d   pareti %.0f mm   %s %.0f mm%s"
                           % (index + 1, n, zw, len(closed), wall_len,
                              "strato pieno" if solid else "riempimento", fill_len,
                              "   APERTI: %d" % len(j.open_loops) if j.open_loops else ""))
        self.refresh()
        j.doc.Views.Redraw()
        return {"index": index, "layers": n, "z": zw, "loops": len(closed), "walls": len(walls),
                "wall_mm": wall_len, "infill_mm": fill_len, "solid": solid, "open": len(j.open_loops)}

    def _face_sets(self):
        """Elenchi di facce per il disegno: ok, vicine alla soglia, oltre soglia, supporti forzati,
        supporti vietati (la pittura copre la mappa degli sbalzi)."""
        j, res = self.job, self.result
        over, warn, free = res["over_mask"], res["warn_mask"], j.paint == 0
        j.face_sets = tuple(int_array(np.where(m)[0]) for m in (
            ~over & ~warn & free, warn & free, over & free, j.paint > 0, j.paint < 0))

    def face_at(self, point):
        """Indice della faccia del pezzo sotto un punto della superficie (None se non si trova)."""
        mp = self.job.mesh.ClosestMeshPoint(rg.Point3d(float(point[0]), float(point[1]), float(point[2])), 0.5)
        return int(mp.FaceIndex) if mp is not None and mp.FaceIndex >= 0 else None

    def paint_at(self, point, face=None, erase=False, mode=None, radius=None):
        """Dipinge i supporti attorno a un punto del pezzo (coordinate del modello): le facce entro
        il raggio rivolte dallo stesso lato della faccia toccata. Restituisce quante facce cambia."""
        j = self.job
        mode = self.paint_mode if mode is None else mode
        if j is None or mode is None:
            return 0
        r = float(radius or self.paint_radius)
        near = ((j.face_centre - np.asarray(point, dtype=float)) ** 2).sum(axis=1) < r * r
        if face is None:
            face = self.face_at(point)
        if face is not None:
            near[face] = True
            near &= (j.face_normal @ j.face_normal[face]) > 0.0
        value = 0 if erase else int(mode)
        changed = int((j.paint[near] != value).sum())
        if changed:
            j.paint[near] = value
            if self.result is not None:
                self._face_sets()
        return changed

    def clear_paint(self):
        if self.job is not None and self.job.paint.any():
            self.job.paint[:] = 0
            self.analyse()

    def _support_draw(self):
        """Prepara cio che la vista disegna dei supporti: tutto, oppure fino allo strato corrente
        (con i tratti di quello strato: linee di base, interfaccia, sezioni dei rami)."""
        j = self.job
        j.sup_lines, j.sup_base, j.sup_face, j.sup_circles, j.sup_faces = [], [], [], [], None
        sp = j.support
        if sp is None or len(sp["columns"]) == 0:
            j.sup_mesh = None
            return
        h, z0 = float(self.settings["layer"]), j.z0
        top = (self.layer_index + 1) * h if self.layer_on else float("inf")
        zw = min((self.layer_index + 0.5) * h, j.height - 1e-3)
        if sp["type"] == "tree":
            if j.sup_mesh is None:
                segs = sp["segments"][np.argsort(sp["segments"][:, 2], kind="stable")]
                Vt, Ft = supports.tree_mesh(segs)
                j.sup_mesh = gp_rhino.to_rhino_mesh(Vt + [0.0, 0.0, z0], Ft)
                j.sup_mesh.Normals.ComputeNormals()
                j.sup_ztop = segs[:, 2]
            n = int(np.searchsorted(j.sup_ztop, top, side="right"))
            j.sup_faces = int_array(np.arange(n * 16))
            if self.layer_on:
                for x, y, r in supports.tree_sections(sp["segments"], zw).tolist():
                    j.sup_circles.append(rg.Circle(rg.Plane(rg.Point3d(x, y, z0 + zw), rg.Vector3d.ZAxis), r))
            return
        cols, ij = sp["columns"], sp["cells"]
        k = max(1, int(round(2.5 / sp["res"])))
        m = (ij[:, 0] % k == 0) & (ij[:, 1] % k == 0) & (cols[:, 3] < top)
        j.sup_lines = [rg.Line(rg.Point3d(x, y, z0 + zb), rg.Point3d(x, y, z0 + min(zt, top)))
                       for x, y, zt, zb in cols[m].tolist()]
        if self.layer_on:
            base, face = supports.layer_pattern(sp, zw, h)
            for seg, store in ((base, j.sup_base), (face, j.sup_face)):
                for a, b in seg.tolist():
                    store.append(rg.Line(rg.Point3d(a[0], a[1], z0 + zw), rg.Point3d(b[0], b[1], z0 + zw)))

    def _walls(self, closed, plane, lw, count, tol=0.01):
        """Pareti di uno strato: contorni spostati verso il materiale di mezza linea, una linea e mezza...
        Un contorno spostato si accetta solo se resta ad almeno 0,8 linee da quelli gia accettati:
        nelle pareti sottili l'esterno e l'interno si incontrano e le linee non si sovrappongono.
        Restituisce (pareti, contorni piu interni per il riempimento)."""
        curves = [p.ToPolylineCurve() for p in closed]
        depth = []
        for i, p in enumerate(closed):
            depth.append(sum(1 for k, c in enumerate(curves)
                             if k != i and c.Contains(p[0], plane, tol) == rg.PointContainment.Inside))
        areas = [poly_area(poly_xy(p)) for p in closed]
        order = sorted(range(len(closed)), key=lambda i: depth[i])
        sign, accepted, walls, inner = {}, [], [], {}

        def run(i, d, sgn):
            try:
                res = curves[i].Offset(plane, sgn * d, tol, rg.CurveOffsetCornerStyle.Sharp)
            except Exception:
                res = None
            out = []
            for c in (res or []):
                pl = as_polyline(c) if (c is not None and c.IsClosed) else None
                if pl is not None and pl.Count >= 4:
                    out.append((c, pl))
            return out

        def offsets(i, d):
            if i not in sign:
                sign[i] = 0.0
                for sgn in (1.0, -1.0):
                    got = run(i, d, sgn)
                    a = sum(poly_area(poly_xy(pl)) for _, pl in got)
                    if got and ((a > areas[i]) if depth[i] % 2 else (a < areas[i])):
                        sign[i] = sgn
                        return got
                return []
            return run(i, d, sign[i]) if sign[i] else []

        def clear(c):
            ts = c.DivideByCount(12, True)
            for t in (ts or []):
                pt = c.PointAt(t)
                for a in accepted:
                    ok, ta = a.ClosestPoint(pt)
                    if ok and pt.DistanceTo(a.PointAt(ta)) < 0.8 * lw:
                        return False
            return True

        for k in range(count):
            for i in order:
                for c, pl in offsets(i, lw * (k + 0.5)):
                    if clear(c):
                        accepted.append(c)
                        walls.append((k, pl))
                        if i in inner and inner[i][0] == k:
                            inner[i][1].append(poly_xy(pl))
                        else:
                            inner[i] = (k, [poly_xy(pl)])
        return walls, [p for _, ps in inner.values() for p in ps]

    # ----------------------------------------------------------- orientamento
    def _reoriented(self, R, note):
        self.job.orient(R)
        self.extra = [l for l in self.extra if not l.startswith("Orientamento")] + [note]
        self.layer_index = 0
        return self.analyse()

    def rotate_by(self, axis, degrees=90.0):
        """Ruota il pezzo di 'degrees' attorno all'asse 'x', 'y' o 'z' del mondo (solo per la stampa)."""
        if self.job is None:
            return None
        a = math.radians(degrees)
        c, s = math.cos(a), math.sin(a)
        Ra = {"x": [[1, 0, 0], [0, c, -s], [0, s, c]], "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
              "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]
        return self._reoriented(np.array(Ra, dtype=float) @ self.job.R,
                                "Orientamento: ruotato di %g gradi attorno a %s (il modello non e cambiato)."
                                % (degrees, axis.upper()))

    def best_orientation(self):
        """Prova gli orientamenti candidati e mette il pezzo in quello con meno sbalzi."""
        j = self.job
        if j is None:
            return None
        before = self.result["overhang_area_mm2"] if self.result else float("nan")
        ranked = printcheck.orientations(j.V0, j.F, self.settings)
        self.ranked = ranked
        b = ranked[0]
        self._reoriented(b["R"], "Orientamento migliore tra %d provati: sbalzo %.0f mm2 (prima %.0f), appoggio "
                         "%.0f mm2, altezza %.1f mm. Il modello non e cambiato: 'Applica al modello' per ruotarlo."
                         % (len(ranked), b["overhang_area_mm2"], before, b["contact_mm2"], b["height"]))
        return b

    def reset_orientation(self):
        if self.job is not None:
            self._reoriented(np.eye(3), "Orientamento: come disegnato.")

    def apply_orientation(self):
        """Ruota davvero gli oggetti di Rhino come nell'anteprima (un solo passo di Annulla)."""
        j = self.job
        if j is None or np.allclose(j.R, np.eye(3)):
            return False
        c = (j.V0.max(axis=0) + j.V0.min(axis=0)) / 2.0
        shift = c - j.R @ c
        shift[2] += float(j.V0[:, 2].min()) - float(((j.V0 - c) @ j.R.T + c)[:, 2].min())
        xf = rg.Transform(1.0)
        for r in range(3):
            for k in range(3):
                xf[r, k] = float(j.R[r, k])
            xf[r, 3] = float(shift[r])
        doc, ids = j.doc, list(j.obj_ids)
        sn = doc.BeginUndoRecord("Generative Print: orientamento di stampa")
        for oid in ids:
            doc.Objects.Transform(oid, xf, True)
        doc.EndUndoRecord(sn)
        st = Rhino.DocObjects.ObjectEnumeratorSettings()
        st.HiddenObjects = True
        objs = [o for o in doc.Objects.GetObjectList(st) if o.Id in ids]
        self.open(doc, objs)
        self.extra = ["Orientamento applicato al modello (Annulla in Rhino per tornare indietro)."]
        self.refresh()
        return True

    def thickness(self):
        j = self.job
        th = checks.thickness(j.V, j.F, gp_rhino.raycast_rhino(j.mesh), samples=1500)
        lw = float(self.settings["line_width"])
        ok = th["p1"] >= 2 * lw - 1e-6
        self.extra = [l for l in self.extra if not l.startswith("Spessore")]
        self.extra.append("Spessore: minimo %.2f mm, 1 percentile %.2f, mediana %.2f (%d campioni) -> %s"
                          % (th["min"], th["p1"], th["median"], th["samples"],
                             "ok" if ok else "sotto due linee da %g mm: pareti fragili" % lw))
        self.refresh()
        return th

    # ----------------------------------------------------------- slicer vero
    def bambu_start(self):
        j = self.job
        if j is None or self.proc is not None:
            return False
        exe = os.path.join(REPO, ".venv", "Scripts", "python.exe")
        if not bambu.available() or not os.path.exists(exe):
            self.extra.append("Bambu: serve Bambu Studio installato e l'ambiente .venv del progetto.")
            self.refresh()
            return False
        out = os.path.join(tempfile.gettempdir(), "generative_print_slicer")
        os.makedirs(out, exist_ok=True)
        stl = os.path.join(out, "oggetto.stl")
        mesh_io.write_stl(stl, j.Vw, j.F)
        s = self.settings
        pr = {k: s[k] for k in ("layer", "line_width", "walls", "infill", "top_layers", "bottom_layers",
                                "overhang_limit_deg")}
        pr["generic"] = True
        pjson = os.path.join(out, "params.json")
        with open(pjson, "w", encoding="utf-8") as fh:
            json.dump({"object": "solid", "print": pr}, fh)
        self.bambu_json = os.path.join(out, "bambu", "slicer.json")
        if os.path.exists(self.bambu_json):
            os.remove(self.bambu_json)
        cmd = [exe, "-m", "generative_print.bambu", stl, pjson, "--printer", self.printer,
               "--out", os.path.join(out, "bambu"), "--no-analysis", "--material", self.material_name] \
            + (["--support", "--support-type", s["support_type"]] if s["supports"] else []) \
            + (["--support-bed-only"] if s["supports"] and s["support_bed_only"] else [])
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, creationflags=0x08000000)
        self.t0 = time.time()
        if j.paint.any():
            self.extra.append("Nota: la pittura dei supporti vale per la nostra anteprima; a Bambu non viene "
                              "passata (lui li mette in automatico).")
        if self.timer is not None:
            self.timer.Start()
        self.bambu_poll()
        return True

    def bambu_poll(self):
        """Controlla se Bambu ha finito; restituisce True quando il risultato e nel rapporto."""
        if self.proc is None:
            return True
        self.extra = [l for l in self.extra if not l.startswith("Bambu")]
        if self.proc.poll() is None:
            self.extra.append("Bambu Studio sta affettando... %d s" % (time.time() - self.t0))
            self.refresh()
            return False
        text = (self.proc.stdout.read() or "").strip()
        self.proc = None
        if self.timer is not None:
            self.timer.Stop()
        self.bambu_result = None
        if os.path.exists(self.bambu_json):
            with open(self.bambu_json, encoding="utf-8") as fh:
                self.bambu_result = json.load(fh)
            lines = bambu.summary_lines(self.bambu_result)
            r, b = self.result, self.bambu_result
            if b.get("ok") and r and "mass_g" in r:
                lines.append("Confronto: la nostra stima era %.1f g e %.1f h (densita %g)."
                             % (r["mass_g"], r["time_h"], self.settings["density_g_cm3"]))
                if self.material_name not in b.get("filament", ""):
                    lines.append("Nota: per %s non c'e un profilo Bambu su questa stampante, ha usato %s."
                                 % (self.material_name, b.get("filament")))
        else:
            lines = ["non ha prodotto un risultato:"] + text.splitlines()[-4:]
        self.extra += ["Bambu (%d s): %s" % (time.time() - self.t0, lines[0])] + ["Bambu: " + l for l in lines[1:]]
        self.refresh()
        return True

    # ----------------------------------------------------------- modifica plastica
    def sculpt_target(self, edge=None):
        """Mesh adatta ai pennelli dall'oggetto corrente: maglia regolare (QuadRemesh), aggiunta al
        documento come 'gp_scolpibile'. L'oggetto originale resta nascosto, non viene cancellato."""
        j = self.job
        if j is None:
            return None
        size = j.V.max(axis=0) - j.V.min(axis=0)
        edge = float(edge or max(1.5, min(6.0, float(np.linalg.norm(size)) / 70.0)))
        qp = rg.QuadRemeshParameters()
        qp.TargetEdgeLength = edge
        qp.AdaptiveSize = 0
        qp.DetectHardEdges = True
        m = None
        try:
            m = j.mesh.QuadRemesh(qp)
        except Exception:
            m = None
        note = "maglia regolare, lato circa %.1f mm" % edge
        if m is None or m.Faces.Count < 50:
            m = j.mesh.DuplicateMesh()
            note = "QuadRemesh non riuscito: uso la mesh cosi com'e"
        m.Faces.ConvertQuadsToTriangles()
        m.Vertices.CombineIdentical(True, True)
        m.Compact()
        m.Normals.ComputeNormals()
        doc, ids = j.doc, list(j.obj_ids)
        gp_rhino.remove_named(doc, "gp_scolpibile")
        sid = gp_rhino.add_mesh(doc, m, "gp_scolpibile", layer="GP::scolpibile", color=CLAY,
                                meta={"kind": "solid", "source": [str(i) for i in ids]})
        self.close(close_form=False)
        for oid in ids:                              # l'originale resta nascosto sotto la copia
            doc.Objects.Hide(oid, True)
        return sid, note

    # ----------------------------------------------------------- interfaccia Eto
    def report_text(self):
        if self.job is None:
            return ("Nessun oggetto.\n\nSeleziona in Rhino cio che vuoi stampare (mesh, polisuperficie,\n"
                    "estrusione o SubD) e premi 'Usa selezione'.")
        L = [printcheck.report(self.result)]
        if self.extra:
            L += [""] + self.extra
        if self.error:
            L += ["", self.error]
        return "\n".join(L)

    def refresh(self):
        if self.form is None:
            return
        j = self.job
        self.w_obj.Text = ("Oggetto: %s   (%d triangoli)" % (j.name, len(j.F))) if j else "Oggetto: nessuno"
        self.w_report.Text = self.report_text()
        if j is not None:
            n = j.layer_count(float(self.settings["layer"]))
            self.loading = True
            self.w_layer.MaxValue = max(1, n - 1)
            self.w_layer.Value = min(self.layer_index, n - 1)
            self.loading = False
        self.w_layer_info.Text = self.layer_text if self.layer_on else ""

    def guard(self, fn):
        if self.loading:
            return
        try:
            self.error = ""
            fn()
        except Exception:
            self.error = traceback.format_exc()
            self.refresh()

    def show_panel(self, dock=True):
        """Apre i controlli: come scheda agganciata accanto ai Livelli (dock=True) o come finestra."""
        import Eto.Forms as ef
        import Eto.Drawing as ed
        if self.form is not None:
            if self.docked:
                gp_panel.dock("slicer", self.form, self.on_form_closed)
            else:
                self.form.BringToFront()
            self.refresh()
            return
        self.loading = True

        def label(text):
            lb = ef.Label()
            lb.Text = text
            lb.VerticalAlignment = ef.VerticalAlignment.Center
            return lb

        def title(text):
            lb = label(text)
            lb.Font = ed.SystemFonts.Bold()
            return lb

        def button(text, handler, tip=None):
            b = ef.Button()
            b.Text = text
            if tip:
                b.ToolTip = tip
            b.Click += lambda s, e: self.guard(handler)
            return b

        def stepper(lo, hi, value, step, places):
            w = ef.NumericStepper()
            w.MinValue, w.MaxValue, w.Increment, w.DecimalPlaces = lo, hi, step, places
            w.Value = value
            w.ValueChanged += lambda s, e: self.guard(self.on_settings)
            return w

        def dropdown(items, current):
            w = ef.DropDown()
            for it in items:
                w.Items.Add(it)
            w.SelectedIndex = items.index(current) if current in items else 0
            w.SelectedIndexChanged += lambda s, e: self.guard(self.on_settings)
            return w

        def check(text, value, handler):
            w = ef.CheckBox()
            w.Text = text
            w.Checked = value
            w.CheckedChanged += lambda s, e: self.guard(handler)
            return w

        def table(rows, spacing=(6, 5)):
            lay = ef.DynamicLayout()
            lay.Spacing = ed.Size(*spacing)
            for row in rows:
                lay.AddRow(*row)
            return lay

        s = self.settings
        self.printers = list(bambu.PRINTERS)
        self.w_obj = ef.Label()
        self.w_obj.Wrap = ef.WrapMode.Word
        self.w_printer = dropdown(self.printers, self.printer)
        self.w_material = dropdown([m for m, _ in MATERIALS], self.material_name)
        self.w_h = stepper(0.08, 0.4, s["layer"], 0.04, 2)
        self.w_lw = stepper(0.3, 1.2, s["line_width"], 0.01, 2)
        self.w_walls = stepper(1, 12, s["walls"], 1, 0)
        self.w_infill = stepper(0, 100, round(100 * s["infill"]), 5, 0)
        self.w_top = stepper(0, 20, s["top_layers"], 1, 0)
        self.w_bottom = stepper(0, 20, s["bottom_layers"], 1, 0)
        self.w_limit = stepper(30, 70, s["overhang_limit_deg"], 1, 0)
        kinds = [text for _, text in SUPPORT_TYPES]
        self.w_sup = dropdown(kinds, dict(SUPPORT_TYPES)[s["support_type"]] if s["supports"] else kinds[0])
        self.w_bed = check("Supporti solo dal piano", bool(s["support_bed_only"]), self.on_settings)
        modes = [text for _, text in PAINT_MODES]
        self.w_paint = ef.DropDown()
        for text in modes:
            self.w_paint.Items.Add(text)
        self.w_paint.SelectedIndex = [m for m, _ in PAINT_MODES].index(self.paint_mode)
        self.w_paint.SelectedIndexChanged += lambda s_, e: self.guard(self.on_paint)
        self.w_paint.ToolTip = ("Trascina col tasto sinistro sul pezzo: blu = supporto forzato, viola = vietato. "
                                "Ctrl cancella. I supporti si ricalcolano quando rilasci.")
        self.w_paint_r = ef.Slider()
        self.w_paint_r.MinValue, self.w_paint_r.MaxValue, self.w_paint_r.Value = 1, 30, int(self.paint_radius)
        self.w_paint_r.ValueChanged += lambda s_, e: self.guard(self.on_paint)
        self.w_paint_l = label("Raggio %d mm" % self.paint_radius)
        self.w_heat = check("Mappa degli sbalzi", self.heat, self.on_view)
        self.w_heat.ToolTip = "Arancio = vicino alla soglia, rosso = oltre"
        self.w_layers = check("Strati", self.layer_on, self.on_view)
        self.w_layer = ef.Slider()
        self.w_layer.MinValue, self.w_layer.MaxValue, self.w_layer.Value = 0, 100, 0
        self.w_layer.ValueChanged += lambda s_, e: self.guard(self.on_view)
        self.w_layer_info = ef.Label()
        self.w_layer_info.Wrap = ef.WrapMode.Word
        self.w_report = ef.TextArea()
        self.w_report.ReadOnly = True
        self.w_report.Font = ed.Font("Consolas", 8.5)
        self.w_report.Wrap = True
        self.w_report.Height = 300

        lay = ef.DynamicLayout()
        lay.Padding = ed.Padding(8)
        lay.Spacing = ed.Size(6, 7)
        lay.AddRow(self.w_obj)
        lay.AddRow(button("Usa selezione", self.on_pick, "Analizza gli oggetti selezionati in Rhino"))
        lay.AddRow(title("Stampa"))
        lay.AddRow(table([(label("Stampante"), self.w_printer), (label("Materiale"), self.w_material),
                          (label("Strato (mm)"), self.w_h), (label("Linea (mm)"), self.w_lw),
                          (label("Pareti"), self.w_walls), (label("Riempimento (%)"), self.w_infill),
                          (label("Strati pieni sopra"), self.w_top), (label("Strati pieni sotto"), self.w_bottom),
                          (label("Limite sbalzo (gradi)"), self.w_limit)]))
        lay.AddRow(title("Supporti"))
        lay.AddRow(table([(label("Tipo"), self.w_sup)]))
        lay.AddRow(self.w_bed)
        lay.AddRow(table([(label("Pittura"), self.w_paint), (self.w_paint_l, self.w_paint_r)]))
        lay.AddRow(button("Cancella la pittura", self.clear_paint, "Torna ai supporti automatici"))
        lay.AddRow(title("Vista"))
        lay.AddRow(self.w_heat)
        lay.AddRow(table([(self.w_layers, self.w_layer)]))
        lay.AddRow(self.w_layer_info)
        lay.AddRow(title("Orientamento"))
        lay.AddRow(table([(button("Ruota X 90", lambda: self.rotate_by("x")),
                           button("Ruota Y 90", lambda: self.rotate_by("y"))),
                          (button("Migliore", self.best_orientation, "Prova gli orientamenti e sceglie meno sbalzi"),
                           button("Come disegnato", self.reset_orientation)),
                          (button("Applica al modello", self.apply_orientation,
                                  "Ruota davvero gli oggetti di Rhino (un passo di Annulla)"), None)]))
        lay.AddRow(title("Azioni"))
        lay.AddRow(table([(button("Verifica spessori", self.thickness),
                           button("Affetta con Bambu", self.bambu_start,
                                  "Slicer vero con le stesse impostazioni: peso e tempo precisi")),
                          (button("Esporta STL", self.on_export),
                           button("Modifica plastica", self.on_sculpt, "Pennelli di scultura su una copia"))]))
        lay.AddRow(self.w_report)
        note = label("Pareti, riempimento e supporti sono un'anteprima; peso e tempo precisi: Affetta con Bambu.")
        note.Wrap = ef.WrapMode.Word
        lay.AddRow(note)
        lay.AddRow(None)
        scroll = ef.Scrollable()
        scroll.Border = System.Enum.Parse(ef.BorderType, "None")
        scroll.ExpandContentWidth = False
        scroll.Content = lay

        def fit(s_=None, e=None):                   # il contenuto segue la larghezza del pannello
            w = int(scroll.Width)
            if w > 60:
                lay.Width = max(230, w - 22)
        scroll.SizeChanged += fit
        self.fit = fit
        self.timer = ef.UITimer()
        self.timer.Interval = 1.0
        self.timer.Elapsed += lambda s_, e: self.guard(self.bambu_poll)
        self.docked = False
        if dock:
            try:
                gp_panel.dock("slicer", scroll, self.on_form_closed)
                self.form, self.docked = scroll, True
            except Exception:
                self.error = traceback.format_exc()
                Rhino.RhinoApp.WriteLine("Generative Print: pannello agganciato non riuscito, apro una finestra\n"
                                         + self.error)
        if not self.docked:
            form = ef.Form()
            form.Title = "Generative Print - slicer"
            form.Resizable = True
            form.Owner = Rhino.UI.RhinoEtoApp.MainWindow
            form.ClientSize = ed.Size(360, 820)
            form.Content = scroll
            form.Closed += lambda s_, e: self.on_form_closed()
            self.form = form
            form.Show()
        self.loading = False
        self.fit()
        self.refresh()

    def on_settings(self):
        s = self.settings
        self.printer = self.printers[max(0, self.w_printer.SelectedIndex)]
        self.material_name = MATERIALS[max(0, self.w_material.SelectedIndex)][0]
        s.update({"bed": bambu.printer_volume(self.printer), "density_g_cm3": dict(MATERIALS)[self.material_name],
                  "layer": float(self.w_h.Value), "line_width": float(self.w_lw.Value),
                  "walls": int(self.w_walls.Value), "infill": float(self.w_infill.Value) / 100.0,
                  "top_layers": int(self.w_top.Value), "bottom_layers": int(self.w_bottom.Value),
                  "overhang_limit_deg": float(self.w_limit.Value)})
        kind = SUPPORT_TYPES[max(0, self.w_sup.SelectedIndex)][0]
        s["supports"] = kind is not None
        if kind is not None:
            s["support_type"] = kind
        s["support_bed_only"] = bool(self.w_bed.Checked)
        self.analyse()

    def on_paint(self):
        self.paint_mode = PAINT_MODES[max(0, self.w_paint.SelectedIndex)][0]
        self.paint_radius = float(self.w_paint_r.Value)
        self.w_paint_l.Text = "Raggio %d mm" % self.paint_radius
        self.paint_cursor = None
        self.mouse.Enabled = self.paint_mode is not None and self.job is not None
        if self.paint_mode in (1, -1) and not self.settings["supports"] and self.w_sup.SelectedIndex == 0:
            self.w_sup.SelectedIndex = 1          # dipingere ha senso solo con i supporti accesi
        if self.job is not None:
            self.job.doc.Views.Redraw()

    def on_view(self):
        self.heat = bool(self.w_heat.Checked)
        self.layer_on = bool(self.w_layers.Checked)
        if self.job is None:
            return
        if self.layer_on:
            self.set_layer(int(self.w_layer.Value))
        else:
            self.refresh()
            self.job.doc.Views.Redraw()

    def on_pick(self):
        doc = Rhino.RhinoDoc.ActiveDoc
        objs = [o for o in doc.Objects.GetSelectedObjects(False, False) if isinstance(o.Geometry, PRINTABLE)]
        if objs:
            self.open(doc, objs)
        elif self.job is None:
            self.open(doc)
        self.refresh()

    def on_export(self):
        import Eto.Forms as ef
        if self.job is None:
            return
        dlg = ef.SaveFileDialog()
        dlg.Title = "Esporta STL (appoggiato al piano)"
        dlg.Filters.Add(ef.FileFilter("STL", ".stl"))
        if dlg.ShowDialog(Rhino.UI.RhinoEtoApp.MainWindow) == ef.DialogResult.Ok:
            path = dlg.FileName if dlg.FileName.lower().endswith(".stl") else dlg.FileName + ".stl"
            mesh_io.write_stl(path, self.job.Vw, self.job.F)
            self.extra.append("Esportato " + path)
            self.refresh()

    def on_sculpt(self):
        import gp_editor
        if self.job is None:
            return
        doc = self.job.doc
        sid, note = self.sculpt_target()
        self.extra = ["Modifica plastica aperta su una copia (%s). L'originale e nascosto: _Show per riaverlo." % note]
        self.refresh()
        ed = gp_editor.get_editor()
        ed.open(doc, doc.Objects.FindId(sid))
        ed.show_panel()

    def on_form_closed(self):
        if self.form is None:
            return
        if self.timer is not None:
            self.timer.Stop()
        self.form = None
        self.close(close_form=False)


def get_slicer():
    sl = sc.sticky.get(STICKY)
    if sl is None:
        sl = Slicer()
        sc.sticky[STICKY] = sl
    return sl


def start(doc=None, panel=True, dock=True):
    """Apre lo slicer sugli oggetti selezionati (o sull'unico oggetto stampabile del documento).
    dock=True: scheda agganciata accanto ai Livelli; False: finestra libera."""
    doc = doc or Rhino.RhinoDoc.ActiveDoc
    sl = get_slicer()
    sl.open(doc)
    if panel:
        sl.show_panel(dock)
    return sl
