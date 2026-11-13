"""Analisi di stampa di un solido qualsiasi (non solo lampade e vasi): la usa lo slicer interno.

`analyse(V, F, settings)` appoggia il pezzo al piano (z minima = 0), poi misura chiusura della
mesh, ingombro rispetto al volume della stampante, sbalzi, isole, appoggio, stabilita e stima
materiale e tempo dalle impostazioni di stampa. Solo numpy.

Le stime di peso e tempo sono geometriche (gusci + riempimento), non un percorso vero:
per i numeri precisi si affetta con uno slicer (vedi generative_print.bambu).
"""
import math

import numpy as np

from . import checks

DEFAULTS = {"layer": 0.2, "line_width": 0.45, "walls": 2, "infill": 0.15, "top_layers": 5, "bottom_layers": 4,
            "density_g_cm3": 1.24, "filament_diameter": 1.75, "flow_mm3_s": 6.0,
            "bed": [256.0, 256.0, 256.0], "overhang_limit_deg": 43.0, "supports": False}


def settings(over=None):
    s = dict(DEFAULTS)
    s.update(over or {})
    return s


def material(V, F, s):
    """Volume estruso stimato (mm3): pareti sulle facce ripide, strati pieni su quelle piane,
    riempimento nel resto. Mai piu del volume del solido."""
    vol, _ = checks.volume_centroid(V, F)
    n, areas = checks.face_normals_areas(V, F)
    flat = np.abs(n[:, 2]) > math.cos(math.radians(45.0))
    up = flat & (n[:, 2] > 0)
    down = flat & (n[:, 2] < 0)
    h = float(s["layer"])
    shell = (float(areas[~flat].sum()) * float(s["line_width"]) * int(s["walls"])
             + float((areas[up] * np.abs(n[up, 2])).sum()) * h * int(s["top_layers"])
             + float((areas[down] * np.abs(n[down, 2])).sum()) * h * int(s["bottom_layers"]))
    shell = min(vol, shell)
    return vol, shell + float(s["infill"]) * max(0.0, vol - shell)


def support_volume(V, F, over_mask, density=0.12):
    """Stima grossolana del volume dei supporti: colonna sotto ogni faccia oltre soglia, fino al piano."""
    if not over_mask.any():
        return 0.0
    n, areas = checks.face_normals_areas(V, F)
    zc = V[:, 2][F[over_mask]].mean(axis=1)
    return float((areas[over_mask] * np.abs(n[over_mask, 2]) * zc).sum()) * density


def analyse(V, F, over=None):
    s = settings(over)
    V = np.asarray(V, dtype=float)
    lift = float(V[:, 2].min())
    V = V - np.array([0.0, 0.0, lift])
    lim = float(s["overhang_limit_deg"])
    topo = checks.topology(V, F)
    closed = bool(topo["watertight"]) and topo["bodies"] >= 1
    size = V.max(axis=0) - V.min(axis=0)
    bed = [float(b) for b in s["bed"]]
    fits = (size[2] <= bed[2] + 1e-6) and ((size[0] <= bed[0] + 1e-6 and size[1] <= bed[1] + 1e-6)
                                           or (size[1] <= bed[0] + 1e-6 and size[0] <= bed[1] + 1e-6))
    ang, areas, on_bed = checks.overhang_angles(V, F)
    free = ~on_bed & (areas > 1e-9)
    over_mask = free & (ang > lim + checks.ANGLE_TOL)
    warn_mask = free & (ang > lim - 10.0) & ~over_mask
    max_deg = float(ang[free].max()) if free.any() else 0.0
    over_area = float(areas[over_mask].sum())
    islands = len(checks.local_minima(V, F))
    contact = float(areas[on_bed].sum())
    needs_support = over_area > 1.0 or islands > 0
    sup_on = bool(s["supports"])

    rows = [("Mesh chiusa", closed, "%d spigoli aperti, %d corpi" % (topo["naked_edges"], topo["bodies"])),
            ("Ingombro", bool(fits), "%.1f x %.1f x %.1f mm (stampante %g x %g x %g)" % (tuple(size) + tuple(bed))),
            ("Sbalzi", (not over_area > 1.0) or sup_on,
             "max %.1f gradi (limite %g), %.0f mm2 oltre%s" % (max_deg, lim, over_area,
                                                              "  -> con supporti" if over_area > 1.0 and sup_on else "")),
            ("Isole", islands == 0 or sup_on, "%d partenze nel vuoto" % islands),
            ("Appoggio", contact >= 100.0, "%.0f mm2 sul piano" % contact)]
    out = {"settings": s, "lift_mm": lift, "size": [float(x) for x in size], "closed": closed, "fits": bool(fits),
           "overhang_max_deg": max_deg, "overhang_area_mm2": over_area, "islands": islands,
           "contact_mm2": contact, "needs_support": bool(needs_support),
           "over_mask": over_mask, "warn_mask": warn_mask,
           "layers": int(math.ceil(float(size[2]) / float(s["layer"])))}
    if closed:
        st = checks.stability(V, F)
        rows.append(("Stabilita", st["margin_mm"] > 0, "baricentro a %.1f mm dal bordo dell'appoggio" % st["margin_mm"]))
        vol, ext = material(V, F, s)
        sup = support_volume(V, F, over_mask) if (sup_on and needs_support) else 0.0
        rho = float(s["density_g_cm3"]) / 1000.0
        fil = math.pi * (float(s["filament_diameter"]) / 2.0) ** 2
        out.update({"volume_cm3": vol / 1000.0, "extruded_cm3": (ext + sup) / 1000.0,
                    "support_cm3": sup / 1000.0, "mass_g": (ext + sup) * rho,
                    "filament_m": (ext + sup) / fil / 1000.0,
                    "time_h": (ext + sup) / float(s["flow_mm3_s"]) / 3600.0})
    out["rows"] = rows
    out["ok"] = all(ok for _, ok, _ in rows)
    return out


def report(res):
    """Testo a larghezza fissa per il pannello."""
    L = ["%s %-12s %s" % ("OK" if ok else "NO", lab, txt) for lab, ok, txt in res["rows"]]
    L.append("")
    if res["lift_mm"] > 0.01:
        L.append("Il pezzo e sollevato di %.2f mm: qui e considerato appoggiato al piano." % res["lift_mm"])
    if "mass_g" in res:
        s = res["settings"]
        L.append("Strati %d da %g mm | pareti %d da %g mm | riempimento %d%%"
                 % (res["layers"], s["layer"], s["walls"], s["line_width"], round(100 * s["infill"])))
        L.append("Volume del solido %.1f cm3 | materiale estruso %.1f cm3" % (res["volume_cm3"], res["extruded_cm3"]))
        L.append("Peso %.1f g | filo %.1f m | tempo circa %.1f h (stime geometriche)"
                 % (res["mass_g"], res["filament_m"], res["time_h"]))
        if res.get("support_cm3", 0) > 0:
            L.append("di cui supporti circa %.1f cm3 (stima molto grossolana)" % res["support_cm3"])
    else:
        L.append("Mesh aperta: volume, peso e tempo non calcolabili. Chiudi il solido.")
    if res["needs_support"] and not res["settings"]["supports"]:
        L.append("ATTENZIONE: servono supporti (zone rosse) oppure va cambiato l'orientamento.")
    L.append("ESITO: %s" % ("STAMPABILE" if res["ok"] else "DA CORREGGERE"))
    return "\n".join(L)
