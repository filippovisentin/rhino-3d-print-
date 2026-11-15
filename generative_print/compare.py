"""Confronto tra due G-code dello stesso pezzo (di solito il nostro e quello di Bambu Studio).

Uso:  python -m generative_print.compare nostro.gcode bambu.gcode

Legge solo il testo: per ogni tipo di tratto (pareti, pieno, rado, supporti...) metri, volume, peso e
tempo a velocita costante; poi il volume estruso strato per strato, per vedere a che quota i due
file si allontanano. Del nostro file guarda solo il corpo (tra `; GP_BODY_START` e `; GP_BODY_END`).
"""
import math
import re
import sys

import numpy as np

_WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")
# tratti di Bambu che da noi sono pieno o parete
GROUPS = {"Outer wall": "pareti", "Inner wall": "pareti", "Overhang wall": "pareti", "Gap infill": "pareti",
          "Floating vertical shell": "pareti",
          "Internal solid infill": "pieno", "Top surface": "pieno", "Bottom surface": "pieno", "Bridge": "pieno",
          "Sparse infill": "rado", "Support": "supporti", "Support interface": "supporti",
          "Support transition": "supporti"}


def read(text, diameter=1.75):
    """Statistiche di un G-code: {"features": {tipo: {"m", "mm3", "min"}}, "layers": volume per quota
    {z: mm3}, "retractions", "travel_m", "volume_mm3"}. Le quote sono arrotondate al centesimo."""
    if "; GP_BODY_START" in text:
        text = text.split("; GP_BODY_START", 1)[1].split("; GP_BODY_END", 1)[0]
    area = math.pi * diameter ** 2 / 4.0
    feat, x, y, z, f = "?", None, None, 0.0, 0.0
    feats, layers, retr, travel = {}, {}, 0, 0.0
    for line in text.splitlines():
        if line.startswith("; FEATURE:"):
            feat = line.split(":", 1)[1].strip()
            continue
        if not line.startswith(("G1 ", "G0 ")):
            continue
        w = dict((k, float(v)) for k, v in _WORD.findall(line.split(";", 1)[0]))
        f = w.get("F", f)
        nx, ny, z, e = w.get("X", x), w.get("Y", y), w.get("Z", z), w.get("E", 0.0)
        d = math.hypot(nx - x, ny - y) if None not in (x, y, nx, ny) else 0.0
        if e > 0.0 and d > 0.0:
            if feat != "Custom":                       # linea di spurgo di Bambu: non e il pezzo
                row = feats.setdefault(feat, {"m": 0.0, "mm3": 0.0, "min": 0.0})
                row["m"] += d / 1000.0
                row["mm3"] += e * area
                row["min"] += d / max(f, 1.0)
                k = round(z, 2)
                layers[k] = layers.get(k, 0.0) + e * area
        elif e < 0.0:
            retr += 1
        elif d > 0.0:
            travel += d
        x, y = nx, ny
    return {"features": feats, "layers": layers, "retractions": retr, "travel_m": travel / 1000.0,
            "volume_mm3": sum(r["mm3"] for r in feats.values())}


def grouped(stats):
    """Volume in mm3 per gruppo (pareti, pieno, rado, supporti, altro)."""
    out = {}
    for k, r in stats["features"].items():
        g = GROUPS.get(k, "altro")
        out[g] = out.get(g, 0.0) + r["mm3"]
    return out


def layer_table(a, b, band=5.0):
    """Volume estruso da a e da b per fasce di quota alte `band` mm: [(z da, z a, mm3 a, mm3 b)]."""
    top = max(list(a["layers"]) + list(b["layers"]) + [band])
    rows = []
    for lo in np.arange(0.0, top, band):
        va = sum(v for z, v in a["layers"].items() if lo < z <= lo + band)
        vb = sum(v for z, v in b["layers"].items() if lo < z <= lo + band)
        rows.append((float(lo), float(lo + band), va, vb))
    return rows


def report(a, b, names=("noi", "Bambu"), density=1.26, band=5.0):
    ga, gb = grouped(a), grouped(b)
    lines = ["%-10s %10s %10s %8s" % ("gruppo", names[0] + " g", names[1] + " g", "scarto")]
    for g in ("pareti", "pieno", "rado", "supporti", "altro"):
        va, vb = ga.get(g, 0.0) * density / 1000.0, gb.get(g, 0.0) * density / 1000.0
        if va or vb:
            lines.append("%-10s %10.2f %10.2f %7.0f%%" % (g, va, vb, 100.0 * (va - vb) / vb if vb else float("nan")))
    va, vb = a["volume_mm3"] * density / 1000.0, b["volume_mm3"] * density / 1000.0
    lines.append("%-10s %10.2f %10.2f %7.0f%%" % ("totale", va, vb, 100.0 * (va - vb) / vb if vb else float("nan")))
    lines.append("ritrazioni %d / %d, spostamenti a vuoto %.1f / %.1f m"
                 % (a["retractions"], b["retractions"], a["travel_m"], b["travel_m"]))
    lines.append("volume per fascia di quota (cm3):")
    for lo, hi, x, y in layer_table(a, b, band):
        lines.append("  z %5.1f - %5.1f  %7.2f %7.2f %6.0f%%" % (lo, hi, x / 1000.0, y / 1000.0,
                                                               100.0 * (x - y) / y if y else float("nan")))
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    texts = [open(p, encoding="utf-8", errors="replace").read() for p in argv[:2]]
    print("\n".join(report(read(texts[0]), read(texts[1]))))


if __name__ == "__main__":
    main()
