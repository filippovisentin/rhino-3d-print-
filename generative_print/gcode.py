"""Scrittura del G-code a partire dai percorsi degli strati, e controllo del testo scritto.

Ingresso: una lista di strati, dal basso. Ogni strato e un dizionario
    {"z": quota in mm della faccia superiore, "h": altezza dello strato, "paths": [...]}
e ogni percorso
    {"type": "outer" | "inner" | "solid" | "sparse" | "support" | "interface",
     "pts": punti (N, 2) in mm nelle coordinate del piatto, "closed": True se e un anello,
     "width": larghezza della linea (facoltativa)}.

Avvio e fine stampa non si scrivono qui: arrivano da `machine.start_end` (modelli ufficiali della
stampante). Tra i due sta il nostro corpo, tra i segni `; GP_BODY_START` e `; GP_BODY_END`:
coordinate assolute, estrusione relativa (M83).
"""
import math
import re

import numpy as np

from . import machine

ORDER = ("support", "interface", "inner", "outer", "solid", "sparse")       # ordine di stampa in uno strato
FEATURE = {"outer": "Outer wall", "inner": "Inner wall", "solid": "Internal solid infill",
           "sparse": "Sparse infill", "support": "Support", "interface": "Support interface"}
DEFAULTS = {
    "line_width": 0.45, "filament_diameter": 1.75,
    # velocita in mm/s: prudenti, i percorsi non sono ancora mai stati stampati
    "speed": {"outer": 120.0, "inner": 180.0, "solid": 150.0, "sparse": 200.0, "support": 120.0, "interface": 80.0},
    "first_layer_speed": 40.0, "travel_speed": 400.0, "z_speed": 12.0,
    "retract": 0.8, "retract_speed": 30.0, "z_hop": 0.4, "retract_min_travel": 1.5,
    "accel": 5000.0, "first_layer_accel": 500.0,
    "bed": (180.0, 180.0, 180.0),
    "prepare_s": 390.0,              # riscaldamento, livellamento e spurgo: ipotesi, non misurata
    "start_xy": (0.0, 0.0),
}
BODY_START, BODY_END = "; GP_BODY_START", "; GP_BODY_END"
_WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def section(width, h):
    """Sezione in mm2 di una linea estrusa: rettangolo con i due fianchi a semicerchio."""
    return (width - h) * h + math.pi * (h / 2.0) ** 2


def spacing(width, h):
    """Distanza tra due linee affiancate perche riempiano tutto: sezione = distanza x altezza."""
    return width - h * (1.0 - math.pi / 4.0)


def _n(v, digits=3):
    t = ("%.*f" % (digits, v)).rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


def _clock(seconds):
    s = int(round(seconds))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return ("%dh %dm %ds" % (h, m, s)) if h else "%dm %ds" % (m, s)


def _move_time(length, v, a):
    """Tempo di un tratto che parte e arriva fermo (trapezio di velocita)."""
    if length <= 0.0:
        return 0.0
    return 2.0 * math.sqrt(length / a) if length < v * v / a else length / v + v / a


def _points(p):
    pts = np.asarray(p["pts"], dtype=float)[:, :2]
    if p.get("closed") and len(pts) > 2 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts


def sequence(paths, pos):
    """Percorsi nell'ordine di stampa: ogni volta quello che comincia piu vicino all'ugello.
    Un percorso aperto si puo percorrere al contrario; un anello parte dal suo vertice piu vicino.
    Restituisce ([(percorso, punti da seguire)], posizione finale)."""
    P = [_points(p) for p in paths]
    n = len(P)
    pos = np.asarray(pos, dtype=float)
    if n == 0:
        return [], pos
    closed = np.array([bool(p.get("closed")) for p in paths])
    S, E = np.array([p[0] for p in P]), np.array([p[-1] for p in P])
    alive, rings, out = np.ones(n, dtype=bool), np.flatnonzero(closed), []
    for _ in range(n):
        dS = np.hypot(S[:, 0] - pos[0], S[:, 1] - pos[1])
        dE = np.hypot(E[:, 0] - pos[0], E[:, 1] - pos[1])
        dE[closed] = np.inf
        turn = {}
        for i in rings:
            if alive[i]:
                d = np.hypot(P[i][:, 0] - pos[0], P[i][:, 1] - pos[1])
                turn[i] = int(d.argmin())
                dS[i] = d[turn[i]]
        dS[~alive], dE[~alive] = np.inf, np.inf
        i = int(np.minimum(dS, dE).argmin())
        if closed[i]:
            pts = np.vstack([P[i][turn[i]:], P[i][:turn[i] + 1]])
        else:
            pts = P[i][::-1] if dE[i] < dS[i] else P[i]
        alive[i] = False
        out.append((paths[i], pts))
        pos = pts[-1]
    return out, pos


def write(layers, start, end, change, filament, settings=None):
    """Testo del G-code completo. start / end / change / filament: come li da `machine.start_end`.
    Restituisce un dizionario: text, layers, time_s (solo stampa), total_time_s, filament_mm,
    volume_mm3, weight_g, by_type (mm estrusi per tipo), travel_mm, retractions."""
    if not layers:
        raise ValueError("nessuno strato da scrivere")
    s = dict(DEFAULTS)
    s.update(settings or {})
    speeds = dict(DEFAULTS["speed"])
    speeds.update((settings or {}).get("speed", {}))
    dia = float(filament.get("diameter", s["filament_diameter"]))
    fil_area = math.pi * (dia / 2.0) ** 2
    ratio = float(filament.get("flow_ratio", 1.0))
    max_flow = float(filament["max_flow"])
    hop, rl, min_travel = float(s["z_hop"]), float(s["retract"]), float(s["retract_min_travel"])
    v_travel, v_retract, v_z = float(s["travel_speed"]), float(s["retract_speed"]), float(s["z_speed"])
    f_travel, f_retract = int(round(v_travel * 60)), int(round(v_retract * 60))
    fan_layer = int(filament.get("fan_off_layers", 1))
    n = len(layers)
    pos = np.asarray(s["start_xy"], dtype=float)
    retracted = False
    blocks, times = [], []
    total_e, travel, n_retract = 0.0, 0.0, 0
    by_type = dict((k, 0.0) for k in ORDER)

    for i, layer in enumerate(layers):
        z, h = float(layer["z"]), float(layer["h"])
        acc = float(s["first_layer_accel"] if i == 0 else s["accel"])
        out = ["; CHANGE_LAYER", "; Z_HEIGHT: " + _n(z), "; LAYER_HEIGHT: " + _n(h)]
        t = 0.0
        if not retracted:
            out.append("G1 E-%s F%d" % (_n(rl, 5), f_retract))
            retracted, n_retract, t = True, n_retract + 1, t + rl / v_retract
        out.append(machine.render(change, {"layer_num": i, "total_layer_count": n, "layer_z": z}).strip())
        out.append(None)                                        # qui va M73, quando si conoscono i tempi
        if i < 2:
            out.append("M204 S%d" % acc)
        if i == 1:
            if filament["nozzle"] != filament["nozzle_first"]:
                out.append("M104 S%d" % round(filament["nozzle"]))
            if filament["bed"] != filament["bed_first"]:
                out.append("M140 S%d" % round(filament["bed"]))
        if i == fan_layer:
            out.append("M106 S%d" % round(255.0 * float(filament.get("fan", 100.0)) / 100.0))
        feature = None
        for kind in ORDER:
            group = [p for p in layer["paths"] if p["type"] == kind and len(p["pts"]) >= 2]
            seq, _ = sequence(group, pos)
            for p, pts in seq:
                w = float(p.get("width", s["line_width"]))
                sec = section(w, h) * ratio
                e_mm = sec / fil_area
                v = min(float(s["first_layer_speed"]) if i == 0 else float(speeds[kind]), max_flow / sec)
                d = float(np.hypot(*(pts[0] - pos)))
                if retracted or d > min_travel:
                    if not retracted:
                        out.append("G1 E-%s F%d" % (_n(rl, 5), f_retract))
                        n_retract, t = n_retract + 1, t + rl / v_retract
                    out += ["G1 Z%s F%d" % (_n(z + hop), f_travel), "G1 X%s Y%s" % (_n(pts[0][0]), _n(pts[0][1])),
                            "G1 Z%s" % _n(z), "G1 E%s F%d" % (_n(rl, 5), f_retract)]
                    retracted = False
                    t += rl / v_retract + 2.0 * hop / v_z
                elif d > 1e-4:
                    out.append("G1 X%s Y%s F%d" % (_n(pts[0][0]), _n(pts[0][1]), f_travel))
                travel += d
                t += _move_time(d, v_travel, acc)
                if feature != (kind, w):
                    out += ["; FEATURE: " + FEATURE[kind], "; LINE_WIDTH: " + _n(w)]
                    feature = (kind, w)
                out.append("G1 F%d" % round(v * 60))
                seg = np.hypot(*(pts[1:] - pts[:-1]).T)
                for q, dq in zip(pts[1:], seg):
                    e = round(float(dq) * e_mm, 5)
                    if dq < 1e-4 or e <= 0.0:
                        continue
                    out.append("G1 X%s Y%s E%s" % (_n(q[0]), _n(q[1]), _n(e, 5)))
                    total_e += e
                length = float(seg.sum())
                by_type[kind] += length
                t += _move_time(length, v, acc)
                pos = pts[-1]
        if not retracted:                                       # si cambia strato con il filo ritirato
            out.append("G1 E-%s F%d" % (_n(rl, 5), f_retract))
            retracted, n_retract, t = True, n_retract + 1, t + rl / v_retract
        blocks.append(out)
        times.append(t)

    print_s = float(sum(times))
    total_s = print_s + float(s["prepare_s"])
    volume = total_e * fil_area
    weight = volume * float(filament["density"]) / 1000.0
    max_z = float(layers[-1]["z"])
    head = ["; HEADER_BLOCK_START", "; generated by GENERATIVE PRINT (Rhino)",
            "; model printing time: %s; total estimated time: %s" % (_clock(print_s), _clock(total_s)),
            "; total layer number: %d" % n, "; total filament length [mm] : %.2f" % total_e,
            "; total filament volume [cm^3] : %.2f" % (volume / 1000.0),
            "; total filament weight [g] : %.2f" % weight,
            "; filament_density: %s" % _n(float(filament["density"])), "; filament_diameter: %s" % _n(dia),
            "; max_z_height: %.2f" % max_z, "; HEADER_BLOCK_END", "",
            "; EXECUTABLE_BLOCK_START", "M73 P0 R%d" % math.ceil(total_s / 60.0)]
    L = head + [start.rstrip("\n"), BODY_START, "G90", "M83", "G92 E0"]
    done = 0.0
    for out, t in zip(blocks, times):
        k = out.index(None)
        out[k] = "M73 P%d R%d" % (int(100.0 * done / print_s) if print_s > 0 else 0,
                                  math.ceil((print_s - done) / 60.0))
        L += out
        done += t
    L += [BODY_END, end.rstrip("\n"), "M73 P100 R0", "; EXECUTABLE_BLOCK_END", ""]
    return {"text": "\n".join(L), "layers": n, "time_s": print_s, "total_time_s": total_s,
            "filament_mm": total_e, "volume_mm3": volume, "weight_g": weight, "by_type": by_type,
            "travel_mm": travel, "retractions": n_retract, "max_z": max_z}


def generate(layers, printer="Bambu Lab A1 mini 0.4 nozzle", material="PLA", settings=None,
             root=machine.PROFILES, verify=True):
    """G-code pronto per la stampante: avvio e fine dai profili installati, corpo dai nostri percorsi.
    Con verify il testo viene riletto da `check` e l'esito sta in ["check"]."""
    s = dict(DEFAULTS)
    s.update(settings or {})
    first = [p for p in layers[0]["paths"] if len(p["pts"]) >= 2]
    if not first:
        raise ValueError("il primo strato non ha percorsi")
    pts = np.vstack([_points(p) for p in first])
    half = float(s["line_width"]) / 2.0
    lo, hi = pts.min(axis=0) - half, pts.max(axis=0) + half
    speed = dict(DEFAULTS["speed"], **(settings or {}).get("speed", {}))["outer"]
    start, end, change, fil = machine.start_end(printer, material, lo.tolist(), (hi - lo).tolist(),
                                                float(layers[-1]["z"]), layer=float(layers[0]["h"]),
                                                line_width=float(s["line_width"]), outer_wall_speed=speed, root=root)
    res = write(layers, start, end, change, fil, settings)
    res["filament"] = fil
    if verify:
        res["check"] = check(res["text"], bed=s["bed"])
    return res


def check(text, bed=(180.0, 180.0, 180.0), expected_mm3=None, tol=0.02):
    """Rilegge il nostro corpo del G-code (tra i due segni) e ne verifica i conti.
    - tutti i movimenti dentro il piatto e sotto l'altezza massima;
    - ogni strato poggia sul precedente (quota = quota prima + altezza dello strato);
    - si estrude solo alla quota dello strato e con il filo riportato in avanti dopo una ritrazione;
    - larghezza della linea che risulta dal filo spinto: tra 0,15 e 1,5 mm;
    - filo totale uguale a quello dichiarato nell'intestazione; se dato, al volume atteso.
    Avvio e fine (ufficiali della stampante) non si controllano: l'avvio esce apposta dal piatto.
    Non sa se sotto una linea c'e davvero materiale: quello dipende dai percorsi, non dal testo."""
    errors = []

    def err(msg):
        if len(errors) < 20:
            errors.append(msg)

    head = dict((k.strip(), v.strip()) for k, v in re.findall(r"^; ([^:\n]+?)\s*:\s*([^\n]*)$", text.split("; HEADER_BLOCK_END")[0], re.M))
    dia = float(head.get("filament_diameter", 1.75))
    density = float(head.get("filament_density", 1.24))
    fil_area = math.pi * (dia / 2.0) ** 2
    if BODY_START not in text or BODY_END not in text:
        return {"ok": False, "errors": ["mancano i segni del corpo del G-code"]}
    body = text.split(BODY_START, 1)[1].split(BODY_END, 1)[0].split("\n")
    x = y = z = None
    layer_z, layer_h, prev_z, layers = None, None, 0.0, 0
    balance, extruded, printed, travel, retractions = 0.0, 0.0, 0.0, 0.0, 0
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for ln, line in enumerate(body, 1):
        if line.startswith(";"):
            if line.startswith("; Z_HEIGHT:"):
                if layer_z is not None:
                    prev_z = layer_z
                layer_z = float(line.split(":")[1])
                layers += 1
            elif line.startswith("; LAYER_HEIGHT:"):
                layer_h = float(line.split(":")[1])
                if abs(layer_z - prev_z - layer_h) > 1e-6:
                    err("strato %d a z %g: non poggia sul precedente (z %g, altezza %g)" % (layers, layer_z, prev_z, layer_h))
            continue
        if not (line.startswith("G1 ") or line.startswith("G0 ")):
            continue
        w = dict((k, float(v)) for k, v in _WORD.findall(line.split(";")[0]))
        nx, ny, nz, e = w.get("X", x), w.get("Y", y), w.get("Z", z), w.get("E", 0.0)
        for k, (v, top) in enumerate(((nx, bed[0]), (ny, bed[1]), (nz, bed[2]))):
            if v is None:
                continue
            lo[k], hi[k] = min(lo[k], v), max(hi[k], v)
            if v < -1e-9 or v > top + 1e-9 or (k == 2 and v <= 0.0):
                err("riga %d: %s = %g fuori dalla stampante (0 - %g)" % (ln, "XYZ"[k], v, top))
        moved = math.hypot(nx - x, ny - y) if None not in (x, y, nx, ny) else 0.0
        if e < 0.0:
            balance += e
            retractions += 1
        elif e > 0.0 and moved < 1e-9:
            balance += e
            if balance > 1e-6:
                err("riga %d: filo spinto da fermi oltre la ritrazione (%g mm)" % (ln, balance))
        elif e > 0.0:
            if abs(balance) > 1e-6:
                err("riga %d: estrusione con il filo ancora ritirato" % ln)
            if layer_z is None or nz is None or abs(nz - layer_z) > 1e-6:
                err("riga %d: estrusione fuori dalla quota dello strato (z %s)" % (ln, nz))
            elif layer_h:
                width = (e * fil_area / moved - math.pi * (layer_h / 2.0) ** 2) / layer_h + layer_h
                if not 0.15 <= width <= 1.5:
                    err("riga %d: linea larga %.2f mm (filo %g mm su %g mm)" % (ln, width, e, moved))
            extruded += e
            printed += moved
        else:
            travel += moved
        x, y, z = nx, ny, nz
    volume = extruded * fil_area
    if "total filament length [mm]" in head:
        declared = float(head["total filament length [mm]"])
        if abs(declared - extruded) > max(0.02, 1e-4 * declared):
            err("filo dichiarato %.2f mm, nel testo %.2f mm" % (declared, extruded))
    if "total layer number" in head and int(head["total layer number"]) != layers:
        err("strati dichiarati %s, nel testo %d" % (head["total layer number"], layers))
    if expected_mm3 is not None and abs(volume - expected_mm3) > tol * expected_mm3:
        err("volume estruso %.1f mm3, atteso %.1f mm3 (tolleranza %g %%)" % (volume, expected_mm3, 100 * tol))
    return {"ok": not errors, "errors": errors, "layers": layers, "extruded_mm": extruded, "volume_mm3": volume,
            "weight_g": volume * density / 1000.0, "printed_mm": printed, "travel_mm": travel,
            "retractions": retractions, "min": lo, "max": hi}
