"""Avvio e fine stampa della stampante, per il G-code scritto dallo slicer di Rhino.

La sequenza di avvio di una Bambu Lab (riscaldamento, livellamento, spurgo) e lunga e specifica
della macchina: non la riscriviamo. Si legge il modello ufficiale dai profili installati con
Bambu Studio (solo i file, il programma non viene avviato) e lo si compila con i nostri valori:
temperature, tipo di piatto, ingombro del primo strato. I modelli usano {if ...}{else}{endif},
{espressioni} e [variabili].
"""
import json
import os
import re

PROFILES = r"C:\Program Files\Bambu Studio\resources\profiles\BBL"
FILAMENTS = {"PLA": "Bambu PLA Basic", "PETG": "Bambu PETG Basic", "TPU": "Bambu TPU 95A",
             "ABS": "Bambu ABS", "ASA": "Bambu ASA"}
SUFFIX = {"Bambu Lab A1 mini 0.4 nozzle": "@BBL A1M", "Bambu Lab A1 0.4 nozzle": "@BBL A1",
          "Bambu Lab X1 Carbon 0.4 nozzle": "@BBL X1C", "Bambu Lab P1S 0.4 nozzle": "@BBL X1C"}
_SAFE = re.compile(r'^[\w\s\[\]\(\)\.\+\-\*/<>=!&|"\']+$')
_FLOW = re.compile(r"\{(if|elsif)\s+(.*?)\}|\{(else|endif)\}")


def load_profile(kind, name, root=PROFILES):
    """Profilo Bambu (kind = machine, filament, process) con tutta la catena di 'inherits' risolta."""
    path = os.path.join(root, kind, name + ".json")
    if not os.path.exists(path):
        raise FileNotFoundError("profilo non trovato: %s" % path)
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    parent = d.get("inherits")
    out = load_profile(kind, parent, root) if parent else {}
    out.update(d)
    return out


def _first(v, default=None):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    return default if v in (None, "") else v


def filament_values(printer, material="PLA", root=PROFILES):
    """Valori del filamento che servono al G-code, dal profilo Bambu per quella stampante."""
    base, suffix = FILAMENTS[material], SUFFIX[printer]
    for name in ("%s %s" % (base, suffix), "%s %s 0.4 nozzle" % (base, suffix)):
        if os.path.exists(os.path.join(root, "filament", name + ".json")):
            break
    else:
        raise FileNotFoundError("nessun profilo %s per %s" % (base, printer))
    p = load_profile("filament", name, root)
    num = lambda key, default=0.0: float(_first(p.get(key), default))
    high, flow = num("nozzle_temperature_range_high", 240), num("filament_max_volumetric_speed", 12)
    return {"profile": name, "type": str(_first(p.get("filament_type"), material)),
            "nozzle_first": num("nozzle_temperature_initial_layer", 220), "nozzle": num("nozzle_temperature", 220),
            "bed_first": num("textured_plate_temp_initial_layer", 65), "bed": num("textured_plate_temp", 65),
            "max_flow": flow, "density": num("filament_density", 1.24),
            # come fa Bambu Studio: se il profilo non da un valore di spurgo, usa il massimo del filamento
            "flush_temp": num("filament_flush_temp") or high,
            "flush_speed": num("filament_flush_volumetric_speed") or flow}


def templates(printer, root=PROFILES):
    """Modelli (avvio, fine, cambio strato) della stampante, cosi come sono nei profili."""
    out = {}
    for key in ("machine_start_gcode", "machine_end_gcode", "layer_change_gcode"):
        path = os.path.join(root, "machine", "%s template %s.json" % (printer, key))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                out[key] = json.load(fh)[key]
        else:                                    # profili vecchi: il testo sta nel profilo della macchina
            out[key] = load_profile("machine", printer, root).get(key, "")
    return out


def _eval(expr, values):
    if not _SAFE.match(expr) or "__" in expr:
        raise ValueError("espressione non ammessa nel modello: %s" % expr)
    py = re.sub(r"!(?!=)", " not ", expr.replace("&&", " and ").replace("||", " or "))
    try:
        return eval(py, {"__builtins__": {}}, values)      # solo nomi dei nostri valori, nessuna funzione
    except NameError as exc:
        raise ValueError("valore sconosciuto nel modello: %s (%s)" % (expr, exc))


def _text(v):
    if isinstance(v, (list, tuple)):
        v = v[0]
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return ("%.3f" % v).rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


def render(template, values):
    """Compila un modello di G-code: condizioni, {espressioni} e [variabili]."""
    out, stack, pos = [], [], 0          # stack: [ramo attivo, un ramo gia preso, genitore attivo]
    live = lambda: all(s[0] for s in stack)
    for m in _FLOW.finditer(template):
        if live():
            out.append(template[pos:m.start()])
        pos = m.end()
        word = m.group(1) or m.group(3)
        if word == "if":
            parent = live()
            ok = bool(_eval(m.group(2), values)) if parent else False
            stack.append([ok, ok, parent])
        elif word in ("elsif", "else"):
            s = stack[-1]
            ok = s[2] and not s[1] and (word == "else" or bool(_eval(m.group(2), values)))
            s[0], s[1] = ok, s[1] or ok
        else:
            stack.pop()
    out.append(template[pos:])
    if stack:
        raise ValueError("modello con {if} non chiuso")
    text = "".join(out)
    text = re.sub(r"\{([^{}\n]+)\}", lambda m: _text(_eval(m.group(1), values)), text)
    return re.sub(r"\[([a-z_0-9]+)\]", lambda m: _text(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def start_end(printer, material, first_layer_min, first_layer_size, max_z, layer=0.2, line_width=0.45,
              outer_wall_speed=100.0, root=PROFILES):
    """G-code di avvio e di fine per un lavoro: (avvio, fine, modello del cambio strato, filamento).
    first_layer_min / first_layer_size: ingombro del primo strato sul piatto, in mm (zona da livellare)."""
    f = filament_values(printer, material, root)
    t = templates(printer, root)
    flow = min(f["max_flow"], outer_wall_speed * layer * line_width)
    cx = first_layer_min[0] + first_layer_size[0] / 2.0
    cy = first_layer_min[1] + first_layer_size[1] / 2.0
    values = {"bed_temperature_initial_layer_single": f["bed_first"], "curr_bed_type": "Textured PEI Plate",
              "initial_no_support_extruder": 0, "initial_extruder": 0, "nozzle_diameter": 0.4,
              "nozzle_temperature_initial_layer": [f["nozzle_first"]], "nozzle_temperature": [f["nozzle"]],
              "filament_type": [f["type"]], "flush_temperatures": [f["flush_temp"]],
              "flush_volumetric_speeds": [f["flush_speed"]], "outer_wall_volumetric_speed": flow,
              "first_layer_print_min": [float(x) for x in first_layer_min],
              "first_layer_print_size": [float(x) for x in first_layer_size],
              "first_layer_center_no_wipe_tower": [cx, cy], "max_layer_z": float(max_z),
              "spiral_mode": False, "print_sequence": "by layer"}
    return (render(t["machine_start_gcode"], values), render(t["machine_end_gcode"], values),
            t["layer_change_gcode"], f)


def leftovers(text):
    """Segnaposto rimasti in un G-code compilato (deve essere una lista vuota)."""
    return re.findall(r"\{[^{}\n]*\}|\[[a-z_0-9]{3,}\]", text)
