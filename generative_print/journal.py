"""Diario di progetto: pagina HTML con immagini, metriche e motivazioni di ogni iterazione.

Uso:  python -m generative_print.journal runs/<corsa>
Legge brief.json e, per ogni iter_NN, params.json, metrics.json, notes.json e views/*.png.
Tutti i numeri della pagina vengono da metrics.json: niente e scritto a mano.
"""
import argparse
import html
import json
import os

CSS = """
:root{--bg:#f6f3ee;--card:#fffdf9;--ink:#1f1d1a;--mute:#6b665e;--line:#ddd6ca;--acc:#b4532a;
--ok:#2f6f4f;--ko:#b3261e;--okbg:#e4f0e8;--kobg:#f8e3e0}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--card:#201e1b;--ink:#ece7df;--mute:#a39c90;
--line:#3a362f;--acc:#e08a5c;--ok:#7fc79f;--ko:#f0958c;--okbg:#1d3327;--kobg:#3d1f1c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 "Segoe UI",system-ui,sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 16px 80px}
h1{font:600 40px/1.1 Georgia,serif;margin:0 0 8px;letter-spacing:-.01em}
h2{font:600 26px/1.2 Georgia,serif;margin:0}h3{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:var(--mute);margin:20px 0 6px}
.kicker{color:var(--acc);font-weight:600;font-size:13px;letter-spacing:.1em;text-transform:uppercase}
.brief{font:italic 20px/1.45 Georgia,serif;border-left:3px solid var(--acc);padding:4px 0 4px 16px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:24px;margin:28px 0}
.head{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
.num{font:600 15px/1 Consolas,monospace;color:var(--acc)}
.badge{font-size:13px;font-weight:600;padding:3px 10px;border-radius:99px;margin-left:auto}
.ok{color:var(--ok);background:var(--okbg)}.ko{color:var(--ko);background:var(--kobg)}
.views{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:18px 0}
figure{margin:0}figure img{width:100%;display:block;border-radius:6px;border:1px solid var(--line);background:#fff}
figcaption{font-size:12px;color:var(--mute);margin-top:4px}
.text{max-width:72ch}
@media (max-width:760px){h1{font-size:30px}}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mute);font-weight:600}
td.v{font-family:Consolas,monospace;white-space:nowrap}
.mark{font-weight:700;white-space:nowrap}.mark.ok,.mark.ko{background:none}
.note{font-size:12px;color:var(--mute)}
ul{margin:6px 0;padding-left:18px}li{margin:3px 0}
code{font-family:Consolas,monospace;font-size:13px;background:var(--bg);padding:1px 5px;border-radius:4px}
.scores{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--mute)}
.scores b{color:var(--ink);font-family:Consolas,monospace}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.chart b{font-size:13px}.chart svg{width:100%;height:auto;display:block;margin-top:6px}
.tablewrap{overflow-x:auto}
footer{color:var(--mute);font-size:13px;margin-top:40px}
"""

VIEW_LABELS = [("persp", "Tre quarti"), ("front", "Fronte"), ("section", "Sezione"), ("top", "Dall'alto"),
               ("editor", "Editor: mappa degli sbalzi")]


def esc(x):
    return html.escape(str(x))


def fmt(v):
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".").replace(".", ",")
    if isinstance(v, list):
        return " × ".join(fmt(x) for x in v)
    return str(v)


def load_run(run_dir):
    with open(os.path.join(run_dir, "brief.json"), encoding="utf-8") as fh:
        brief = json.load(fh)
    iters = []
    for name in sorted(os.listdir(run_dir)):
        d = os.path.join(run_dir, name)
        if not (name.startswith("iter_") and os.path.isdir(d)):
            continue
        item = {"name": name, "n": int(name.split("_")[1])}
        for key in ("params", "metrics", "notes"):
            p = os.path.join(d, key + ".json")
            item[key] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
        item["views"] = [(k, lab, "%s/views/%s.png" % (name, k)) for k, lab in VIEW_LABELS
                         if os.path.exists(os.path.join(d, "views", k + ".png"))]
        iters.append(item)
    return brief, iters


def check_value(it, cid):
    for c in it["metrics"].get("checks", []):
        if c["id"] == cid:
            return c
    return None


def chart(title, unit, points, lo=None, hi=None):
    """Piccolo grafico SVG: un punto per iterazione, limiti tratteggiati.
    points: lista di (iterazione, valore, ok). Cerchio pieno = dentro i limiti, vuoto = fuori."""
    pts = [p for p in points if p[1] is not None]
    if not pts:
        return ""
    w, h, ml, mr, mt, mb = 300, 150, 40, 12, 12, 24
    vals = [p[1] for p in pts] + [x for x in (lo, hi) if x is not None]
    vmin, vmax = min(vals), max(vals)
    pad = (vmax - vmin) * 0.15 or 1.0
    vmin, vmax = vmin - pad, vmax + pad
    n0, n1 = pts[0][0], pts[-1][0]

    def X(n):
        return ml + (w - ml - mr) * ((n - n0) / float(max(1, n1 - n0)))

    def Y(v):
        return mt + (h - mt - mb) * (1 - (v - vmin) / (vmax - vmin))
    out = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s">' % (w, h, esc(title))]
    for lim in (lo, hi):
        if lim is not None:
            out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="var(--ko)" stroke-dasharray="4 3"/>'
                       % (ml, w - mr, Y(lim), Y(lim)))
            out.append('<text x="%d" y="%.1f" font-size="10" fill="var(--ko)" text-anchor="end">%s</text>'
                       % (ml - 4, Y(lim) + 3, fmt(float(lim))))
    out.append('<polyline fill="none" stroke="var(--mute)" stroke-width="1.5" points="%s"/>'
               % " ".join("%.1f,%.1f" % (X(n), Y(v)) for n, v, _ in pts))
    for n, v, ok in pts:
        out.append('<circle cx="%.1f" cy="%.1f" r="4.5" stroke="%s" stroke-width="2" fill="%s"><title>'
                   'Iterazione %d: %s %s</title></circle>'
                   % (X(n), Y(v), "var(--ok)" if ok else "var(--ko)", "var(--ok)" if ok else "var(--card)",
                      n, fmt(float(v)), esc(unit)))
        out.append('<text x="%.1f" y="%d" font-size="10" fill="var(--mute)" text-anchor="middle">%d</text>'
                   % (X(n), h - 6, n))
    last = pts[-1]
    out.append('<text x="%.1f" y="%.1f" font-size="11" fill="var(--ink)" text-anchor="end">%s</text>'
               % (X(last[0]) - 2, Y(last[1]) - 9, fmt(float(last[1]))))
    out.append("</svg>")
    return '<div class="chart"><b>%s</b> <span class="note">%s</span>%s</div>' % (esc(title), esc(unit), "".join(out))


def summary_charts(brief, iters):
    def series(cid, key=None):
        out = []
        for it in iters:
            c = check_value(it, cid)
            v = None if c is None else (c["value"] if key is None else key(it))
            out.append((it["n"], v, bool(c and c["pass"])))
        return out
    cons = iters[-1]["params"].get("constraints", {})
    lim = iters[-1]["params"].get("print", {}).get("overhang_limit_deg", 45.0)
    thr = brief.get("aesthetic_threshold")
    scores = [(it["n"], it["notes"].get("critique", {}).get("score"),
               (it["notes"].get("critique", {}).get("score") or 0) >= (thr or 0)) for it in iters]
    td = cons.get("target_diameter")
    tol = cons.get("diameter_tolerance", 3.0)
    parts = [chart("Peso stimato", "g", series("mass"), hi=cons.get("max_mass_g")),
             chart("Sbalzo massimo", "gradi", series("overhang"), hi=lim),
             chart("Diametro", "mm", series("diameter"), lo=td - tol if td else None, hi=td + tol if td else None),
             chart("Spessore (1° percentile)", "mm", series("wall"), lo=cons.get("min_wall")),
             chart("Voto estetico", "su 10", scores, lo=thr)]
    return '<div class="charts">%s</div><p class="note">Cerchio pieno: dentro i limiti. Cerchio vuoto: fuori. ' \
           'Linee tratteggiate: limiti del brief. In ascissa il numero di iterazione.</p>' % "".join(parts)


def iteration_card(it):
    m, notes = it["metrics"], it["notes"]
    ok = bool(m.get("pass"))
    rows = []
    for c in m.get("checks", []):
        rows.append('<tr><td class="mark %s">%s</td><td>%s<div class="note">%s</div></td>'
                    '<td class="v">%s %s</td><td class="v">%s</td></tr>'
                    % ("ok" if c["pass"] else "ko", "✓ ok" if c["pass"] else "✕ no", esc(c["label"]),
                       esc(c.get("note", "")), esc(fmt(c["value"])), esc(c["unit"]), esc(c["limit"])))
    changes = "".join("<li><code>%s</code>: %s → <b>%s</b>. %s</li>"
                      % (esc(c["param"]), esc(fmt(c["from"])), esc(fmt(c["to"])), esc(c["why"]))
                      for c in notes.get("changes", []))
    cr = notes.get("critique", {})
    scores = "".join("<span>%s <b>%s</b></span>" % (lab, fmt(float(cr[k]))) for k, lab in
                     (("proportions", "Proporzioni"), ("rhythm", "Ritmo"), ("brief_fit", "Coerenza col brief"),
                      ("silhouette", "Sagoma"), ("score", "Voto")) if k in cr)
    views = "".join('<figure><a href="%s"><img loading="lazy" src="%s" alt="%s, iterazione %d"></a>'
                    '<figcaption>%s</figcaption></figure>' % (src, src, esc(lab), it["n"], esc(lab))
                    for _, lab, src in it["views"])
    mat = m.get("material", {})
    matline = ("%s cm³ · %s g · %s m di filo · %d strati" % (fmt(round(mat.get("volume_cm3", 0), 1)),
               fmt(round(mat.get("mass_est_g", 0), 1)), fmt(round(mat.get("filament_m", 0), 1)),
               m.get("layers", 0))) if mat else ""
    return """<section class="card" id="%s">
<div class="head"><span class="num">ITERAZIONE %02d</span><h2>%s</h2>
<span class="badge %s">%s</span></div>
<div class="views">%s</div>
<div class="text">
%s
<h3>Cosa dicono le misure</h3><p>%s</p>
<h3>Critica estetica</h3><p>%s</p><div class="scores">%s</div>
<h3>Decisione</h3><p>%s</p>
</div>
<h3>Verifica di stampa</h3>
<div class="tablewrap"><table><tr><th></th><th>Controllo</th><th>Misura</th><th>Limite</th></tr>%s</table></div>
<p class="note">%s</p>
</section>""" % (it["name"], it["n"], esc(notes.get("title", "")), "ok" if ok else "ko",
                              "✓ stampabile" if ok else "✕ da correggere", views,
                              ("<h3>Modifiche rispetto alla precedente</h3><ul>%s</ul>" % changes) if changes else "",
                              esc(notes.get("reasoning", "")), esc(cr.get("text", "")), scores,
                              esc(notes.get("decision", "")), "".join(rows), esc(matline))


def build(run_dir, extra_html=""):
    brief, iters = load_run(run_dir)
    last = iters[-1]
    n_ok = sum(1 for it in iters if it["metrics"].get("pass"))
    body = ['<p class="kicker">Generative Print · diario di progetto</p>',
            "<h1>%s</h1>" % esc(brief.get("title", "Diario delle iterazioni")),
            '<p class="brief">%s</p>' % esc(brief["brief"]),
            "<p>%d iterazioni registrate, %d stampabili secondo le verifiche. Ciclo: genera in Rhino 8, "
            "misura l'STL esportato, critica le viste, correggi motivando. Le iterazioni finali sono "
            "modellate con i pennelli dell'editor plastico.</p>" % (len(iters), n_ok),
            summary_charts(brief, iters)]
    body += [iteration_card(it) for it in iters]
    body.append(extra_html)
    body.append("<footer>Generato da <code>python -m generative_print.journal</code>. Tutte le misure vengono "
                "dai file <code>metrics.json</code> di ogni iterazione. Oggetto finale: <code>%s</code>.</footer>"
                % esc(last["name"]))
    page = ('<!doctype html><html lang="it"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Generative Print: diario</title><style>%s</style></head><body><main>%s</main></body></html>"
            % (CSS, "\n".join(body)))
    out = os.path.join(run_dir, "journal.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Genera il diario HTML di una corsa")
    ap.add_argument("run_dir")
    a = ap.parse_args(argv)
    extra = ""
    p = os.path.join(a.run_dir, "method.html")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            extra = fh.read()
    print(build(a.run_dir, extra))


if __name__ == "__main__":
    main()
