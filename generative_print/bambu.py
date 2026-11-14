"""Confronto con uno slicer vero: affetta un STL con Bambu Studio (riga di comando) e legge il G-code.

Uso:  python -m generative_print.bambu runs/<corsa>/final/<nome>.stl runs/<corsa>/final/params.json
      [--printer "Bambu Lab X1 Carbon 0.4 nozzle"] [--out cartella] [--support [--support-angle 45]]

Bambu Studio e open source (AGPL, derivato da PrusaSlicer); qui lo si lancia solo come programma
esterno, senza includerne codice. I profili di sistema hanno l'ereditarieta ("inherits"): vengono
appiattiti in tre JSON (macchina, processo, filamento) con le impostazioni della nostra ricetta sopra,
e passati alla CLI come preset utente che eredita dal profilo di sistema.

Dal G-code (senza archi, solo segmenti G1) si ricavano:
- peso, metri di filo, tempo e strati calcolati dallo slicer;
- metri estrusi per tipo di tratto (muro esterno, interno, gap infill, supporto...);
- lo sbalzo visto dallo slicer: di quanto il muro esterno di ogni strato sporge rispetto a quello
  dello strato sotto (angolo = atan(sporgenza / altezza strato));
- in modalita spirale, la continuita: spostamenti a vuoto, ritrazioni e discese di z nella parete.

Nota: Bambu ricentra il pezzo sul suo piatto e lo ruota leggermente (circa 2,5 gradi misurati sulla
lampada), quindi le analisi usano solo il G-code e non confrontano punto per punto con il modello.
"""
import argparse
import glob
import json
import math
import os
import re
import subprocess
import zipfile

import numpy as np

EXE = r"C:\Program Files\Bambu Studio\bambu-studio.exe"
PROFILES = r"C:\Program Files\Bambu Studio\resources\profiles\BBL"
PRINTERS = {
    "Bambu Lab X1 Carbon 0.4 nozzle": ("0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C"),
    "Bambu Lab P1S 0.4 nozzle": ("0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C"),
    "Bambu Lab A1 mini 0.4 nozzle": ("0.20mm Standard @BBL A1M", "Bambu PLA Basic @BBL A1M"),
    "Bambu Lab A1 0.4 nozzle": ("0.20mm Standard @BBL A1", "Bambu PLA Basic @BBL A1"),
}
# codici di uscita della CLI osservati
EXIT_CODES = {0: "ok", -17: "processo non compatibile con la stampante",
              -50: "il pezzo non entra nel volume di stampa",
              -61: "materiale non ammesso sul tipo di piatto (visto con PETG sul Cool Plate)"}


def available():
    return os.path.exists(EXE) and os.path.isdir(PROFILES)


FILAMENTS = {"PLA": "Bambu PLA Basic", "PETG": "Bambu PETG Basic", "ABS": "Bambu ABS", "ASA": "Bambu ASA",
             "TPU": "Bambu TPU 95A"}


def filament_profile(printer, material="PLA"):
    """Nome del profilo di filamento Bambu per quel materiale su quella stampante; se non esiste
    (per esempio PETG sulla A1 mini) torna il PLA della stampante."""
    pla = PRINTERS[printer][1]
    suffix = pla[pla.index("@"):]
    name = "%s %s" % (FILAMENTS.get(material, FILAMENTS["PLA"]), suffix)
    if name != pla and not os.path.exists(os.path.join(PROFILES, "filament", name + ".json")):
        return pla
    return name


FALLBACK_VOLUMES = {"Bambu Lab X1 Carbon 0.4 nozzle": [256.0, 256.0, 250.0],
                    "Bambu Lab P1S 0.4 nozzle": [256.0, 256.0, 250.0],
                    "Bambu Lab A1 mini 0.4 nozzle": [180.0, 180.0, 180.0],
                    "Bambu Lab A1 0.4 nozzle": [256.0, 256.0, 256.0]}


def printer_volume(printer):
    """Volume di stampa [x, y, z] in mm, letto dal profilo Bambu se installato."""
    try:
        m = flatten("machine", printer)
        pts = [tuple(float(c) for c in q.split("x")) for q in m["printable_area"]]
        return [max(q[0] for q in pts) - min(q[0] for q in pts), max(q[1] for q in pts) - min(q[1] for q in pts),
                float(m["printable_height"])]
    except Exception:
        return list(FALLBACK_VOLUMES.get(printer, [256.0, 256.0, 256.0]))


def default_printer():
    """Stampante preselezionata in Bambu Studio, se e tra quelle note; altrimenti la prima."""
    try:
        conf = os.path.join(os.environ.get("APPDATA", ""), "BambuStudio", "BambuStudio.conf")
        with open(conf, encoding="utf-8", errors="replace") as fh:
            m = re.search(r'"machine"\s*:\s*"([^"]+)"', fh.read())
        if m and m.group(1) in PRINTERS:
            return m.group(1)
    except OSError:
        pass
    return next(iter(PRINTERS))


def _index(kind):
    idx = {}
    for f in glob.glob(os.path.join(PROFILES, kind, "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (ValueError, OSError):
            continue
        if isinstance(d, dict) and "name" in d:
            idx[d["name"]] = d
    return idx


def flatten(kind, name):
    """Profilo completo: segue la catena 'inherits' fino alla radice."""
    idx = _index(kind)
    chain = []
    while name:
        d = idx[name]
        chain.append(d)
        name = d.get("inherits", "")
    out = {}
    for d in reversed(chain):
        out.update(d)
    # preset utente completo che eredita dal profilo di sistema: la CLI lo accetta
    # (senza 'inherits' risponde -17, processo non compatibile)
    out["inherits"] = chain[0]["name"]
    out["from"] = "User"
    if kind != "machine":
        out["name"] = "generative_print " + kind
    return out


def _s(x):
    return ("%g" % x) if isinstance(x, float) else str(x)


def support_overrides(kind="grid", bed_only=False):
    """Impostazioni di Bambu per i nostri tre tipi di supporto."""
    tree = kind == "tree"
    return {"enable_support": "1", "support_type": "tree(auto)" if tree else "normal(auto)",
            "support_style": {"grid": "grid", "snug": "snug", "tree": "organic"}[kind],
            "support_on_build_plate_only": "1" if bed_only else "0"}


def overrides(params):
    """Impostazioni di processo ricavate dai nostri parametri (stessa ricetta della scheda)."""
    pr = params["print"]
    lw = float(pr.get("line_width", 0.45))
    h = float(pr.get("layer", 0.2))
    o = {"layer_height": _s(h), "initial_layer_print_height": _s(h),
         "enable_support": "0",
         "enable_arc_fitting": "0",          # solo segmenti G1: il G-code resta analizzabile
         "curr_bed_type": "Textured PEI Plate",   # ammette PLA, PETG, ABS, ASA e TPU (il Cool Plate no)
         "sparse_infill_density": "0%",
         "outer_wall_line_width": _s(lw), "inner_wall_line_width": _s(lw)}
    if pr.get("generic"):
        # solido qualsiasi dallo slicer interno: pareti, riempimento e strati pieni cosi come scelti
        o.update({"spiral_mode": "0", "wall_loops": str(int(pr.get("walls", 2))),
                  "sparse_infill_density": "%d%%" % int(round(100 * float(pr.get("infill", 0.15)))),
                  "top_shell_layers": str(int(pr.get("top_layers", 5))),
                  "bottom_shell_layers": str(int(pr.get("bottom_layers", 4))), "wall_generator": "arachne"})
    elif params.get("object") == "vase" and pr.get("mode") == "spiral":
        o.update({"spiral_mode": "1", "wall_loops": "1", "top_shell_layers": "0",
                  "bottom_shell_layers": str(int(pr.get("bottom_layers", 4)))})
    else:
        walls = max(2, int(round(float(params["wall"]["thickness"]) / lw)))
        floor_layers = max(3, int(round(float(params["floor"]["thickness"]) / h)))
        # Arachne: linee a larghezza variabile. Una parete inclinata tagliata in orizzontale e piu
        # larga del suo spessore (t / cos dell'inclinazione); con le linee fisse ('classic')
        # lo slicer riempie la differenza con molto gap infill.
        o.update({"spiral_mode": "0", "wall_loops": str(walls), "top_shell_layers": "0",
                  "bottom_shell_layers": str(floor_layers), "wall_generator": "arachne"})
    return o


def prepare(out_dir, printer, params, extra=None, material="PLA"):
    proc_name = PRINTERS[printer][0]
    fil_name = filament_profile(printer, material)
    m = flatten("machine", printer)
    p = flatten("process", proc_name)
    f = flatten("filament", fil_name)
    p.update(overrides(params))
    p.update(extra or {})
    paths = []
    for tag, d in (("macchina", m), ("processo", p), ("filamento", f)):
        path = os.path.join(out_dir, tag + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
        paths.append(path)
    return paths


def slice_stl(stl, params, out_dir, printer="Bambu Lab X1 Carbon 0.4 nozzle", extra=None, timeout=900,
              analyse=True, material="PLA"):
    """Affetta e restituisce il riassunto; il G-code resta in out_dir/plate_1.gcode."""
    from . import mesh_io
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    mach, proc, fil = prepare(out_dir, printer, params, extra, material)
    out3mf = os.path.join(out_dir, "affettato.3mf")
    if os.path.exists(out3mf):
        os.remove(out3mf)
    # pezzo al centro del piatto e appoggiato a z = 0 (Bambu lo ricentra comunque)
    V, F = mesh_io.read_stl(stl)
    with open(mach, encoding="utf-8") as fh:
        area = json.load(fh).get("printable_area", ["0x0", "256x256"])
    pts = [tuple(float(c) for c in q.split("x")) for q in area]
    centre = [(min(q[0] for q in pts) + max(q[0] for q in pts)) / 2.0,
              (min(q[1] for q in pts) + max(q[1] for q in pts)) / 2.0]
    offset = [centre[0] - (V[:, 0].max() + V[:, 0].min()) / 2.0,
              centre[1] - (V[:, 1].max() + V[:, 1].min()) / 2.0, -float(V[:, 2].min())]
    placed = os.path.join(out_dir, "modello_sul_piatto.stl")
    mesh_io.write_stl(placed, V + offset, F)
    cmd = [EXE, "--slice", "0",
           "--load-settings", mach + ";" + proc, "--load-filaments", fil,
           "--outputdir", out_dir, "--export-3mf", "affettato.3mf", placed]
    # cwd = out_dir: la CLI scrive result.json nella cartella corrente
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=out_dir)
    with open(os.path.join(out_dir, "bambu_log.txt"), "w", encoding="utf-8") as fh:
        fh.write("CMD " + " ".join(cmd) + "\nEXIT %d\n" % r.returncode + (r.stdout or "") + (r.stderr or ""))
    code = r.returncode - (1 << 32) if r.returncode >= (1 << 31) else r.returncode
    res = {"printer": printer, "filament": filament_profile(printer, material), "exit_code": code, "exit_text": EXIT_CODES.get(code, "vedi bambu_log.txt"),
           "ok": code == 0 and os.path.exists(out3mf), "settings": dict(overrides(params), **(extra or {}))}
    if not res["ok"]:
        return res
    with zipfile.ZipFile(out3mf) as z:
        names = [n for n in z.namelist() if n.endswith(".gcode")]
        gcode = z.read(names[0]).decode("utf-8", "replace") if names else ""
        info = z.read("Metadata/slice_info.config").decode("utf-8", "replace") \
            if "Metadata/slice_info.config" in z.namelist() else ""
    with open(os.path.join(out_dir, "plate_1.gcode"), "w", encoding="utf-8") as fh:
        fh.write(gcode)
    res.update(parse_gcode(gcode))
    res["slice_info"] = parse_slice_info(info)
    if analyse:
        h = float(params["print"].get("layer", 0.2))
        res["wall_overhang"] = wall_overhang(gcode, h, float(params["print"].get("overhang_limit_deg", 45.0)))
        if res.get("spiral_mode"):
            # dalla seconda spira: la prima comincia con lo spostamento che arriva dal fondo pieno
            start = h * (int(params["print"].get("bottom_layers", 4)) + 2)
            res["spiral"] = spiral_continuity(gcode, start + 1e-3, float(V[:, 2].max() - V[:, 2].min()) - h)
    return res


def parse_slice_info(xml):
    out = {}
    for k in ("prediction", "weight"):
        m = re.search(r'key="%s" value="([^"]+)"' % k, xml)
        if m:
            out[k] = float(m.group(1))
    out["warnings"] = re.findall(r'<warning msg="([^"]+)"[^>]*error_code="([^"]*)"', xml)
    return out


_NUM = r"(-?\d*\.?\d+)"


def moves(g):
    """Movimenti lineari del G-code: (tipo di tratto, strato, z, x0, y0, x1, y1, estrusione).
    Lo strato si conta dai marcatori di cambio strato: in spirale z cresce a ogni tratto."""
    feat, x, y, z, layer = None, None, None, 0.0, 0
    for line in g.splitlines():
        if line.startswith("; FEATURE:"):
            feat = line.split(":", 1)[1].strip()
            continue
        if line.startswith("; CHANGE_LAYER") or line.startswith(";LAYER_CHANGE"):
            layer += 1
            continue
        if not (line.startswith("G1 ") or line.startswith("G0 ")):
            continue
        code = line.split(";", 1)[0]
        mz = re.search(r"Z" + _NUM, code)
        mx = re.search(r"X" + _NUM, code)
        my = re.search(r"Y" + _NUM, code)
        me = re.search(r"E" + _NUM, code)
        if mz:
            z = float(mz.group(1))
        px, py = x, y
        if mx:
            x = float(mx.group(1))
        if my:
            y = float(my.group(1))
        if (mx or my) and px is not None:
            yield feat, layer, z, px, py, x, y, float(me.group(1)) if me else 0.0


def parse_gcode(g):
    """Statistiche dal G-code Bambu: peso, filo, tempo, strati e metri estrusi per tipo di tratto."""
    res = {}
    for key, pat, f in (("weight_g", r";\s*total filament weight \[g\]\s*:\s*([\d.]+)", float),
                        ("filament_m", r";\s*total filament length \[mm\]\s*:\s*([\d.]+)", lambda s: float(s) / 1000),
                        ("time_text", r";\s*model printing time:\s*([^;\n]+)", lambda s: s.strip()),
                        ("layers", r";\s*total layer number:\s*(\d+)", int),
                        ("density_g_cm3", r"^; filament_density = ([\d.]+)", float),
                        ("spiral_mode", r"^; spiral_mode = (\d)", lambda s: s == "1"),
                        ("wall_generator", r"^; wall_generator = (\w+)", str),
                        ("arcs", r"^; enable_arc_fitting = (\d)", lambda s: s == "1")):
        m = re.search(pat, g, re.M)
        if m:
            res[key] = f(m.group(1))
    m = re.search(r";\s*model printing time:\s*(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?", g)
    if m:
        d, hh, mm, ss = (int(v) if v else 0 for v in m.groups())
        res["time_h"] = round(d * 24 + hh + mm / 60.0 + ss / 3600.0, 3)
    length = {}
    for feat, _, z, x0, y0, x1, y1, e in moves(g):
        if e > 0 and feat:
            length[feat] = length.get(feat, 0.0) + math.hypot(x1 - x0, y1 - y0)
    res["feature_m"] = {k: round(v / 1000.0, 2) for k, v in sorted(length.items())}
    res["support_m"] = round(sum(v for k, v in length.items() if "upport" in k) / 1000.0, 2)
    res["gap_infill_m"] = round(length.get("Gap infill", 0.0) / 1000.0, 2)
    return res


def _layer_segments(g, feature="Outer wall"):
    """Segmenti estrusi di un tipo di tratto per strato: {z dello strato: (A, B)}.
    La quota dello strato e la z massima dei suoi tratti (in spirale z sale lungo lo strato)."""
    out, ztop = {}, {}
    for feat, layer, z, x0, y0, x1, y1, e in moves(g):
        if e > 0 and feat == feature:
            out.setdefault(layer, []).append((x0, y0, x1, y1))
            ztop[layer] = max(ztop.get(layer, z), z)
    res = {}
    for layer, v in out.items():
        a = np.array(v, dtype=float)
        res[round(ztop[layer], 3)] = (a[:, :2], a[:, 2:])
    return res


def _densify(A, B, step):
    pts = []
    for a, b in zip(A, B):
        n = max(1, int(math.ceil(math.hypot(*(b - a)) / step)))
        t = np.arange(n) / float(n)
        pts.append(a + t[:, None] * (b - a))
    return np.vstack(pts)


def _dist_to_segments(Q, A, B, chunk=128):
    """Distanza minima di ogni punto di Q dai segmenti AB (esatta, non a campioni)."""
    AB = B - A
    L2 = np.maximum((AB ** 2).sum(1), 1e-12)
    d = np.empty(len(Q))
    for s in range(0, len(Q), chunk):
        q = Q[s:s + chunk, None, :]
        t = np.clip(((q - A[None]) * AB[None]).sum(-1) / L2[None], 0.0, 1.0)
        p = A[None] + t[..., None] * AB[None]
        d[s:s + chunk] = np.sqrt(((q - p) ** 2).sum(-1)).min(1)
    return d


def _inside_even_odd(Q, A, B, chunk=256):
    """Punto dentro la regione racchiusa dai percorsi (regola pari/dispari): per un guscio, tra il
    contorno esterno e quello interno, cioe sopra al materiale dello strato."""
    inside = np.zeros(len(Q), dtype=bool)
    ay, by = A[:, 1], B[:, 1]
    dy = np.where(np.abs(by - ay) < 1e-12, 1e-12, by - ay)
    for s in range(0, len(Q), chunk):
        q = Q[s:s + chunk]
        qy, qx = q[:, None, 1], q[:, None, 0]
        straddle = (ay[None] > qy) != (by[None] > qy)
        xcross = A[None, :, 0] + (qy - ay[None]) * (B[None, :, 0] - A[None, :, 0]) / dy[None]
        inside[s:s + chunk] = (np.sum(straddle & (qx < xcross), axis=1) % 2) == 1
    return inside


def wall_overhang(g, layer, limit_deg=45.0, every=2, step=0.05, max_query=3000, seed=0):
    """Sbalzo visto dallo slicer: per ogni strato (uno ogni 'every'), la sporgenza massima del muro
    esterno rispetto al percorso del muro esterno dello strato sotto (distanza punto-segmento),
    contando solo i punti che cadono fuori dalla regione racchiusa da quei percorsi (pari/dispari):
    un contorno che rientra sopra il materiale (bordo che si chiude, parete che si stringe) e appoggiato.
    Le sporgenze oltre 3 altezze di strato sono discontinuita (un contorno che nasce, come il
    passaggio dal fondo alla parete, o l'ultimo strato) e si contano a parte: non sono pendenze."""
    S = _layer_segments(g)
    zs = sorted(S)
    rng = np.random.default_rng(seed)
    rows, jumps = [], []
    for i in range(1, len(zs)):
        if i % every:
            continue
        dz = zs[i] - zs[i - 1]
        if dz <= 0 or dz > 1.5 * layer:
            continue
        Q = _densify(*S[zs[i]], step=step)
        if len(Q) > max_query:
            Q = Q[rng.choice(len(Q), max_query, replace=False)]
        A0, B0 = S[zs[i - 1]]
        dd = _dist_to_segments(Q, A0, B0)
        dd[_inside_even_odd(Q, A0, B0)] = 0.0       # sopra al materiale dello strato sotto: appoggiato
        d = float(dd.max())
        if d > 3 * layer:
            jumps.append(round(zs[i], 2))
            continue
        p99 = float(np.percentile(dd, 99))
        rows.append((zs[i], d, math.degrees(math.atan2(d, dz)), math.degrees(math.atan2(p99, dz))))
    res = {"layers_checked": len(rows), "every": every, "discontinuities_z": jumps,
           "note": "distanza punto-segmento; campioni del muro superiore ogni %g mm" % step}
    if rows:
        a = np.array([r[2] for r in rows])
        k = int(np.argmax(a))
        tol = 0.5
        res.update({"max_deg": round(float(a.max()), 1), "z_max": round(rows[k][0], 2),
                    "max_shift_mm": round(rows[k][1], 3), "median_deg": round(float(np.median(a)), 1),
                    "layers_over_limit": int(np.sum(a > limit_deg + tol)),
                    "z_over_limit": [round(r[0], 1) for r in rows if r[2] > limit_deg + tol][:20],
                    # misura robusta: il 99% dei punti di ogni strato; esclude i picchi isolati del
                    # percorso (cuciture, discretizzazione) che non sono superfici inclinate
                    "p99_max_deg": round(max(r[3] for r in rows), 1),
                    "p99_layers_over_limit": int(sum(1 for r in rows if r[3] > limit_deg + tol))})
    return res


def spiral_continuity(g, z_from, z_to, min_jump=0.05):
    """Parete in spirale tra z_from e z_to: spostamenti a vuoto, ritrazioni e discese di z."""
    travels, retracts, drops, segs, last_z = 0, 0, 0, 0, None
    for feat, _, z, x0, y0, x1, y1, e in moves(g):
        if z < z_from or z > z_to or feat != "Outer wall":
            continue
        if e < 0:
            retracts += 1
        elif e == 0:
            if math.hypot(x1 - x0, y1 - y0) > min_jump:
                travels += 1
        else:
            segs += 1
            if last_z is not None and z < last_z - 1e-4:
                drops += 1
            last_z = z
    return {"z_from": round(z_from, 2), "z_to": round(z_to, 2), "segments": segs,
            "travel_moves": travels, "retractions": retracts, "z_drops": drops,
            "continuous": segs > 0 and travels == 0 and retracts == 0 and drops == 0}


def summary_lines(res):
    """Righe in italiano per scheda e terminale."""
    if not res.get("ok"):
        return ["Bambu Studio non ha affettato il pezzo: codice %s (%s)." % (res.get("exit_code"), res.get("exit_text"))]
    L = ["Stampante %s, filamento %s, Bambu Studio da riga di comando."
         % (res["printer"], res.get("filament", "PLA del profilo")),
         "Peso %.1f g (densita del profilo %s g/cm3), %.1f m di filo, %d strati, tempo %s."
         % (res["weight_g"], res.get("density_g_cm3", "?"), res["filament_m"], res["layers"], res["time_text"])]
    if res.get("support_m"):
        L.append("Supporti generati: %.1f m." % res["support_m"])
    if res.get("gap_infill_m"):
        L.append("Gap infill: %.1f m." % res["gap_infill_m"])
    ov = res.get("wall_overhang", {})
    if ov.get("max_deg") is not None:
        L.append("Sbalzo del muro esterno strato per strato (uno strato ogni %d, %d controllati): sul 99%% dei "
                 "punti di ogni strato al massimo %.1f gradi, %d strati oltre il limite; picco assoluto %.1f gradi "
                 "a z %.1f mm, %d strati con almeno un punto oltre il limite; mediana %.1f gradi."
                 % (ov["every"], ov["layers_checked"], ov["p99_max_deg"], ov["p99_layers_over_limit"],
                    ov["max_deg"], ov["z_max"], ov["layers_over_limit"], ov["median_deg"]))
        if ov.get("discontinuities_z"):
            L.append("Discontinuita (contorni che nascono, non pendenze) a z: %s mm."
                     % ", ".join("%g" % z for z in ov["discontinuities_z"][:6]))
    sp = res.get("spiral")
    if sp:
        L.append("Spirale tra z %.1f e %.1f mm: %d tratti, %d spostamenti a vuoto, %d ritrazioni, %d discese di z: %s."
                 % (sp["z_from"], sp["z_to"], sp["segments"], sp["travel_moves"], sp["retractions"], sp["z_drops"],
                    "continua" if sp["continuous"] else "NON continua"))
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description="Affetta un STL con Bambu Studio e riassume il G-code")
    ap.add_argument("stl")
    ap.add_argument("params")
    ap.add_argument("--printer", default="Bambu Lab X1 Carbon 0.4 nozzle")
    ap.add_argument("--out", default=None)
    ap.add_argument("--support", action="store_true", help="attiva i supporti automatici")
    ap.add_argument("--support-angle", type=float, default=None,
                    help="soglia dei supporti in gradi dall'orizzontale (vuoto = valore del profilo Bambu)")
    ap.add_argument("--support-type", default="grid", choices=["grid", "snug", "tree"],
                    help="supporti normali a griglia, normali aderenti o ad albero (organici)")
    ap.add_argument("--support-bed-only", action="store_true", help="supporti solo dal piano")
    ap.add_argument("--no-spiral", action="store_true", help="vaso a parete normale (per provare i supporti)")
    ap.add_argument("--save", default=None, help="salva il riassunto JSON anche in questo file")
    ap.add_argument("--no-analysis", action="store_true", help="salta l'analisi del G-code (piu veloce)")
    ap.add_argument("--material", default="PLA", help="PLA, PETG, ABS, ASA o TPU (se manca il profilo: PLA)")
    a = ap.parse_args(argv)
    from . import generators
    with open(a.params, encoding="utf-8") as fh:
        params = generators.resolve(json.load(fh))
    if a.no_spiral:
        params["print"]["mode"] = "walls"
        params["wall"]["thickness"] = params["print"]["line_width"]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.stl)), "slicer")
    extra = None
    if a.support:
        extra = support_overrides(a.support_type, a.support_bed_only)
        if a.support_angle is not None:
            extra["support_threshold_angle"] = "%g" % a.support_angle
    res = slice_stl(a.stl, params, out, a.printer, extra, analyse=not a.no_analysis, material=a.material)
    for path in [os.path.join(out, "slicer.json")] + ([a.save] if a.save else []):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, ensure_ascii=False)
    print("\n".join(summary_lines(res)))


if __name__ == "__main__":
    main()
