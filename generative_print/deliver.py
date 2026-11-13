"""Consegna: copia l'iterazione scelta in runs/<corsa>/final con STL, 3MF, parametri e scheda.

Uso:  python -m generative_print.deliver runs/<corsa> 8 nome-file
Tutti i numeri della scheda vengono da metrics.json dell'iterazione (rimisurata qui).
"""
import argparse
import json
import os
import shutil

from . import mesh_io, verify


def it(x):
    if isinstance(x, float):
        return ("%.2f" % x).rstrip("0").rstrip(".").replace(".", ",")
    if isinstance(x, list):
        return " x ".join(it(v) for v in x)
    return str(x)


def recipe(p, m):
    pr = p["print"]
    if p.get("object") == "vase" and pr.get("mode") == "spiral":
        return [
            "PLA, ugello %s mm, strato %s mm, **modalità vaso / spirale** (Bambu e Orca: \"Spiral vase\"; "
            "PrusaSlicer: \"Spiral vase\"), larghezza della linea esterna %s mm, %d strati pieni di fondo, "
            "nessun supporto." % (it(pr["nozzle"]), it(pr["layer"]), it(pr["line_width"]),
                                  int(pr.get("bottom_layers", 4))),
            "Orientamento: fondo sul piano, così come esce il file. Il file è un solido pieno: è lo slicer a "
            "ricavarne la parete singola. Non svuotarlo prima.",
            "Perché: a ogni quota c'è un solo contorno (verificato ogni mm), condizione necessaria per la spirale; "
            "sbalzo massimo %s gradi." % it(round(m["overhang"]["max_deg"], 1)),
            "La parete singola non è a tenuta d'acqua garantita: per fiori freschi serve un inserto o un "
            "rivestimento interno."]
    walls = max(2, int(round(float(p["wall"]["thickness"]) / float(pr["line_width"]))))
    return ["PLA, ugello %s mm, strato %s mm, %d perimetri (la parete da %s mm è fatta di soli perimetri), "
            "nessun supporto." % (it(pr["nozzle"]), it(pr["layer"]), walls, it(p["wall"]["thickness"])),
            "Orientamento: fondo sul piano, così come esce il file.",
            "Perché: nessuna faccia supera i %s gradi di sbalzo (massimo misurato %s gradi)."
            % (it(pr["overhang_limit_deg"]), it(round(m["overhang"]["max_deg"], 1)))]


def deliver(run_dir, n, name):
    src = os.path.join(run_dir, "iter_%02d" % n)
    dst = os.path.join(run_dir, "final")
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(src, "params.json"), encoding="utf-8") as fh:
        params = json.load(fh)
    from . import generators
    p = generators.resolve(params)
    m = verify.verify_file(os.path.join(src, "model.stl"), params)
    shutil.copy(os.path.join(src, "model.stl"), os.path.join(dst, name + ".stl"))
    if os.path.exists(os.path.join(src, "skin.npy")):
        shutil.copy(os.path.join(src, "skin.npy"), os.path.join(dst, "skin.npy"))
    V, F = mesh_io.read_stl(os.path.join(src, "model.stl"))
    mesh_io.write_3mf(os.path.join(dst, name + ".3mf"), V, F, name)
    with open(os.path.join(dst, "params.json"), "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in params.items() if k != "sculpt"}, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(run_dir, "brief.json"), encoding="utf-8") as fh:
        brief = json.load(fh)

    L = ["# %s: scheda" % brief.get("title", name), "",
         "Oggetto finale della corsa `%s` (iterazione %d). Unità: mm, g, gradi." % (os.path.basename(run_dir), n), "",
         "> %s" % brief["brief"], "",
         "## File", "- `%s.stl` (%d triangoli) e `%s.3mf`" % (name, m["triangles"], name),
         "- `params.json`: parametri del generatore", ""]
    L += ["## Parametri", "| Parametro | Valore |", "|---|---|",
          "| Altezza | %s mm |" % it(p["envelope"]["height"]),
          "| Raggi del profilo (dal basso) | %s mm |" % ", ".join(it(float(x)) for x in p["profile"]["radii"])]
    sec = p["section"]
    if int(sec.get("sides", 0)) >= 2:
        shape = ("poligono a lati diritti, raccordo %s" % it(sec.get("corner", 0))
                 if sec.get("style") == "polygon" else "lobi morbidi, rientro %s" % it(sec.get("depth", 0)))
        L.append("| Sezione | %d lati, %s, rotazione %s gradi |" % (int(sec["sides"]), shape, it(float(sec["twist_deg"]))))
    if int(p["ribs"]["count"]) > 0:
        L.append("| Nervature | %d, ampiezza %s%%, torsione %s gradi |"
                 % (p["ribs"]["count"], it(p["ribs"]["amplitude"] * 100), it(float(p["ribs"]["twist_deg"]))))
    if p["print"].get("mode") == "spiral":
        L.append("| Parete | una linea da %s mm (modalità spirale) |" % it(p["print"]["line_width"]))
    else:
        L.append("| Parete / fondo | %s mm / %s mm |" % (it(p["wall"]["thickness"]), it(p["floor"]["thickness"])))
    L += ["", "## Misure sull'STL (`python -m generative_print.verify`)",
          "| Controllo | Misura | Limite | Esito |", "|---|---|---|---|"]
    for c in m["checks"]:
        L.append("| %s | %s %s | %s | %s |" % (c["label"], it(c["value"]), c["unit"], c["limit"],
                                              "ok" if c["pass"] else "NO"))
    mat = m.get("material", {})
    if mat:
        L += ["", "Peso stimato %s g, circa %s m di filo da 1,75 mm, %d strati da %s mm. Tempo di stampa circa %s h "
              "(stima grossolana)." % (it(round(mat["mass_est_g"], 1)), it(round(mat["filament_m"], 1)), m["layers"],
                                       it(p["print"]["layer"]), it(round(mat["time_h_rough"], 1)))]
    L += ["", "## Stampa consigliata", ""]
    for line in recipe(p, m):
        L += [line, ""]
    L += ["", "## Non verificato",
          "L'oggetto non è stato stampato né aperto in uno slicer. La densità del PLA (%s g/cm3) è un valore "
          "ipotizzato." % it(p["print"]["density_g_cm3"])]
    with open(os.path.join(dst, "scheda.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return dst, m


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prepara la cartella final di una corsa")
    ap.add_argument("run_dir")
    ap.add_argument("iteration", type=int)
    ap.add_argument("name")
    a = ap.parse_args(argv)
    dst, m = deliver(a.run_dir, a.iteration, a.name)
    print(dst, "STAMPABILE" if m["pass"] else "DA CORREGGERE")


if __name__ == "__main__":
    main()
