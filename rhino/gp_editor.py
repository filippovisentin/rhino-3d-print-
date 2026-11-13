# r: numpy
"""Editor plastico per Rhino 8: pennelli di modellazione con verifica di stampa integrata.

Avvio: esegui `rhino/avvia_editor.py` con _ScriptEditor oppure _RunPythonScript.

Mouse nella vista:
    trascina col tasto sinistro sulla forma   applica il pennello
    Ctrl + trascina                           inverte il pennello (scava)
    Shift + trascina                          liscia
    tasto destro                              ruota la vista, come sempre in Rhino

La forma modificabile puo essere:
- una PELLE (lampade e vasi generati): si scolpisce la superficie esterna, il solido
  stampabile (spessore, fondo, foro) viene ricostruito a ogni verifica;
- un SOLIDO chiuso qualunque (forma libera o mesh selezionata).
"""
import json
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

import gp_rhino  # noqa: E402
from generative_print import checks, generators, mesh_io, sculpt, slicer  # noqa: E402

STICKY = "generative_print_editor"
BRUSH_LABELS = [("draw", "Tira / Spingi"), ("inflate", "Gonfia"), ("smooth", "Liscia"),
                ("flatten", "Appiattisci"), ("pinch", "Pizzica"), ("grab", "Afferra")]
CLAY = (214, 208, 198)


def int_array(values):
    return System.Array[System.Int32](values.tolist())


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
        self.mesh = gp_rhino.to_rhino_mesh(self.V, self.F)
        self.normals = checks.vertex_normals(self.V, self.F)
        self.undo_stack = []
        self.brush = "draw"
        self.radius = 18.0
        self.strength = 0.5          # 0..1 dal cursore del pannello
        self.symmetry = 1
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
        self.dirty = False
        self.limit = float(self.params["print"]["overhang_limit_deg"])
        self.material = Rhino.Display.DisplayMaterial(sd.Color.FromArgb(*CLAY))
        self.material.IsTwoSided = True
        self.material.BackDiffuse = sd.Color.FromArgb(170, 150, 135)
        self.mat_warn = Rhino.Display.DisplayMaterial(sd.Color.FromArgb(240, 175, 50))
        self.mat_over = Rhino.Display.DisplayMaterial(sd.Color.FromArgb(215, 45, 40))
        for m in (self.mat_warn, self.mat_over):
            m.IsTwoSided = True
            m.BackDiffuse = m.Diffuse
        self.face_sets = None
        self.max_angle = 0.0
        self.over_area = 0.0
        self.classify()

    # ---------------------------------------------------------------- sincronizzazione
    def classify(self):
        """Divide le facce in tre gruppi per la mappa degli sbalzi: ok, vicine alla
        soglia (ultimi 10 gradi), oltre soglia. Stessa regola della verifica."""
        n, areas = checks.face_normals_areas(self.V, self.F)
        if self.kind == "skin":      # parete sottile: conta l'inclinazione in entrambi i versi
            lean = np.abs(n[:, 2])
        else:
            lean = np.clip(-n[:, 2], 0.0, 1.0)
            on_bed = self.V[:, 2][self.F].max(axis=1) <= checks.BED_TOL
            lean = np.where(on_bed, 0.0, lean)
        ang = np.degrees(np.arcsin(np.clip(lean, 0.0, 1.0)))
        over = ang > self.limit + checks.ANGLE_TOL
        warn = (ang > self.limit - 10.0) & ~over
        self.max_angle = float(ang.max()) if len(ang) else 0.0
        self.over_area = float(areas[over].sum())
        self.face_sets = (int_array(np.where(~over & ~warn)[0]), int_array(np.where(warn)[0]),
                          int_array(np.where(over)[0]))

    def push(self, idx=None):
        """Copia i vertici numpy nella mesh di Rhino e aggiorna normali e colori."""
        ids = np.arange(len(self.V)) if idx is None else idx
        set_v = self.mesh.Vertices.SetVertex
        for i, (x, y, z) in zip(ids.tolist(), self.V[ids].tolist()):
            set_v(i, x, y, z)
        self.mesh.Normals.ComputeNormals()
        self.normals = checks.vertex_normals(self.V, self.F)
        if self.heatmap:
            self.classify()
        self.solid = None
        self.dirty = True

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

    # ---------------------------------------------------------------- pennelli
    def begin(self):
        self.undo_stack.append(self.V.copy())
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)

    def undo(self):
        if self.undo_stack:
            self.V = self.undo_stack.pop()
            self.push(None)

    def dab_amount(self, brush, invert):
        if brush in ("smooth", "flatten", "pinch"):
            return max(0.05, self.strength)
        mm = 0.15 + 2.5 * self.strength * (self.radius / 20.0)
        return -mm if invert else mm

    def apply_dab(self, center, brush=None, invert=False, amount=None):
        brush = brush or self.brush
        amount = self.dab_amount(brush, invert) if amount is None else amount
        moved = []
        for c, _ in sculpt.radial_copies(np.asarray(center, dtype=float), None, self.symmetry):
            idx = sculpt.dab(self.V, self.normals, self.edges, c, self.radius, amount, brush)
            if len(idx):
                moved.append(idx)
        if not moved:
            return
        idx = np.unique(np.concatenate(moved))
        sculpt.constrain(self.V, self.base_ring)
        self.push(idx)

    def stroke(self, points, brush=None, invert=False, amount=None, record=True):
        """Pennellata lungo una lista di punti 3D (usata dal mouse e dagli script)."""
        if record:
            self.begin()
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

    def grab_begin(self, view, point, hit):
        cam = view.ActiveViewport.CameraDirection
        plane = rg.Plane(rg.Point3d(hit[0], hit[1], hit[2]), cam)
        copies = sculpt.radial_copies(hit, None, self.symmetry)
        sets = []
        for c, _ in copies:
            d = np.linalg.norm(self.V - c, axis=1)
            idx = np.where(d < self.radius)[0]
            sets.append((idx, sculpt.falloff(d[idx], self.radius)[:, None]))
        self.grab = {"plane": plane, "anchor": hit, "V0": self.V.copy(), "sets": sets}

    def grab_move(self, view, point):
        g = self.grab
        origin, d = self.ray(view, point)
        ok, t = rg.Intersect.Intersection.LinePlane(rg.Line(origin, origin + d * 1.0), g["plane"])
        if not ok:
            return
        p = origin + d * t
        delta = np.array([p.X, p.Y, p.Z]) - g["anchor"]
        self.V[:] = g["V0"]
        moved = []
        for (idx, w), (_, dv) in zip(g["sets"], sculpt.radial_copies(g["anchor"], delta, self.symmetry)):
            self.V[idx] += dv * w
            moved.append(idx)
        sculpt.constrain(self.V, self.base_ring)
        self.push(np.unique(np.concatenate(moved)) if moved else None)

    # ---------------------------------------------------------------- solido e verifiche
    def build_solid(self):
        if self.solid is None:
            if self.kind == "skin":
                skin = self.V.reshape(self.grid[0], self.grid[1], 3)
                self.solid = generators.shell_from_params(skin, self.params)
            else:
                self.solid = (self.V, self.F)
        return self.solid

    def verify(self, samples=1500):
        try:
            Vs, Fs = self.build_solid()
            rc = gp_rhino.raycast_rhino(gp_rhino.to_rhino_mesh(Vs, Fs))
            self.result = checks.run_checks(Vs, Fs, self.params, raycast=rc, samples=samples)
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
            elif s.heatmap and s.face_sets is not None:
                for faces, mat in zip(s.face_sets, (s.material, s.mat_warn, s.mat_over)):
                    if faces.Length:
                        e.Display.DrawMeshShaded(s.mesh, mat, faces)
            else:
                e.Display.DrawMeshShaded(s.mesh, s.material)
            if s.cursor is not None and s.layer_z is None:
                p, n = s.cursor
                circle = rg.Circle(rg.Plane(rg.Point3d(p[0], p[1], p[2]), rg.Vector3d(n[0], n[1], n[2])),
                                   s.radius)
                e.Display.DrawCircle(circle, sd.Color.FromArgb(30, 120, 230), 2)
        except Exception:
            self.ed.fail()


class Mouse(Rhino.UI.MouseCallback):
    def __init__(self, editor):
        super().__init__()
        self.ed = editor

    def _ok(self, e):
        s = self.ed.session
        return s is not None and e.View is not None and s.layer_z is None

    def OnMouseDown(self, e):
        try:
            if not self._ok(e) or e.MouseButton != Rhino.UI.MouseButton.Left:
                return
            s = self.ed.session
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
            near = int(np.argmin(np.linalg.norm(s.V - hit, axis=1)))
            s.cursor = (hit, s.normals[near])
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
            if s is not None and s.stroking:
                s.stroking = False
                s.grab = None
                e.Cancel = True
                self.ed.refresh_status()
        except Exception:
            self.ed.fail()


# -------------------------------------------------------------------- pannello
class Editor(object):
    def __init__(self):
        self.session = None
        self.conduit = Conduit(self)
        self.mouse = Mouse(self)
        self.form = None
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
            form.Close()

    # ----------------------------------------------------------- interfaccia Eto
    def show_panel(self):
        import Eto.Forms as ef
        import Eto.Drawing as ed
        if self.form is not None:
            self.form.BringToFront()
            return
        form = ef.Form()
        form.Title = "Generative Print - editor plastico"
        form.Padding = ed.Padding(10)
        form.Resizable = True
        form.Owner = Rhino.UI.RhinoEtoApp.MainWindow

        self.w_status = ef.Label()
        self.w_brush = ef.DropDown()
        for _, label in BRUSH_LABELS:
            self.w_brush.Items.Add(label)
        self.w_brush.SelectedIndex = 0
        self.w_radius = ef.Slider()
        self.w_radius.MinValue, self.w_radius.MaxValue, self.w_radius.Value = 2, 80, 18
        self.w_strength = ef.Slider()
        self.w_strength.MinValue, self.w_strength.MaxValue, self.w_strength.Value = 1, 100, 50
        self.w_sym = ef.NumericStepper()
        self.w_sym.MinValue, self.w_sym.MaxValue, self.w_sym.Value = 1, 64, 1
        self.w_heat = ef.CheckBox()
        self.w_heat.Text = "Mappa degli sbalzi (rosso = oltre soglia)"
        self.w_heat.Checked = True
        self.w_layers = ef.CheckBox()
        self.w_layers.Text = "Anteprima a strati"
        self.w_layer = ef.Slider()
        self.w_layer.MinValue, self.w_layer.MaxValue, self.w_layer.Value = 0, 100, 0
        self.w_layer.Enabled = False
        self.w_layer_info = ef.Label()
        self.w_report = ef.TextArea()
        self.w_report.ReadOnly = True
        self.w_report.Font = ed.Font("Consolas", 8.5)
        self.w_report.Wrap = False
        self.w_report.Size = ed.Size(700, 260)
        for w in (self.w_radius, self.w_strength, self.w_layer):
            w.Width = 380
        self.w_radius_l, self.w_strength_l = ef.Label(), ef.Label()

        def label(text):
            lb = ef.Label()
            lb.Text = text
            return lb

        def button(text, handler):
            b = ef.Button()
            b.Text = text
            b.Click += lambda s, e: self.guard(handler)
            return b

        self.w_brush.SelectedIndexChanged += lambda s, e: self.guard(self.on_settings)
        self.w_radius.ValueChanged += lambda s, e: self.guard(self.on_settings)
        self.w_strength.ValueChanged += lambda s, e: self.guard(self.on_settings)
        self.w_sym.ValueChanged += lambda s, e: self.guard(self.on_settings)
        self.w_heat.CheckedChanged += lambda s, e: self.guard(self.on_heat)
        self.w_layers.CheckedChanged += lambda s, e: self.guard(self.on_layers)
        self.w_layer.ValueChanged += lambda s, e: self.guard(self.on_layers)

        lay = ef.DynamicLayout()
        lay.Spacing = ed.Size(6, 6)
        lay.AddRow(self.w_status)
        lay.AddRow(label("Pennello"), self.w_brush)
        lay.AddRow(self.w_radius_l, self.w_radius)
        lay.AddRow(self.w_strength_l, self.w_strength)
        lay.AddRow(label("Simmetria radiale (copie)"), self.w_sym)
        lay.AddRow(self.w_heat)
        lay.AddRow(self.w_layers, self.w_layer)
        lay.AddRow(self.w_layer_info)
        row = ef.DynamicLayout()
        row.Spacing = ed.Size(6, 6)
        row.AddRow(button("Annulla pennellata", self.on_undo), button("Verifica stampa", self.on_verify),
                   button("Esporta STL", self.on_export))
        row.AddRow(button("Nuova forma libera", self.on_new_blob), button("Nuova lampada / vaso", self.on_new_vessel),
                   button("Applica e chiudi", self.on_apply))
        lay.AddRow(row)
        lay.AddRow(self.w_report)
        lay.AddRow(label("Sinistro: pennello   Ctrl: scava   Shift: liscia   Destro: ruota la vista"))
        form.Content = lay
        form.Closed += lambda s, e: self.on_form_closed()
        self.form = form
        self.sync_form()
        self.refresh_status()
        form.Show()

    def guard(self, fn):
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

    def refresh_status(self):
        if self.form is None or self.session is None:
            return
        s = self.session
        s.classify()
        kind = "pelle di lampada/vaso" if s.kind == "skin" else "solido chiuso"
        self.w_status.Text = ("%s, %d vertici | sbalzo max %.0f gradi | oltre soglia: %.0f mm2"
                              % (kind, len(s.V), s.max_angle, s.over_area))

    def on_settings(self):
        s = self.session
        if s is None:
            return
        s.brush = BRUSH_LABELS[max(0, self.w_brush.SelectedIndex)][0]
        s.radius = float(self.w_radius.Value)
        s.strength = self.w_strength.Value / 100.0
        s.symmetry = int(self.w_sym.Value)
        self.w_radius_l.Text = "Raggio %d mm" % self.w_radius.Value
        self.w_strength_l.Text = "Forza %d %%" % self.w_strength.Value

    def on_heat(self):
        s = self.session
        s.heatmap = bool(self.w_heat.Checked)
        if s.heatmap:
            s.classify()
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

    def on_verify(self):
        self.w_report.Text = "Verifica in corso..."
        self.w_report.Text = self.session.verify()

    def on_export(self):
        import Eto.Forms as ef
        dlg = ef.SaveFileDialog()
        dlg.Title = "Esporta STL"
        dlg.Filters.Add(ef.FileFilter("STL", ".stl"))
        if dlg.ShowDialog(self.form) == ef.DialogResult.Ok:
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
    skin, V, F = generators.build_vessel(params)
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


def start(doc=None, panel=True):
    """Apre l'editor sulla mesh selezionata, sulla pelle generata o su una forma nuova."""
    doc = doc or Rhino.RhinoDoc.ActiveDoc
    ed = get_editor()
    session = ed.open(doc)
    if panel:
        ed.show_panel()
    return ed, session
