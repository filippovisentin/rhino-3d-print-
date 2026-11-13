# Generative Print

Generative design per la stampa 3D dentro Rhino 8: un agente AI genera lampade e vasi da un brief, li misura, li critica e li corregge; il designer li rifinisce a mano con un editor plastico a pennelli che controlla la stampabilità mentre si modella.

| Lampada a nervature torte | Vaso esagonale in spirale |
|---|---|
| ![Lampada](runs/2026-11-13_lampada-nervature/iter_08/views/persp.png) | ![Vaso](runs/2026-11-13_vaso-esagonale/iter_03/views/persp.png) |

## Cosa c'è

- **Due generatori**: lampade (diffusore con foro per portalampada E27) e vasi (sezione circolare, a lobi o poligonale con spigoli raccordati, che può ruotare lungo l'altezza; fondo pieno; capienza in ml; parete vera oppure modalità spirale degli slicer, con controllo di un solo contorno per strato).
- **Ciclo chiuso** genera → verifica → critica → correggi, con un diario HTML di ogni iterazione: [`runs/2026-11-13_lampada-nervature/journal.html`](runs/2026-11-13_lampada-nervature/journal.html).
- **Editor plastico in Rhino 8** (`rhino/avvia_editor.py`): pennelli Tira/Spingi, Gonfia, Liscia, Appiattisci, Pizzica, Afferra; simmetria radiale; mappa degli sbalzi in tempo reale (giallo vicino alla soglia, rosso oltre); anteprima a strati; verifica di stampa ed esportazione STL dal pannello.
- **Definizione Grasshopper** (`grasshopper/generative_print.gh`): 20 slider per profilo, nervature, parete, fondo e portalampada; sbalzo, peso e diametro si aggiornano a ogni movimento (circa 0,9 s per una forma nuova con maglia da 2,5 mm), zone colorate in arancio (vicino alla soglia) e rosso (oltre), anteprima a strati, verifica completa, esportazione STL e un pulsante che porta la forma nell'editor plastico.
- **Verifiche di stampa** (`generative_print/checks.py`): mesh chiusa, sbalzi, isole, spessore minimo, peso, ingombro, appoggio, stabilità, sede del portalampada. Funzionano su qualunque STL.

## Demo

**Lampada.** Brief: *lampada da tavolo, diffusore a nervature torte, Ø 18 cm, PLA senza supporti, max 250 g.*
Otto iterazioni: cinque parametriche e tre di modellazione plastica. Oggetto finale: 226,0 g, sbalzo massimo 44,5°, spessore 1,27 mm, Ø 181,7 mm. File in [`runs/2026-11-13_lampada-nervature/final/`](runs/2026-11-13_lampada-nervature/final/) (STL, 3MF, scheda); [diario](runs/2026-11-13_lampada-nervature/journal.html).

**Vaso.** Brief: *vaso per fiori secchi a sezione esagonale torta, alto 20 cm, almeno 1,5 litri, PLA in modalità spirale, senza supporti, max 100 g.*
Tre iterazioni. Nella seconda la vista dall'alto ha mostrato che la sezione "morbida" non può avere lati diritti: il generatore è stato esteso con il poligono raccordato. Oggetto finale: 65,1 g, sbalzo massimo 36,4°, capienza 1725 ml, un solo contorno a ogni mm di altezza. File in [`runs/2026-11-13_vaso-esagonale/final/`](runs/2026-11-13_vaso-esagonale/final/); [diario](runs/2026-11-13_vaso-esagonale/journal.html).

## Uso

```bash
python -m venv .venv
.venv/Scripts/python -m pip install numpy trimesh rtree networkx lxml pytest
.venv/Scripts/python -m pytest -q tests
.venv/Scripts/python -m generative_print.verify modello.stl --params params.json
.venv/Scripts/python -m generative_print.journal runs/2026-11-13_lampada-nervature
.venv/Scripts/python -m generative_print.deliver runs/2026-11-13_vaso-esagonale 3 vaso-esagonale
```

Grasshopper: apri `grasshopper/generative_print.gh`. Il file trova da solo la cartella del progetto (deve restare in `grasshopper/`); se lo sposti, collega il percorso del progetto all'ingresso `cartella`. La definizione si rigenera con `grasshopper/build_definition.py`.

![Definizione Grasshopper](grasshopper/canvas.png)

Editor: in Rhino 8 esegui `_ScriptEditor`, apri `rhino/avvia_editor.py` e premi F5. Lavora sulla mesh selezionata, sulla pelle generata o su una forma libera nuova. Trascina col tasto sinistro per scolpire, Ctrl per scavare, Shift per lisciare, tasto destro per ruotare la vista.

Ciclo guidato dall'agente: serve Rhino 8 con il pacchetto Rhino-MCP-Platform e il comando `MCPStart`; l'agente chiama `gp_rhino.step(doc, cartella_corsa, n, modifiche)`.

## Struttura

- `generative_print/`: nucleo in solo numpy (gira anche nel Python 3.9 di Rhino): `generators`, `checks`, `sculpt`, `slicer`, `mesh_io`, `verify`, `journal`, `deliver`.
- `rhino/`: ponte con Rhino (`gp_rhino.py`), editor (`gp_editor.py`), calcolo del componente Grasshopper (`gp_gh.py`).
- `grasshopper/`: definizione `.gh`, script del componente e script che la costruisce.
- `tests/`: 31 test delle misure su solidi dal risultato noto (cubi, coni, cilindri, poligoni esatti).
- `runs/`: una cartella per corsa, con parametri, STL, metriche, viste e note di ogni iterazione.

## Limiti dichiarati

- L'oggetto della demo non è stato stampato né aperto in uno slicer. Tempo e filo sono stime grossolane; la densità del PLA (1,24 g/cm³) è ipotizzata.
- La critica estetica è un giudizio del modello sulle viste, non una misura.
- Nelle iterazioni 6-8 le pennellate sono state applicate dall'agente tramite script; il mouse è stato provato a parte.
- I pennelli spostano i vertici senza cambiare la topologia: niente suddivisione dinamica.
- Lampade: solo sorgenti LED.
- Vasi in spirale: la parete singola non è a tenuta d'acqua garantita; la rigidità della linea da 0,7 mm è una scelta d'esperienza, non calcolata.

## Licenza

MIT.
