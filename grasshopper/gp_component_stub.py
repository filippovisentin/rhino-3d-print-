"""Generative Print: lampade e vasi stampabili (componente Python 3 di Rhino 8).

Il calcolo sta in rhino/gp_gh.py e nel pacchetto generative_print della stessa cartella
di progetto. Se il file .gh viene spostato, collega all'ingresso 'cartella' il percorso
della cartella del progetto (quella che contiene README.md).
"""
# r: numpy
import os
import sys
import importlib

_repo = str(cartella).strip().strip('"') if cartella else ""
if not _repo:
    _gh = ghenv.Component.OnPingDocument()
    _f = _gh.FilePath if _gh is not None else ""
    _repo = os.path.dirname(os.path.dirname(_f)) if _f else ""
for _p in (_repo, os.path.join(_repo, "rhino")):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import gp_gh
except ImportError:
    raise Exception("Cartella del progetto non trovata: collega 'cartella' (percorso che contiene README.md)")
if ricarica:
    for _name in sorted(n for n in list(sys.modules) if n.startswith("generative_print") or n.startswith("gp_")):
        importlib.reload(sys.modules[_name])

_res = gp_gh.solve(dict(
    altezza=altezza, r0=r0, r1=r1, r2=r2, r3=r3, r4=r4, nervature=nervature, ampiezza=ampiezza,
    torsione=torsione, forma=forma, sfuma_basso=sfuma_basso, sfuma_alto=sfuma_alto, parete=parete,
    fondo=fondo, foro=foro, lati=lati, stile=stile, rientro=rientro, raccordo=raccordo, rot_sezione=rot_sezione, spirale=spirale, linea=linea,
    capienza_min=capienza_min, diametro=diametro, peso_max=peso_max, limite=limite, risoluzione=risoluzione,
    verifica=verifica, strato=strato, esporta=esporta, file=file, editor=editor))

pelle, solido, oltre, vicino = _res["pelle"], _res["solido"], _res["oltre"], _res["vicino"]
strato_linee, report, ok = _res["strato"], _res["report"], _res["ok"]
peso, sbalzo, diametro_mis, params = _res["peso"], _res["sbalzo"], _res["diametro_mis"], _res["params"]
