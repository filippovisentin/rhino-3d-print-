# r: numpy
"""Editor plastico per Rhino 8: pennelli di modellazione con verifica di stampa integrata.

Avvio: esegui `rhino/avvia_editor.py` con _ScriptEditor oppure _RunPythonScript (alias GPEditor).
I controlli si aprono come scheda agganciata accanto ai Livelli.

Mouse nella vista:
    trascina col tasto sinistro sulla forma   applica il pennello
    Ctrl + trascina                           inverte il pennello (scava, cancella la maschera)
    Shift + trascina                          liscia
    Ctrl + Shift + trascina                   regola il pennello: orizzontale = raggio, verticale = forza
    tasto destro                              ruota la vista, come sempre in Rhino

La forma modificabile puo essere:
- una PELLE (lampade e vasi generati): si scolpisce la superficie esterna, il solido
  stampabile (spessore, fondo, foro) viene ricostruito a ogni verifica;
- un SOLIDO chiuso qualunque (forma libera o mesh selezionata).

A ogni colpo si aggiorna solo la zona toccata (normali, mappa degli sbalzi), cosi i pennelli
restano fluidi anche su mesh grandi.
"""
import os
import sys
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
from generative_print import checks, generators, mesh_io, sculpt, slicer  # noqa: E402

STICKY = "generative_print_editor"
BRUSH_LABELS = [("draw", "Tira / Spingi"), ("clay", "Argilla"), ("inflate", "Gonfia"), ("layer", "Strato"),
                ("crease", "Piega"), ("smooth", "Liscia"), ("flatten", "Appiattisci"), ("scrape", "Raschia"),
                ("pinch", "Pizzica"), ("grab", "Afferra"), ("mask", "Maschera")]
BRUSH_HELP = {"draw": "Alza la superficie lungo la normale (Ctrl: scava).",
              "clay": "Aggiunge materiale a strisce piatte, riempiendo le conche (Ctrl: toglie).",
              "inflate": "Gonfia: ogni punto si sposta lungo la propria normale.",
              "layer": "Rilievo ad altezza costante: ripassando non cresce oltre.",
              "crease": "Solco netto che stringe la superficie (Ctrl: cresta).",
              "smooth": "Liscia le irregolarita.",
              "flatten": "Porta la zona su un piano.",
              "scrape": "Abbassa solo le creste (Ctrl: riempie solo le valli).",
              "pinch": "Stringe i punti verso il centro del pennello.",
              "grab": "Afferra e trascina la zona.",
              "mask": "Dipinge una protezione: la zona scura non si muove (Ctrl: cancella)."}
FRACTION_BRUSHES = ("smooth", "flatten", "pinch", "scrape")
CLAY = (214, 208, 198)
UNDO_STEPS = 60


def int_array(values):
    """Array di interi .NET da numpy, copiando la memoria in blocco (veloce anche con molte facce)."""
    a = np.ascontiguousarray(values, dtype=np.int32)
    out = System.Array.CreateInstance(System.Int32, len(a))
    if len(a):
        try:
            System.Runtime.InteropServices.Marshal.Copy(System.IntPtr(System.Int64(a.ctypes.data)), out, 0, len(a))
        except Exception:
            return System.Array[System.Int32](a.tolist())
    return out


class Session(object):
    """Stato dell'editor: mesh in numpy (calcolo) e mesh di Rhino (disegno e selezione)."""

    def __init__(self, doc, obj):
        self.doc = doc
        self.obj_id = obj.Id
        meta = gp_rhino.read_meta(obj) or {}
        self.kind = meta.get("kind", "solid")
        self.params = generators.resolve(meta.get("params"))
        self.V, self.F = gp_rhino.from_rhino_mesh(obj.Geometry)
        self.grid = (meta["rings"], meta["segments"]) if self.kind == "skin" else None
        self.base_ring = np.arange(self.grid[1]) if self.grid else None
        self.edges = sculpt.edge_list(self.F)
        self.topo = sculpt.Topology(self.F, len(self.V))
        self.mesh = gp_rhino.to_rhino_mesh(self.V, self.F)
        self.normals = checks.vertex_normals(self.V, self.F)
        self.cross = np.zeros((len(self.F), 3))      # prodotto vettoriale per faccia (normale x 2 area)
        self.ang = np.zeros(len(self.F))
        self.area = np.zeros(len(self.F))
        self.cls = None                              # classe di ogni faccia: 0 ok, 1 vicina, 2 oltre, 3 maschera
        self.mask = np.zeros(len(self.V))            # protezione per vertice, 0..1
        self.face_mask = np.zeros(len(self.F), dtype=bool)
        self.undo_stack, self.redo_stack = [], []
        self.V_ref, self.mask_ref, self.touched = None, None, []
        self.brush = "draw"
        self.radius = 18.0
        self.strength = 0.5          # 0..1 dal cursore del pannello
        self.symmetry = 1
        self.mirror = None           # None, "x" oppure "y": specchio rispetto al centro dell'oggetto
        self.connected = True        # il pennello resta sulla superficie collegata al punto toccato
        c = (self.V.max(axis=0) + self.V.min(axis=0)) / 2.0
        self.centre = (0.0, 0.0) if self.kind == "skin" else (float(c[0]), float(c[1]))
        self.heatmap = True
        self.layer_z = None
        self.layer_lines = []
        self.layer_mesh = None
        self.solid = None            # (V, F) del solido stampabile, ricostruito su richiesta
        self.cursor = None
        self.stroking = False
        self.last_dab = None
        self.grab = None
        self.report = ""
        self.log = []                # pennellate applicate, per il diario
        self.respect = False         # a fine pennellata riporta la zona scolpita sotto il limite
        self.respect_note = ""
        self.dirty = False
        self.limit = float(self.params["print"]["overhang_limit_deg"])

        def mat(rgb, back=None):
            m = Rhino.Display.DisplayMaterial(sd.Color.FromArgb(*rgb))
            m.IsTwoSided = True
            m.BackDiffuse = sd.Color.FromArgb(*(back or rgb))
            return m
        self.material = mat(CLAY, (170, 150, 135))
        self.mat_warn, self.mat_over, self.mat_mask = mat((240, 175, 50)), mat((215, 45, 40)), mat((92, 96, 108))
        self.face_sets = None
        self.max_angle = 0.0
        self.over_area = 0.0
        self._faces(None)
        self.classify()

    # ---------------------------------------------------------------- sincronizzazione
    def _faces(self, faces):
        """Ricalcola normale, area e angolo di sbalzo delle facce indicate (None = tutte)."""
        f = slice(None) if faces is None else faces
        T = self.V[self.F[f]]
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        ln = np.linalg.norm(n, axis=1)
        nz = n[:, 2] / np.maximum(ln, 1e-30)
        if self.kind == "skin":      # parete sottile: conta l'inclinazione in entrambi i versi
            lean = np.abs(nz)
        else:
            lean = np.where(T[:, :, 2].max(axis=1) <= checks.BED_TOL, 0.0, np.clip(-nz, 0.0, 1.0))
        self.cross[f] = n
        self.area[f] = ln / 2.0
        self.ang[f] = np.degrees(np.arcsin(np.clip(lean, 0.0, 1.0)))

    def classify(self, faces=None, force=False):
        """Mappa degli sbalzi: facce ok, vicine alla soglia (ultimi 10 gradi), oltre soglia, piu le
        facce protette dalla maschera. Gli elenchi per il disegno si rifanno solo se qualcosa cambia."""
        over = self.ang > self.limit + checks.ANGLE_TOL
        warn = (self.ang > self.limit - 10.0) & ~over
        self.max_angle = float(self.ang.max()) if len(self.ang) else 0.0
        self.over_area = float(self.area[over].sum())
        cls = np.where(over, 2, np.where(warn, 1, np.where(self.face_mask, 3, 0))).astype(np.int8)
        if not self.heatmap:
            cls = np.where(self.face_mask, 3, 0).astype(np.int8)
        if not force and self.cls is not None:
            same = (cls[faces] == self.cls[faces]).all() if faces is not None else np.array_equal(cls, self.cls)
            if same:
                return
        self.cls = cls
        self.face_sets = tuple(int_array(np.where(cls == k)[0]) for k in (0, 1, 2, 3))

    def push(self, idx=None):
        """Copia i vertici numpy nella mesh di Rhino e aggiorna normali e mappa, solo dove serve."""
        full = idx is None or len(idx) > 0.5 * len(self.V)
        ids = np.arange(len(self.V)) if idx is None else np.asarray(idx)
        set_v = self.mesh.Vertices.SetVertex
        for i, (x, y, z) in zip(ids.tolist(), self.V[ids].tolist()):
            set_v(i, x, y, z)
        self.mesh.Normals.ComputeNormals()
        if full:
            self._faces(None)
            self.normals = checks.vertex_normals(self.V, self.F)
            self.classify()
        else:
            faces = self.topo.faces_of(ids)
            self._faces(faces)
            vs = np.unique(self.F[faces])
            self.normals[vs] = self.topo.vertex_normals(self.cross, vs)
            self.classify(faces)
        self.solid = None
        self.dirty = True
        self.touched.append(ids)

    def set_mask(self, mask):
        self.mask = np.clip(np.asarray(mask, dtype=float), 0.0, 1.0)
        self.face_mask = self.mask[self.F].mean(axis=1) > 0.5
        self.classify()

    def enforce_limit(self):
        """Se l'opzione e attiva, riporta sotto il limite di sbalzo la zona toccata dall'ultima
        pennellata (sculpt.relax_overhang_mesh). Restituisce il risultato o None."""
        self.respect_note = ""
        if not self.respect or not self.touched:
            return None
        region = np.unique(np.concatenate(self.touched))
        skin = self.kind == "skin"
        fixed = (self.V[:, 2] <= checks.BED_TOL) | (self.mask > 0.5)
        if skin:
            fixed = self.mask > 0.5
            fixed[self.base_ring] = True
        res = sculpt.relax_overhang_mesh(self.V, self.F, self.edges, self.limit, region=region,
                                         two_sided=skin, horizontal=skin, fixed=fixed)
        if len(res["moved"]):
            sculpt.constrain(self.V, self.base_ring)
            self.push(res["moved"])
        self.respect_note = ("limite rispettato (%d punti corretti)" % len(res["moved"]) if res["ok"]
                             else "limite NON raggiunto nella zona: %.0f gradi" % res["max_deg"])
        return res

    # ---------------------------------------------------------------- selezione col mouse
    def ray(self, view, point):
        line = view.ActiveViewport.ClientToWorld(point)
        d = line.From - line.To          # dalla camera verso la scena
        d.Unitize()
        return line.To, d

    def hit(self, view, point):
        origin, d = self.ray(view, point)
        t = rg.Intersect.Intersection.MeshRay(self.mesh, rg.Ray3d(origin, d))
        if t < 0:
            return None
        p = origin + d * t
        return np.array([p.X, p.Y, p.Z])

    def nearest(self, point):
        return int(np.argmin(((self.V - np.asarray(point, dtype=float)) ** 2).sum(axis=1)))

    # ---------------------------------------------------------------- annulla e ripeti
    def begin(self):
        """Inizio di una pennellata: da qui si misura cio che cambia (per annulla, strato, afferra)."""
        self.V_ref = self.V.copy()
        self.mask_ref = self.mask.copy()
        self.touched = []

    def end(self):
        """Fine della pennellata: applica il limite di sbalzo se richiesto e registra il passo di annulla."""
        if self.V_ref is None:
            return None
        res = self.enforce_limit()
        rec = {}
        if self.touched:
            idx = np.unique(np.concatenate(self.touched))
            idx = idx[(self.V[idx] != self.V_ref[idx]).any(axis=1)]
            if len(idx):
                rec["v"] = (idx, self.V_ref[idx].copy(), self.V[idx].copy())
        m = np.where(self.mask != self.mask_ref)[0]
        if len(m):
            rec["m"] = (m, self.mask_ref[m].copy(), self.mask[m].copy())
        if rec:
            self.undo_stack.append(rec)
            del self.undo_stack[:-UNDO_STEPS]
            self.redo_stack = []
        self.V_ref, self.mask_ref, self.touched = None, None, []
        return res

    def _restore(self, rec, which):
        if "v" in rec:
            idx = rec["v"][0]
            self.V[idx] = rec["v"][which]
            self.push(idx)
        if "m" in rec:
            self.mask[rec["m"][0]] = rec["m"][which]
            self.set_mask(self.mask)
        self.touched = []

    def undo(self):
        if not self.undo_stack:
            return False
        rec = self.undo_stack.pop()
        self._restore(rec, 1)
        self.redo_stack.append(rec)
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        rec = self.redo_stack.pop()
        self._restore(rec, 2)
        self.undo_stack.append(rec)
        return True

    # ---------------------------------------------------------------- pennelli
    def dab_amount(self, brush, invert):
        if brush in FRACTION_BRUSHES:
            f = max(0.05, self.strength)
            return -f if (invert and brush == "scrape") else f
        if brush == "mask":
            return -1.0 if invert else 1.0
        mm = 0.15 + 2.5 * self.strength * (self.radius / 20.0)
        return -mm if invert else mm

    def region(self, c):
        """Vertici sotto il pennello centrato in c e loro distanza dal centro."""
        if self.connected:
            idx = self.topo.connected(self.V, self.nearest(c), c, self.radius)
        else:
            idx = np.where(((self.V - c) ** 2).sum(axis=1) < self.radius ** 2)[0]
        return idx, np.linalg.norm(self.V[idx] - c, axis=1)

    def apply_dab(self, center, brush=None, invert=False, amount=None):
        brush = brush or self.brush
        amount = self.dab_amount(brush, invert) if amount is None else amount
        mask = self.mask if self.mask.any() else None
        moved = []
        for c, _ in sculpt.symmetry_copies(np.asarray(center, dtype=float), None, self.symmetry,
                                           self.mirror, self.centre):
            if brush == "mask":
                idx, dist = self.region(c)
                w = sculpt.falloff(dist, self.radius) * max(0.25, self.strength) * 3.0
                self.mask[idx] = np.clip(self.mask[idx] + (w if amount > 0 else -w), 0.0, 1.0)
                continue
            seed = self.nearest(c) if self.connected else None
            idx = sculpt.dab(self.V, self.normals, self.edges, c, self.radius, amount, brush,
                             topo=self.topo, seed=seed, mask=mask, base=self.V_ref)
            if len(idx):
                moved.append(idx)
        if brush == "mask":
            self.set_mask(self.mask)
            self.dirty = True
            return
        if not moved:
            return
        idx = np.unique(np.concatenate(moved))
        sculpt.constrain(self.V, self.base_ring)
        self.push(idx)

    def stroke(self, points, brush=None, invert=False, amount=None, record=True):
        """Pennellata lungo una lista di punti 3D (usata dal mouse e dagli script)."""
        if record:
            self.begin()
        self.log.append({"brush": brush or self.brush, "radius": self.radius, "invert": bool(invert),
                         "amount": self.dab_amount(brush or self.brush, invert) if amount is None else amount,
                         "symmetry": self.symmetry,
                         "points": [[round(float(c), 2) for c in p] for p in points]})
        last = None
        step = max(self.radius * 0.25, 0.5)
        for p in points:
            p = np.asarray(p, dtype=float)
            if last is None:
                self.apply_dab(p, brush, invert, amount)
                last = p
                continue
            n = int(np.linalg.norm(p - last) / step)
            for k in range(1, n + 1):
                self.apply_dab(last + (p - last) * (k / float(n)), brush, invert, amount)
            if n:
                last = p
        if record:
            self.end()

    def grab_begin(self, view, point, hit):
        cam = view.ActiveViewport.CameraDirection
        plane = rg.Plane(rg.Point3d(hit[0], hit[1], hit[2]), cam)
        copies = sculpt.symmetry_copies(hit, None, self.symmetry, self.mirror, self.centre)
        sets = []
        for c, _ in copies:
            idx, dist = self.region(c)
            sets.append((idx, (sculpt.falloff(dist, self.radius) * (1.0 - self.mask[idx]))[:, None]))
        self.grab = {"plane": plane, "anchor": hit, "sets": sets,
                     "all": np.unique(np.concatenate([i for i, _ in sets])) if sets else np.zeros(0, dtype=np.int64)}

    def grab_move(self, view, point):
        g = self.grab
        origin, d = self.ray(view, point)
        ok, t = rg.Intersect.Intersection.LinePlane(rg.Line(origin, origin + d * 1.0), g["plane"])
        if not ok or not len(g["all"]):
            return
        p = origin + d * t
        delta = np.array([p.X, p.Y, p.Z]) - g["anchor"]
        self.V[g["all"]] = self.V_ref[g["all"]]
        for (idx, w), (_, dv) in zip(g["sets"], sculpt.symmetry_copies(g["anchor"], delta, self.symmetry,
                                                                        self.mirror, self.centre)):
            self.V[idx] += dv * w
        sculpt.constrain(self.V, self.base_ring)
        self.push(g["all"])

    # ---------------------------------------------------------------- solido e verifiche
    def build_solid(self):
        if self.solid is None:
            if self.kind == "skin":
                skin = self.V.reshape(self.grid[0], self.grid[1], 3)
                self.solid = generators.solid_from_skin(skin, self.params)
            else:
                self.solid = (self.V, self.F)
        return self.solid

    def verify(self, samples=1500):
        try:
            Vs, Fs = self.build_solid()
            rc = gp_rhino.raycast_rhino(gp_rhino.to_rhino_mesh(Vs, Fs))
            skin = self.V.reshape(self.grid[0], self.grid[1], 3) if self.grid else None
            self.result = checks.run_checks(Vs, Fs, self.params, raycast=rc, samples=samples, skin=skin)
            self.report = checks.format_report(self.result)
        except Exception as exc:
            self.result = None
            self.report = "Impossibile costruire il solido: %s" % exc
        return self.report

    def set_layer(self, index):
        """Anteprima a strati: index = None la spegne, altrimenti numero dello strato."""
        if index is None:
            self.layer_z, self.layer_lines, self.layer_mesh = None, [], None
            return ""
        try:
            Vs, Fs = self.build_solid()
        except Exception as exc:
            return "Solido non valido: %s" % exc
        h = float(self.params["print"]["layer"])
        z = (index + 0.5) * h
        seg = slicer.slice_segments(Vs, Fs, z)
        self.layer_lines = [rg.Line(rg.Point3d(a[0], a[1], a[2]), rg.Point3d(b[0], b[1], b[2]))
                            for a, b in seg.tolist()]
        self.layer_mesh = gp_rhino.to_rhino_mesh(Vs, slicer.faces_below(Vs, Fs, z))
        self.layer_z = z
        per = float(np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum()) if len(seg) else 0.0
        return "Strato %d  z = %.2f mm  perimetro %.0f mm" % (index + 1, z, per)

    def layer_count(self):
        return int(np.ceil(self.V[:, 2].max() / float(self.params["print"]["layer"])))

    def export_stl(self, path):
        Vs, Fs = self.build_solid()
        mesh_io.write_stl(path, Vs, Fs)
        return len(Fs)

    def commit(self):
        """Scrive la forma modificata nel documento (e il solido, se e una pelle)."""
        self.doc.Objects.Replace(self.obj_id, self.mesh.DuplicateMesh())
        if self.kind == "skin":
            try:
                Vs, Fs = self.build_solid()
                gp_rhino.remove_named(self.doc, "gp_solido")
                gp_rhino.add_mesh(self.doc, gp_rhino.to_rhino_mesh(Vs, Fs), "gp_solido", layer="GP::solido")
            except Exception:
                pass
        self.dirty = False


# -------------------------------------------------------------------- disegno e mouse
class Conduit(Rhino.Display.DisplayConduit):
    def __init__(self, editor):
        super().__init__()
        self.ed = editor

    def CalculateBoundingBox(self, e):
        s = self.ed.session
        if s is not None:
            e.IncludeBoundingBox(s.mesh.GetBoundingBox(False))

    def PostDrawObjects(self, e):
        s = self.ed.session
        if s is None:
            return
        try:
            bed = s.params["print"]["bed"]
            rect = rg.Rectangle3d(rg.Plane.WorldXY, rg.Interval(-bed[0] / 2.0, bed[0] / 2.0),
                                  rg.Interval(-bed[1] / 2.0, bed[1] / 2.0))
            e.Display.DrawPolyline(rect.ToPolyline(), sd.Color.FromArgb(90, 110, 150), 2)
            if s.layer_z is not None and s.layer_mesh is not None:
                e.Display.DrawMeshShaded(s.layer_mesh, s.material)
                if s.layer_lines:
                    e.Display.DrawLines(s.layer_lines, sd.Color.FromArgb(230, 90, 20), 3)
            elif s.face_sets is not None:
                for faces, mat in zip(s.face_sets, (s.material, s.mat_warn, s.mat_over, s.mat_mask)):
                    if faces.Length:
                        e.Display.DrawMeshShaded(s.mesh, mat, faces)
            else:
                e.Display.DrawMeshShaded(s.mesh, s.material)
            if s.cursor is not None and s.layer_z is None:
                p, n = s.cursor
                plane = rg.Plane(rg.Point3d(p[0], p[1], p[2]), rg.Vector3d(n[0], n[1], n[2]))
                blue = sd.Color.FromArgb(30, 120, 230)
                e.Display.DrawCircle(rg.Circle(plane, s.radius), blue, 2)
                e.Display.DrawCircle(rg.Circle(plane, max(0.3, s.radius * s.strength)), blue, 1)
        except Exception:
            self.ed.fail()


class Mouse(Rhino.UI.MouseCallback):
    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.adjust = None
        self.brush = "draw"
        self.invert = False

    def _ok(self, e):
        s = self.ed.session
        return s is not None and e.View is not None and s.layer_z is None

    def OnMouseDown(self, e):
        try:
            if not self._ok(e) or e.MouseButton != Rhino.UI.MouseButton.Left:
                return
            s = self.ed.session
            if e.CtrlKeyDown and e.ShiftKeyDown:           # regola raggio e forza trascinando
                self.adjust = (e.ViewportPoint.X, e.ViewportPoint.Y, s.radius, s.strength)
                e.Cancel = True
                return
            hit = s.hit(e.View, e.ViewportPoint)
            if hit is None:
                return
            e.Cancel = True
            s.stroking = True
            s.begin()
            self.brush = "smooth" if e.ShiftKeyDown else s.brush
            self.invert = bool(e.CtrlKeyDown)
            if self.brush == "grab":
                s.grab_begin(e.View, e.ViewportPoint, hit)
            else:
                s.apply_dab(hit, self.brush, self.invert)
                s.last_dab = hit
            e.View.Redraw()
        except Exception:
            self.ed.fail()

    def OnMouseMove(self, e):
        try:
            if not self._ok(e):
                return
            s = self.ed.session
            if self.adjust is not None:
                x0, y0, r0, f0 = self.adjust
                s.radius = float(np.clip(r0 + (e.ViewportPoint.X - x0) * 0.2, 2.0, 80.0))
                s.strength = float(np.clip(f0 - (e.ViewportPoint.Y - y0) * 0.004, 0.01, 1.0))
                e.View.Redraw()
                return
            if s.stroking and self.brush == "grab":
                s.grab_move(e.View, e.ViewportPoint)
                e.View.Redraw()
                return
            hit = s.hit(e.View, e.ViewportPoint)
            if hit is None:
                if s.cursor is not None:
                    s.cursor = None
                    e.View.Redraw()
                return
            s.cursor = (hit, s.normals[s.nearest(hit)])
            if s.stroking:
                if np.linalg.norm(hit - s.last_dab) >= max(s.radius * 0.25, 0.5):
                    s.stroke([s.last_dab, hit], self.brush, self.invert, record=False)
                    s.last_dab = hit
            e.View.Redraw()
        except Exception:
            self.ed.fail()

    def OnMouseUp(self, e):
        try:
            s = self.ed.session
            if self.adjust is not None:
                self.adjust = None
                e.Cancel = True
                self.ed.sync_widgets()
                return
            if s is not None and s.stroking:
                s.stroking = False
                s.grab = None
                e.Cancel = True
                s.end()
                self.ed.refresh_status()
                e.View.Redraw()
        except Exception:
            self.ed.fail()


# -------------------------------------------------------------------- pannello
class Editor(object):
    def __init__(self):
        self.session = None
        self.conduit = Conduit(self)
        self.mouse = Mouse(self)
        self.form = None
        self.docked = False
        self.loading = False
        self.error = None
        self.hidden = []

    def fail(self):
        """Un errore in un evento non deve bloccare Rhino: si spegne tutto e si registra."""
        self.error = traceback.format_exc()
        self.mouse.Enabled = False
        self.conduit.Enabled = False
        Rhino.RhinoApp.WriteLine("Generative Print: errore nell'editor\n" + self.error)

    # ----------------------------------------------------------- sessione
    def find_target(self, doc):
        settings = Rhino.DocObjects.ObjectEnumeratorSettings()
        settings.HiddenObjects = True
        objs = [o for o in doc.Objects.GetObjectList(settings)
                if isinstance(o.Geometry, rg.Mesh)]
        for o in objs:
            if o.IsSelected(False) > 0:
                return o
        for name in ("gp_pelle", "gp_blob"):
            for o in objs:
                if o.Attributes.Name == name:
                    return o
        return objs[0] if objs else None

    def open(self, doc, obj=None):
        self.close(commit=False, close_form=False)
        gp_rhino.setup_doc(doc)
        obj = obj or self.find_target(doc)
        if obj is None:
            obj = doc.Objects.FindId(new_blob(doc))
        self.session = Session(doc, obj)
        self.hidden = []
        settings = Rhino.DocObjects.ObjectEnumeratorSettings()
        for o in doc.Objects.GetObjectList(settings):
            if o.Id == obj.Id or (o.Attributes.Name or "").startswith("gp_"):
                self.hidden.append(o.Id)
        for oid in self.hidden + [obj.Id]:
            doc.Objects.Hide(oid, True)
        self.conduit.Enabled = True
        self.mouse.Enabled = True
        doc.Views.Redraw()
        self.sync_form()
        self.refresh_status()
        return self.session

    def close(self, commit=True, close_form=True):
        s = self.session
        self.mouse.Enabled = False
        self.conduit.Enabled = False
        if s is not None:
            if commit and s.dirty:
                s.commit()
            for oid in self.hidden:
                s.doc.Objects.Show(oid, True)
            if s.kind == "skin":
                s.doc.Objects.Hide(s.obj_id, True)     # resta visibile il solido
            else:
                s.doc.Objects.Show(s.obj_id, True)
            s.doc.Views.Redraw()
        self.session = None
        if close_form and self.form is not None:
            form, self.form = self.form, None
            if self.docked:
                gp_panel.undock("editor")
            else:
                form.Close()

    # ----------------------------------------------------------- interfaccia Eto
    def show_panel(self, dock=True):
        """Apre i controlli: scheda agganciata accanto ai Livelli (dock=True) oppure finestra."""
        import Eto.Forms as ef
        import Eto.Drawing as ed
        if self.form is not None:
            if self.docked:
                gp_panel.dock("editor", self.form, self.on_form_closed)
            else:
                self.form.BringToFront()
            return
        self.loading = True

        def label(text, wrap=False):
            lb = ef.Label()
            lb.Text = text
            if wrap:
                lb.Wrap = ef.WrapMode.Word
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

        def check(text, value, handler):
            w = ef.CheckBox()
            w.Text = text
            w.Checked = value
            w.CheckedChanged += lambda s, e: self.guard(handler)
            return w

        def slider(lo, hi, value, handler):
            w = ef.Slider()
            w.MinValue, w.MaxValue, w.Value = lo, hi, value
            w.ValueChanged += lambda s, e: self.guard(handler)
            return w

        def table(rows):
            lay = ef.DynamicLayout()
            lay.Spacing = ed.Size(6, 5)
            for row in rows:
                lay.AddRow(*row)
            return lay

        s = self.session
        self.w_status = label("", True)
        self.w_brush = ef.DropDown()
        for _, text in BRUSH_LABELS:
            self.w_brush.Items.Add(text)
        self.w_brush.SelectedIndex = 0
        self.w_brush.SelectedIndexChanged += lambda s_, e: self.guard(self.on_settings)
        self.w_help = label("", True)
        self.w_radius = slider(2, 80, 18, self.on_settings)
        self.w_strength = slider(1, 100, 50, self.on_settings)
        self.w_radius_l, self.w_strength_l = ef.Label(), ef.Label()
        self.w_sym = ef.NumericStepper()
        self.w_sym.MinValue, self.w_sym.MaxValue, self.w_sym.Value = 1, 64, 1
        self.w_sym.ValueChanged += lambda s_, e: self.guard(self.on_settings)
        self.w_mirror = ef.DropDown()
        for text in ("Nessuno", "X (sinistra / destra)", "Y (davanti / dietro)"):
            self.w_mirror.Items.Add(text)
        self.w_mirror.SelectedIndex = 0
        self.w_mirror.SelectedIndexChanged += lambda s_, e: self.guard(self.on_settings)
        self.w_connected = check("Solo la superficie toccata", s.connected if s else True, self.on_settings)
        self.w_connected.ToolTip = "Il pennello non passa sull'altro lato di una parete sottile"
        self.w_respect = check("Rispetta il limite di sbalzo", bool(s.respect) if s else False, self.on_settings)
        self.w_respect.ToolTip = "A fine pennellata la zona toccata viene riportata sotto il limite"
        self.w_heat = check("Mappa degli sbalzi", True, self.on_heat)
        self.w_heat.ToolTip = "Arancio = vicino alla soglia, rosso = oltre"
        self.w_layers = check("Strati", False, self.on_layers)
        self.w_layer = slider(0, 100, 0, self.on_layers)
        self.w_layer.Enabled = False
        self.w_layer_info = label("", True)
        self.w_report = ef.TextArea()
        self.w_report.ReadOnly = True
        self.w_report.Font = ed.Font("Consolas", 8.5)
        self.w_report.Wrap = True
        self.w_report.Height = 220

        lay = ef.DynamicLayout()
        lay.Padding = ed.Padding(8)
        lay.Spacing = ed.Size(6, 7)
        lay.AddRow(self.w_status)
        lay.AddRow(title("Pennello"))
        lay.AddRow(self.w_brush)
        lay.AddRow(self.w_help)
        lay.AddRow(table([(self.w_radius_l, self.w_radius), (self.w_strength_l, self.w_strength)]))
        lay.AddRow(table([(button("Annulla", self.on_undo, "Annulla l'ultima pennellata"),
                           button("Ripeti", self.on_redo, "Rifa la pennellata annullata"))]))
        lay.AddRow(title("Simmetria"))
        lay.AddRow(table([(label("Copie radiali"), self.w_sym), (label("Specchio"), self.w_mirror)]))
        lay.AddRow(title("Maschera"))
        lay.AddRow(table([(button("Cancella", self.on_mask_clear, "Toglie tutta la protezione"),
                           button("Inverti", self.on_mask_invert, "Scambia zona protetta e zona libera"))]))
        lay.AddRow(title("Opzioni"))
        lay.AddRow(self.w_connected)
        lay.AddRow(self.w_respect)
        lay.AddRow(self.w_heat)
        lay.AddRow(table([(self.w_layers, self.w_layer)]))
        lay.AddRow(self.w_layer_info)
        lay.AddRow(title("Azioni"))
        lay.AddRow(table([(button("Verifica stampa", self.on_verify), button("Esporta STL", self.on_export)),
                          (button("Nuova forma libera", self.on_new_blob),
                           button("Nuova lampada", self.on_new_vessel, "Nuova lampada o vaso da scolpire")),
                          (button("Applica e chiudi", self.on_apply, "Scrive la forma nel documento"), None)]))
        lay.AddRow(self.w_report)
        lay.AddRow(label("Sinistro: pennello. Ctrl: inverte. Shift: liscia. Ctrl+Shift e trascina: raggio "
                         "(orizzontale) e forza (verticale). Destro: ruota la vista.", True))
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
        self.docked = False
        if dock:
            try:
                gp_panel.dock("editor", scroll, self.on_form_closed)
                self.form, self.docked = scroll, True
            except Exception:
                self.error = traceback.format_exc()
                Rhino.RhinoApp.WriteLine("Generative Print: pannello agganciato non riuscito, apro una finestra\n"
                                         + self.error)
        if not self.docked:
            form = ef.Form()
            form.Title = "Generative Print - editor plastico"
            form.Resizable = True
            form.Owner = Rhino.UI.RhinoEtoApp.MainWindow
            form.ClientSize = ed.Size(360, 820)
            form.Content = scroll
            form.Closed += lambda s_, e: self.on_form_closed()
            self.form = form
            form.Show()
        self.loading = False
        fit()
        self.sync_form()
        self.refresh_status()

    def guard(self, fn):
        if self.loading:
            return
        try:
            fn()
        except Exception:
            self.error = traceback.format_exc()
            if self.form is not None:
                self.w_report.Text = self.error

    def sync_form(self):
        if self.form is None or self.session is None:
            return
        self.on_settings()
        self.w_layer.MaxValue = max(1, self.session.layer_count() - 1)

    def sync_widgets(self):
        """Porta nei cursori raggio e forza cambiati col mouse."""
        s = self.session
        if self.form is None or s is None:
            return
        self.loading = True
        self.w_radius.Value = int(round(s.radius))
        self.w_strength.Value = int(round(100 * s.strength))
        self.loading = False
        self.w_radius_l.Text = "Raggio %d mm" % round(s.radius)
        self.w_strength_l.Text = "Forza %d %%" % round(100 * s.strength)

    def refresh_status(self):
        if self.form is None or self.session is None:
            return
        s = self.session
        kind = "Pelle di lampada/vaso" if s.kind == "skin" else "Solido"
        self.w_status.Text = ("%s, %d vertici. Sbalzo max %.0f gradi, oltre soglia %.0f mm2. Annulla: %d%s"
                              % (kind, len(s.V), s.max_angle, s.over_area, len(s.undo_stack),
                                 (". " + s.respect_note) if s.respect_note else ""))

    def on_settings(self):
        s = self.session
        if s is None:
            return
        s.brush = BRUSH_LABELS[max(0, self.w_brush.SelectedIndex)][0]
        s.radius = float(self.w_radius.Value)
        s.strength = self.w_strength.Value / 100.0
        s.symmetry = int(self.w_sym.Value)
        s.respect = bool(self.w_respect.Checked)
        s.connected = bool(self.w_connected.Checked)
        s.mirror = (None, "x", "y")[max(0, self.w_mirror.SelectedIndex)]
        self.w_help.Text = BRUSH_HELP[s.brush]
        self.w_radius_l.Text = "Raggio %d mm" % self.w_radius.Value
        self.w_strength_l.Text = "Forza %d %%" % self.w_strength.Value

    def on_heat(self):
        s = self.session
        s.heatmap = bool(self.w_heat.Checked)
        s.classify(force=True)
        s.doc.Views.Redraw()

    def on_layers(self):
        s = self.session
        on = bool(self.w_layers.Checked)
        self.w_layer.Enabled = on
        if on:
            self.w_layer.MaxValue = max(1, s.layer_count() - 1)
        self.w_layer_info.Text = s.set_layer(int(self.w_layer.Value) if on else None)
        s.doc.Views.Redraw()

    def on_undo(self):
        self.session.undo()
        self.session.doc.Views.Redraw()
        self.refresh_status()

    def on_redo(self):
        self.session.redo()
        self.session.doc.Views.Redraw()
        self.refresh_status()

    def _mask(self, new):
        s = self.session
        s.begin()
        s.set_mask(new)
        s.end()
        s.doc.Views.Redraw()
        self.refresh_status()

    def on_mask_clear(self):
        self._mask(np.zeros(len(self.session.V)))

    def on_mask_invert(self):
        self._mask(1.0 - self.session.mask)

    def on_verify(self):
        self.w_report.Text = "Verifica in corso..."
        self.w_report.Text = self.session.verify()

    def on_export(self):
        import Eto.Forms as ef
        dlg = ef.SaveFileDialog()
        dlg.Title = "Esporta STL"
        dlg.Filters.Add(ef.FileFilter("STL", ".stl"))
        if dlg.ShowDialog(Rhino.UI.RhinoEtoApp.MainWindow) == ef.DialogResult.Ok:
            path = dlg.FileName if dlg.FileName.lower().endswith(".stl") else dlg.FileName + ".stl"
            n = self.session.export_stl(path)
            self.w_report.Text = self.session.verify() + "\n\nEsportato %s (%d triangoli)" % (path, n)

    def on_new_blob(self):
        doc = self.session.doc if self.session else Rhino.RhinoDoc.ActiveDoc
        self.close(commit=True, close_form=False)
        gp_rhino.remove_named(doc, "gp_")
        self.open(doc, doc.Objects.FindId(new_blob(doc)))

    def on_new_vessel(self):
        doc = self.session.doc if self.session else Rhino.RhinoDoc.ActiveDoc
        self.close(commit=True, close_form=False)
        self.open(doc, doc.Objects.FindId(new_vessel(doc)))

    def on_apply(self):
        self.close(commit=True, close_form=True)

    def on_form_closed(self):
        if self.form is not None:
            self.form = None
            self.close(commit=True, close_form=False)


# -------------------------------------------------------------------- forme di partenza
def new_blob(doc, radius=40.0, height=90.0, subdivisions=5):
    params = {"object": "free_form", "blob": {"radius": radius, "height": height, "subdivisions": subdivisions}}
    V, F = generators.build_blob(params)
    meta = {"kind": "solid", "params": params}
    return gp_rhino.add_mesh(doc, gp_rhino.to_rhino_mesh(V, F), "gp_blob", layer="GP::forma", meta=meta)


def new_vessel(doc, params=None):
    params = params or {"ribs": {"count": 0, "amplitude": 0.0, "twist_deg": 0.0}, "mesh": {"segments": 180}}
    skin, V, F = generators.build(params)
    gp_rhino.remove_named(doc, "gp_")
    nz, nt = skin.shape[:2]
    meta = {"kind": "skin", "rings": int(nz), "segments": int(nt), "params": params}
    sid = gp_rhino.add_mesh(doc, gp_rhino.to_rhino_mesh(skin.reshape(-1, 3), generators.skin_faces(nz, nt)),
                            "gp_pelle", layer="GP::pelle", color=(120, 160, 210), meta=meta)
    gp_rhino.add_mesh(doc, gp_rhino.to_rhino_mesh(V, F), "gp_solido", layer="GP::solido")
    return sid


def get_editor():
    ed = sc.sticky.get(STICKY)
    if ed is None:
        ed = Editor()
        sc.sticky[STICKY] = ed
    return ed


def start(doc=None, panel=True, dock=True):
    """Apre l'editor sulla mesh selezionata, sulla pelle generata o su una forma nuova."""
    doc = doc or Rhino.RhinoDoc.ActiveDoc
    ed = get_editor()
    session = ed.open(doc)
    if panel:
        ed.show_panel(dock)
    return ed, session
