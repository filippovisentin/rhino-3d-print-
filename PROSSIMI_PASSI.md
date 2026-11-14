# Prossimi passi

Scritto il 2026-11-14. Obiettivo: lo slicer dentro Rhino scrive da solo il **G-code pronto da stampare** per la Bambu Lab A1 mini, senza usare Bambu Studio. Il pulsante «Affetta con Bambu» resta solo come confronto facoltativo.

Per lo stato completo e le prove già fatte: `RIPRESA.md`. Per le decisioni stabili: `CLAUDE.md`.

## Dove siamo

| Parte | Stato |
|---|---|
| Analisi di stampa, mappa degli sbalzi, orientamento | fatto |
| Supporti (griglia, aderenti, albero, solo dal piano, pittura manuale) | fatto, come anteprima e stima |
| Pannelli agganciati accanto ai Livelli, alias `GPSlicer` e `GPEditor` | fatto |
| Modifica plastica (pennelli, maschera, annulla/ripeti) | fatto, non provata col mouse |
| Avvio e fine stampa della A1 mini (`generative_print/machine.py`) | fatto e provato sui profili veri |
| Percorsi veri di tutti gli strati | **da fare** |
| Scrittura del G-code | **da fare** |
| Prima stampa vera | **da fare** |

## Passo 1 — Scrittura del G-code (nucleo, fuori da Rhino)

File nuovo: `generative_print/gcode.py`, solo numpy, con i suoi test in `tests/test_gcode.py`.

Ingresso: una lista di strati; ogni strato ha la quota `z`, l'altezza `h` e i percorsi, ciascuno con un tipo (`outer`, `inner`, `solid`, `sparse`, `support`, `interface`), i punti in mm e se è chiuso.

Cosa deve fare:
- ordine di stampa in ogni strato: supporti, pareti interne, parete esterna, pieno, riempimento; dentro ogni tipo, il percorso più vicino per primo;
- estrusione relativa (`M83`); sezione della linea = (larghezza − h) × h + π × (h/2)²;
- ritrazione 0,8 mm con sollevamento di 0,4 mm sui trasferimenti oltre 1,5 mm;
- velocità per tipo, con tetto di portata in mm³/s preso dal filamento (`machine.filament_values`); primo strato lento;
- ventola dal secondo strato, temperature dal profilo del filamento;
- a ogni strato: `M73` (avanzamento) e il modello del cambio strato compilato con `machine.render`;
- intestazione `HEADER_BLOCK` con numero di strati, tempo e peso;
- avvio e fine presi da `machine.start_end(...)`, con l'ingombro del primo strato sul piatto;
- stima del tempo dai percorsi (lunghezza / velocità più accelerazioni).

Controllo da scrivere insieme (`gcode.check`): rilegge il testo e verifica che tutto stia dentro il piatto (180 × 180 × 180 mm), che l'estrusione totale coincida con il calcolo geometrico e che non ci sia estrusione in aria.

Fatto quando: un cubo di prova dà un peso entro pochi punti percentuali dal calcolo a mano e il controllo passa.

## Passo 2 — Percorsi veri in Rhino

In `rhino/gp_slicer.py`:
- tagliare tutti gli strati in una sola chiamata di `MeshPlane` (oggi uno strato alla volta: 0,2–0,4 s sulla lampada);
- pareti: ci sono già in `_walls`;
- strati pieni locali: usare il campionamento verticale `supports.ldi`; una cella è piena se sopra o sotto ha meno di N strati di materiale (serve una funzione `above` gemella di `supports.below`);
- riempimento: `slicer.hatch`, accorciato di 0,35 linee ai capi, diviso in pieno e rado secondo la maschera;
- supporti: `supports.layer_pattern` (normali) e `supports.tree_sections` (albero) diventano percorsi;
- pezzo centrato sul piatto;
- pulsante «Genera G-code» con barra di avanzamento, Esc per interrompere, e nel rapporto peso e tempo calcolati dal G-code.

Fatto quando: la staffa di prova produce un file completo e l'anteprima a strati mostra gli stessi percorsi che finiscono nel file.

## Passo 3 — Confronto e prima stampa

1. Stesso pezzo con Bambu Studio e con il nostro G-code: confrontare peso, tempo e numero di strati.
2. Verificare che la A1 mini accetti un `.gcode` semplice dalla microSD (non ancora controllato).
3. Prima stampa **sorvegliata** di un pezzo piccolo (cubo 20 mm), con la mano sul pulsante di arresto: l'avvio è quello ufficiale, ma i percorsi sono nostri e non sono mai stati stampati.
4. Poi la staffa con i supporti.

## Dopo

- Correggere `bambu.filament_profile`: il PETG per la A1 mini esiste (nome con «0.4 nozzle») ma oggi non viene trovato.
- Togliere da `printcheck.py` le vecchie funzioni `support_columns` e `support_volume`, non più usate.
- Supporti: distanza orizzontale dal pezzo.
- Modifica plastica: prova col mouse, suddivisione dove serve dettaglio, pennello «Correggi sbalzi».
- Aggiornare o togliere `PIANO.md` (descrive il piano iniziale, superato).

## Cose da ricordare

- Bambu Studio deve restare installato: avvio e fine stampa si leggono dai suoi profili (`C:\Program Files\Bambu Studio\resources\profiles\BBL`). Non copiarli nel progetto: sono sotto la licenza di Bambu.
- Il G-code prodotto dalla riga di comando di Bambu **non è stampabile** (avvio generico, spurgo fuori dal piatto): vale solo per i confronti.
- Rhino si avvia con `Rhino.exe /nosplash /runscript="_MCPStart"`.
- Test: `.venv/Scripts/python -m pytest -q tests` (63 test).
- I file lunghi con triple virgolette vanno scritti su file e poi eseguiti: la shell non li accetta in linea.
