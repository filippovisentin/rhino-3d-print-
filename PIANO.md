# Generative Print: piano proposto (da confermare)

Unità: mm, g, gradi. Materiale di riferimento: PLA, densità 1,24 g/cm³ (ipotesi, da tarare sul filamento reale).

## 1. Come pilotare Rhino 8: scelta e motivo

| Via | Esito della verifica (2026-11-13) | Giudizio |
|---|---|---|
| **Rhino-MCP-Platform 0.4.0 (router McNeel)** | Pacchetto, router e config OK (doctor: 32 tool). L'avvio automatico di Rhino da qui fallisce (`startup_timeout`, nessuna finestra): serve Rhino aperto a mano con `MCPStart`. | **Scelta.** Rhino vivo e visibile, Python 3 (`run_python`), catture di vista (`get_viewport_image`), strumenti Grasshopper (`g1_*`). |
| Rhino.Compute | Non installato; richiede server locale o cloud e configurazione. | Scartato: troppa infrastruttura per una demo. |
| `Rhino.exe /nosplash /runscript` per ogni iterazione | Possibile ma con 15-30 s di avvio a ogni giro e errori difficili da leggere. | Ripiego, per una modalità batch futura. |
| rhino3dm | Solo lettura/scrittura .3dm, niente modellazione. | Non serve: le metriche si calcolano sulla mesh. |

**Separazione dei compiti:** Rhino **genera e fotografa**; un modulo Python esterno (3.12 + `trimesh` + `numpy`) **misura** sul file STL. Le metriche non dipendono da Rhino, si possono testare senza di esso e diventano una "seconda opinione" indipendente su ciò che Rhino esporta.

## 2. Architettura del ciclo

```
brief.txt ──► agente (Claude) ──► params.json
                 ▲                    │
                 │             [Rhino 8] generatore Python 3 (anche in un componente GH)
                 │                    │ ── model.stl + 4 viste PNG
                 │             [Python] verify.py ── metrics.json (pass/fail per vincolo)
                 │                    │
                 └── critique.md ◄── agente legge viste + metriche, motiva i nuovi parametri
                                      │
                         stop: vincoli OK e voto estetico ≥ soglia, oppure N max iterazioni
                                      ▼
                    final.stl / final.3mf + scheda + journal.html
```

- **Generatore** (`generators/lamp.py`, `generators/vase.py`): una funzione pura `build(params) -> Mesh`, che crea direttamente una mesh chiusa (parete esterna modulata, parete interna offset, bordo, fondo e sede del portalampada). Lo stesso file è caricato **sia** dal componente Python 3 di una definizione `.gh` (con slider, per l'uso manuale del designer) **sia** dall'agente tramite `run_python`. Una sola fonte di verità.
- **Forma base:** il profilo di rivoluzione r(z) (curva di Bézier/spline a pochi punti di controllo) è modulato da nervature: r(z,θ) = r(z)·(1 + a(z)·f(n·(θ + torsione·z))). Lo sbalzo dipende in modo analitico da dr/dz e dalla torsione, quindi l'agente può "ragionarci sopra".
- **Viste fisse:** fronte, 3/4 dall'alto, dall'alto e sezione, in modalità Arctic o Rendered, a sfondo e luce costanti, così le iterazioni sono confrontabili.
- **Critica estetica:** la fa l'agente stesso, guardando le immagini, con una rubrica fissa (proporzioni, ritmo delle nervature, coerenza col brief, qualità della silhouette, voto 1-10) salvata in `critique.md`. Nessuna chiave API richiesta. Una modalità autonoma via API Anthropic resta un'estensione possibile.

## 3. Formato JSON dei parametri (esempio lampada)

```json
{
  "object": "lamp_shade",
  "brief": "lampada da tavolo, diffusore a nervature torte, Ø 18 cm, PLA senza supporti, max 250 g",
  "units": "mm",
  "envelope": { "height": 220, "max_diameter": 180, "base_diameter": 120, "top_diameter": 90 },
  "profile": { "control_radii": [60, 88, 90, 70, 45], "bulge_z": 0.45 },
  "ribs": { "count": 24, "amplitude": 0.06, "twist_deg": 90, "shape": "sine", "fade_bottom": 0.1 },
  "wall": { "thickness": 1.6 },
  "fixture": { "socket": "E27", "seat_diameter": 40.5, "seat_ring_width": 12, "cable_hole": 8 },
  "thermal": { "top_opening_diameter": 60, "min_bulb_clearance": 30 },
  "print": { "material": "PLA", "density_g_cm3": 1.24, "nozzle": 0.4, "layer": 0.2,
             "bed": [256, 256, 256], "overhang_limit_deg": 45 }
}
```

I vincoli (sbalzo massimo, spessore minimo, peso massimo, piano di stampa) stanno in `constraints` nel brief compilato. L'agente cambia solo i parametri "liberi" e scrive il motivo di ogni modifica.

## 4. Metriche di verifica (`verify.py`, sul file STL)

| Metrica | Metodo | Soglia predefinita |
|---|---|---|
| Mesh chiusa / manifold | `trimesh.is_watertight`, `is_winding_consistent`, nessun bordo libero | obbligatorio |
| Sbalzo massimo | angolo tra normale delle facce rivolte verso il basso e la verticale; area oltre soglia, escluse le facce del piano di appoggio (z = 0) | ≤ 45° (configurabile); area oltre soglia = 0 |
| Spessore minimo | raggi lanciati dall'interno lungo −normale (ray casting), percentili p1/p5 | ≥ 2 × ugello (0,8 mm), consigliato ≥ 1,2 mm |
| Volume e peso | `mesh.volume` × densità (pieno, sovrastima); stima con perimetri e riempimento come seconda colonna | ≤ peso massimo del brief |
| Ingombro | bounding box rispetto al piano di stampa | dentro al piano |
| Stabilità | baricentro dentro l'impronta d'appoggio | obbligatorio |
| Portalampada (lampade) | diametro della sede, spazio libero lampadina-parete, apertura superiore per i moti convettivi | E27 / E14 da valori di catalogo verificati con 2 fonti |

Il PLA si deforma intorno ai 55-60 °C: per le lampade il brief impone **LED** (dichiarato come ipotesi nella scheda) e una distanza minima lampadina-parete.

## 5. Struttura del repository

```
generative-print/
  README.md            LICENSE (MIT)        pyproject.toml
  generators/          lamp.py, vase.py, common.py   (Python 3, girano dentro Rhino 8)
  grasshopper/         generative_print.gh            (componente Python 3 che importa generators/)
  verify/              verify.py, metrics.py         (trimesh, gira fuori da Rhino)
  loop/                run_iteration.py (orchestrazione), capture.py, journal.py
  briefs/              lamp_twisted_ribs.json
  runs/<data>_<nome>/  iter_01/{params.json, model.stl, metrics.json, views/*.png, critique.md}
                       final/{model.stl, model.3mf, scheda.md}  journal.html
  tests/               test_metrics.py (casi noti: cubo, cilindro, cono a 30°/60°, parete sottile)
```

## 6. Fasi e criteri di "fatto"

1. Scaffold del repository, venv, `verify.py` con test sui casi noti (prima le misure, poi la forma).
2. Generatore lampada in Rhino via MCP, esportazione STL e 4 viste: un giro manuale.
3. Ciclo completo: un brief, da 4 a 6 iterazioni registrate e un diario HTML.
4. Componente/definizione Grasshopper con slider (per l'uso interattivo).
5. README, licenza e consegna. Il vaso è un secondo generatore, se resta tempo.

**Fatto quando:** l'STL finale è chiuso, nessuna faccia supera lo sbalzo consentito (escluso il piano di appoggio), lo spessore p1 rispetta il minimo, il peso stimato rispetta il limite e il diario HTML contiene tutte le iterazioni. Ogni numero viene da `metrics.json` eseguiti, non da stime a voce.

## Prerequisito per l'utente
Aprire Rhino 8 a mano e digitare `MCPStart`. Consigliato: attivare *Load MCP server at startup* nelle impostazioni RhinoAI.
