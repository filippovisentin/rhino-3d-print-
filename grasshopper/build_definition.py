# r: numpy
"""Costruisce grasshopper/generative_print.gh da zero (eseguire dentro Rhino 8 con Grasshopper aperto).

Serve a rigenerare la definizione in modo riproducibile: la definizione salvata e il
risultato di questo script, non un file modificato a mano.
"""
import os

import System
import System.Drawing as sd
import Grasshopper
import Grasshopper.Kernel.Special as ghs
from Grasshopper.Kernel import GH_ParameterSide

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None


def build(repo, save=True):
    here = os.path.join(repo, "grasshopper")
    canvas = Grasshopper.Instances.ActiveCanvas
    doc = Grasshopper.Kernel.GH_Document()
    Grasshopper.Instances.DocumentServer.AddDocument(doc)
    canvas.Document = doc
    server = Grasshopper.Instances.ComponentServer

    def place(obj, x, y):
        obj.CreateAttributes()
        obj.Attributes.Pivot = sd.PointF(x, y)
        doc.AddObject(obj, False)
        return obj

    def by_name(name):
        proxy = server.FindObjectByName(name, True, True)
        return proxy.CreateInstance()

    # ---------------------------------------------------------------- componente principale
    comp = by_name("Python 3 Script")
    place(comp, 640, 420)
    with open(os.path.join(here, "gp_component_stub.py"), encoding="utf-8") as fh:
        comp.SetSource(fh.read())
    comp.NickName = "Generative Print"
    comp.UsingStandardOutputParam = False
    for side, plist in ((GH_ParameterSide.Input, comp.Params.Input), (GH_ParameterSide.Output, comp.Params.Output)):
        for prm in list(plist):
            comp.Params.UnregisterParameter(prm)

    sliders = [  # nickname, etichetta, min, valore, max, decimali
        ("altezza", "Altezza (mm)", 100, 220, 300, 0),
        ("r0", "Raggio base (mm)", 20, 54, 120, 1),
        ("r1", "Raggio 1/4 (mm)", 20, 84.5, 120, 1),
        ("r2", "Raggio 2/4 (mm)", 20, 81.5, 120, 1),
        ("r3", "Raggio 3/4 (mm)", 20, 64, 120, 1),
        ("r4", "Raggio bordo (mm)", 20, 44, 120, 1),
        ("nervature", "Nervature (n)", 0, 16, 48, -1),
        ("ampiezza", "Ampiezza nervature (frazione)", 0, 0.05, 0.15, 3),
        ("torsione", "Torsione (gradi)", -360, 110, 360, 0),
        ("forma", "Forma 0 sin 1 viva 2 lobi 3 scan.", 0, 0, 3, -1),
        ("sfuma_basso", "Sfumatura in basso (frazione)", 0, 0.22, 0.5, 2),
        ("sfuma_alto", "Sfumatura in alto (frazione)", 0, 0.10, 0.5, 2),
        ("parete", "Parete (mm)", 0.8, 1.35, 3.0, 2),
        ("fondo", "Fondo (mm, 0 = aperto)", 0, 3, 6, 1),
        ("foro", "Foro portalampada (mm, 0 = vaso)", 0, 40.5, 60, 1),
        ("lati", "Sezione: lati (0 = cerchio)", 0, 0, 12, -1),
        ("stile", "Sezione: 0 lobi, 1 poligono", 0, 1, 1, -1),
        ("rientro", "Lobi: rientro dei lati", 0, 0.0, 0.4, 3),
        ("raccordo", "Poligono: raggio raccordo", 0, 0.12, 1.0, 2),
        ("rot_sezione", "Sezione: rotazione (gradi)", -180, 0, 180, 0),
        ("capienza_min", "Vaso: capienza minima (ml)", 0, 0, 3000, 0),
        ("linea", "Larghezza linea (mm)", 0.4, 0.45, 1.0, 2),
        ("diametro", "Diametro obiettivo (mm, 0 = libero)", 0, 180, 250, 0),
        ("peso_max", "Peso massimo (g)", 50, 250, 600, 0),
        ("limite", "Sbalzo limite (gradi)", 30, 45, 70, 0),
        ("risoluzione", "Lato maglia (mm)", 1.0, 2.5, 5.0, 1),
        ("strato", "Anteprima strato (0 = spenta)", 0, 0, 1500, -1),
    ]
    toggles = [("verifica", "Verifica completa", False, "toggle"),
               ("spirale", "Vaso in modalita spirale", False, "toggle"), ("ricarica", "Ricarica codice", False, "toggle"),
               ("esporta", "Esporta STL", False, "button"), ("editor", "Apri nell'editor plastico", False, "button")]
    texts = [("file", os.path.join(repo, "export", "lampada_gh")), ("cartella", None)]

    names = [s[0] for s in sliders] + [t[0] for t in toggles] + [t[0] for t in texts]
    for k, nick in enumerate(names):
        prm = comp.CreateParameter(GH_ParameterSide.Input, k)
        prm.NickName = nick
        prm.Name = nick
        prm.Optional = True
        comp.Params.RegisterInputParam(prm, k)
    outs = ["pelle", "solido", "oltre", "vicino", "strato_linee", "report", "ok", "peso", "sbalzo",
            "diametro_mis", "params"]
    for k, nick in enumerate(outs):
        prm = comp.CreateParameter(GH_ParameterSide.Output, k)
        prm.NickName = nick
        prm.Name = nick
        comp.Params.RegisterOutputParam(prm, k)
    comp.VariableParameterMaintenance()
    comp.Params.OnParametersChanged()
    comp.Hidden = True                         # la geometria si vede tramite le anteprime colorate

    def inp(nick):
        return [p for p in comp.Params.Input if p.NickName == nick][0]

    def outp(nick):
        return [p for p in comp.Params.Output if p.NickName == nick][0]

    y = 40
    for nick, label, lo, val, hi, dec in sliders:
        s = ghs.GH_NumberSlider()
        s.CreateAttributes()
        s.NickName = label
        if dec < 0:
            s.Slider.Type = Grasshopper.GUI.Base.GH_SliderAccuracy.Integer
        else:
            s.Slider.Type = Grasshopper.GUI.Base.GH_SliderAccuracy.Float
            s.Slider.DecimalPlaces = dec
        s.Slider.Minimum = System.Decimal(lo)
        s.Slider.Maximum = System.Decimal(hi)
        s.Slider.Value = System.Decimal(val)
        s.Attributes.Bounds = sd.RectangleF(0, 0, 330, 20)
        place(s, 40, y)
        inp(nick).AddSource(s)
        y += 26
    y += 12
    for nick, label, val, kind in toggles:
        obj = ghs.GH_BooleanToggle() if kind == "toggle" else ghs.GH_ButtonObject()
        obj.NickName = label
        if kind == "toggle":
            obj.Value = val
        place(obj, 200, y)
        inp(nick).AddSource(obj)
        y += 30
    for nick, val in texts:
        if val is None:
            continue
        pan = ghs.GH_Panel()
        pan.NickName = "File di esportazione (senza estensione)"
        pan.UserText = val
        place(pan, 120, y + 10)
        pan.Attributes.Bounds = sd.RectangleF(pan.Attributes.Bounds.X, pan.Attributes.Bounds.Y, 300, 40)
        inp(nick).AddSource(pan)

    # ---------------------------------------------------------------- uscite
    report = ghs.GH_Panel()
    report.NickName = "Verifica di stampa"
    place(report, 900, 185)
    report.Attributes.Bounds = sd.RectangleF(900, 185, 520, 300)
    report.AddSource(outp("report"))

    def preview(src, color, x, y, label):
        cp = by_name("Custom Preview")
        cp.NickName = label
        place(cp, x, y)
        sw = ghs.GH_ColourSwatch()
        sw.SwatchColour = sd.Color.FromArgb(*color)
        place(sw, x - 150, y + 20)
        cp.Params.Input[0].AddSource(src)
        cp.Params.Input[1].AddSource(sw)
        return cp

    preview(outp("solido"), (214, 208, 198), 1100, 520, "Solido")
    preview(outp("vicino"), (240, 175, 50), 1100, 650, "Vicino")
    preview(outp("oltre"), (215, 45, 40), 1100, 780, "Oltre")
    preview(outp("strato_linee"), (230, 90, 20), 1100, 910, "Strato")

    note = ghs.GH_Panel()
    note.NickName = "Istruzioni"
    note.UserText = ("GENERATIVE PRINT - lampade e vasi stampabili\n"
                     "Muovi gli slider: sbalzo, peso e diametro si aggiornano subito.\n"
                     "Arancio = vicino alla soglia di sbalzo, rosso = oltre (servirebbero supporti).\n"
                     "'Verifica completa' aggiunge spessori e portalampada (piu lento).\n"
                     "'Apri nell'editor plastico' porta la forma ai pennelli.\n"
                     "Foro 0 = vaso: sezione poligonale, capienza, modalita spirale.\n"
                     "Valori iniziali = iterazione 5 della demo della lampada.")
    place(note, 900, 30)
    note.Attributes.Bounds = sd.RectangleF(900, 30, 520, 140)

    doc.NewSolution(False)
    if save:
        path = os.path.join(here, "generative_print.gh")
        io = Grasshopper.Kernel.GH_DocumentIO(doc)
        io.SaveQuiet(path)
        doc.FilePath = path
        return comp, path
    return comp, None
