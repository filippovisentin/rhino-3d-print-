# Prossimi passi

Scritto il 2026-11-14, aggiornato il 2026-11-15. Obiettivo: lo slicer dentro Rhino scrive da solo il **G-code pronto da stampare** per la Bambu Lab A1 mini, senza usare Bambu Studio. Il pulsante «Affetta con Bambu» resta solo come confronto facoltativo.

Per lo stato completo e le prove già fatte: `RIPRESA.md`. Per le decisioni stabili: `CLAUDE.md`.

## Dove siamo

| Parte | Stato |
|---|---|
| Analisi di stampa, mappa degli sbalzi, orientamento | fatto |
| Supporti (griglia, aderenti, albero, solo dal piano, pittura manuale) | fatto, come anteprima e stima |
| Pannelli agganciati accanto ai Livelli, alias `GPSlicer` e `GPEditor` | fatto |
| Modifica plastica (pennelli, maschera, annulla/ripeti) | fatto, non provata col mouse |
| Avvio e fine stampa della A1 mini (`generative_print/machine.py`) | fatto e provato sui profili veri |
| Scrittura del G-code (`generative_print/gcode.py`) e suo controllo | fatto, provato su un cubo |
| Interfaccia dei pannelli in inglese | fatto |
| Percorsi veri di tutti gli strati e pulsante «Generate G-code» | fatto, provato in Rhino sul tavolo di prova |
| Confronto con Bambu sui percorsi veri | **da fare** |
| Prima stampa vera | **da fare** |

## Passo 1 — Scrittura del G-code (nucleo, fuori da Rhino) — FATTO il 2026-11-15

Risultato: cubo pieno da 20 mm con i profili veri della A1 mini (PLA): 9,69 g contro 9,88 g calcolati a mano (-1,9 %), 100 strati, controllo superato. Come si usa dal passo 2:
- `gcode.generate(strati, stampante, materiale, impostazioni)` restituisce testo, peso, tempo e l'esito di `gcode.check`;
- le linee affiancate vanno messe a distanza `gcode.spacing(larghezza, h)` (0,407 mm per 0,45 / 0,2), non alla larghezza: cosi il pieno e pieno davvero;
- i punti dei percorsi sono gia nelle coordinate del piatto (pezzo centrato su 90, 90);
- il controllo guarda solo il nostro corpo (tra `; GP_BODY_START` e `; GP_BODY_END`): l'avvio ufficiale esce apposta dal piatto;
- non ancora fatti: giunzione delle righe di riempimento con estrusione, pulitura a fine percorso (wipe), posizione scelta della cucitura, rallentamento per strati piccoli. Il tempo e una stima (non conta i rallentamenti in curva).

Descrizione originale del passo:

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

## Passo 2 — Percorsi veri in Rhino — FATTO il 2026-11-15

Risultato su `prova_tavolo`: 175 strati scritti in 1-3 s, controllo superato; supporti aderenti 34,6 g, ad albero 20,0 g. L'anteprima a strati usa `Slicer.layer_paths`, la stessa funzione del file (pieno in viola, rado in azzurro).

Limiti noti, da tenere presenti alla prima stampa:
- una parete interna che in un punto si avvicina troppo a un'altra viene scartata tutta (resta un vuoto tra le pareti): manca il riempimento dei vuoti sottili;
- la cucitura della parete esterna cade sul vertice piu vicino all'ugello, non in un punto scelto;
- gli strati pieni si decidono su una griglia da 0,5 mm (cresce sui pezzi grandi): il bordo tra pieno e rado e a scalini;
- niente ponti, niente rallentamento sugli sbalzi o sugli strati piccoli, niente pulitura a fine percorso;
- i supporti si stampano alla larghezza piena della linea e toccano il pezzo di lato (nessuna distanza orizzontale);
- mai provato su un pezzo grande (la lampada non entra nella A1 mini) ne interrotto con Esc durante la scrittura.

Descrizione originale del passo:

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

1. Stesso pezzo con Bambu Studio e con il nostro G-code: confrontare peso, tempo e numero di strati. Da spiegare subito: sul tavolo con l'albero Bambu dava 28,6 g e 1 h 45, noi 20,0 g e 44 min (piu 6 di avvio). Confrontare anche senza supporti, per separare il pezzo dai supporti. Aprire il nostro `.gcode` nell'anteprima di Bambu Studio per guardarlo strato per strato.
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
- Test: `.venv/Scripts/python -m pytest -q tests` (74 test).
- Interfaccia dei pannelli in inglese; documenti e commenti in italiano.
- I file lunghi con triple virgolette vanno scritti su file e poi eseguiti: la shell non li accetta in linea.
