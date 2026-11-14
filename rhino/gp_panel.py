"""Pannelli agganciabili di Rhino (come Livelli e Proprieta) creati da script.

Rhino registra un pannello a partire da un tipo .NET con un attributo Guid e da un plug-in
proprietario. Da Python non si possono mettere attributi .NET su una classe: il tipo (un
Eto.Forms.Panel vuoto) si genera quindi a caldo con Reflection.Emit, una volta per sessione di
Rhino, e si registra a nome del plug-in degli script (RhinoCodePlugin). Il contenuto vero
(i controlli Eto) si mette poi dentro l'istanza che Rhino crea.
"""
import clr
import System
import Rhino
import scriptcontext as sc

STICKY = "generative_print_panels"
PANELS = {"slicer": ("6f0b6c1e-3c52-4a56-9d0e-5d1f0a7c2b11", "GP Slicer"),
          "editor": ("6f0b6c1e-3c52-4a56-9d0e-5d1f0a7c2b12", "GP Sculpt")}


def _state():
    st = sc.sticky.get(STICKY)
    if st is None:
        st = {"types": {}, "closed": {}, "hooked": False, "module": None}
        sc.sticky[STICKY] = st
    return st


def _emit(st, key, guid):
    import Eto.Forms as ef
    from System.Reflection import AssemblyName, MethodAttributes, TypeAttributes
    from System.Reflection.Emit import AssemblyBuilder, AssemblyBuilderAccess, CustomAttributeBuilder
    if st["module"] is None:
        ab = AssemblyBuilder.DefineDynamicAssembly(AssemblyName("GenerativePrintPanels"), AssemblyBuilderAccess.Run)
        st["module"] = ab.DefineDynamicModule("panels")
    tb = st["module"].DefineType("GenerativePrint.Panel_" + key, TypeAttributes.Public | TypeAttributes.Class,
                                 clr.GetClrType(ef.Panel))
    ctor = clr.GetClrType(System.Runtime.InteropServices.GuidAttribute).GetConstructor(
        System.Array[System.Type]([clr.GetClrType(System.String)]))
    tb.SetCustomAttribute(CustomAttributeBuilder(ctor, System.Array[System.Object]([guid])))
    tb.DefineDefaultConstructor(MethodAttributes.Public)
    return tb.CreateType()


def _plugin():
    for name in ("RhinoCodePlugin", "IronPython", "Grasshopper"):
        pid = Rhino.PlugIns.PlugIn.IdFromName(name)
        if pid != System.Guid.Empty:
            p = Rhino.PlugIns.PlugIn.Find(pid)
            if p is not None:
                return p
    raise RuntimeError("No plug-in to own the panel")


def _on_closed(sender, e):
    st = _state()
    for key, (guid, _) in PANELS.items():
        if str(e.PanelId).lower() == guid:
            fn = st["closed"].get(key)
            if fn is not None:
                try:
                    fn()
                except Exception:
                    import traceback
                    Rhino.RhinoApp.WriteLine("Generative Print: " + traceback.format_exc())


def dock(key, content, on_closed=None, doc=None):
    """Apre il pannello 'key' come scheda accanto ai Livelli, ci mette 'content' (controllo Eto)
    e restituisce il pannello. on_closed viene chiamata quando l'utente chiude la scheda."""
    st = _state()
    guid, title = PANELS[key]
    gid = System.Guid(guid)
    if key not in st["types"]:
        t = _emit(st, key, guid)
        Rhino.UI.Panels.RegisterPanel(_plugin(), t, title, System.Drawing.SystemIcons.Application,
                                      Rhino.UI.PanelType.System)
        st["types"][key] = t
    if not st["hooked"]:
        Rhino.UI.Panels.Closed += _on_closed
        st["hooked"] = True
    st["closed"][key] = None                      # niente richiami mentre si apre
    P = Rhino.UI.Panels
    if not P.IsPanelVisible(gid) and P.IsPanelVisible(Rhino.UI.PanelIds.Layers):
        bar = P.PanelDockBar(Rhino.UI.PanelIds.Layers)      # stessa colonna dei Livelli, come scheda
        if bar != System.Guid.Empty:
            P.OpenPanel(bar, gid, True)
    if not P.IsPanelVisible(gid, True):
        P.OpenPanel(gid, True)
    doc = doc or Rhino.RhinoDoc.ActiveDoc
    panel = Rhino.UI.Panels.GetPanel(gid, doc)
    if panel is None:
        raise RuntimeError("Rhino did not create the panel " + title)
    panel.Content = content
    st["closed"][key] = on_closed
    return panel


def undock(key):
    """Chiude la scheda senza richiamare on_closed."""
    st = _state()
    st["closed"][key] = None
    gid = System.Guid(PANELS[key][0])
    if key in st["types"] and Rhino.UI.Panels.IsPanelVisible(gid):
        Rhino.UI.Panels.ClosePanel(gid)


def visible(key):
    return key in _state()["types"] and Rhino.UI.Panels.IsPanelVisible(System.Guid(PANELS[key][0]))


def ensure_alias(name, script_path):
    """Alias di Rhino che lancia uno script del progetto. Restituisce True se l'ha creato o aggiornato."""
    macro = '_-RunPythonScript "%s"' % script_path
    al = Rhino.ApplicationSettings.CommandAliasList
    if al.IsAlias(name):
        if al.GetMacro(name) == macro:
            return False
        return bool(al.SetMacro(name, macro))
    return bool(al.Add(name, macro))
