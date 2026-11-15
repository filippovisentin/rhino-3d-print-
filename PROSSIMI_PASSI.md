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
| Confronto con Bambu sui percorsi veri | fatto sul tavolo di prova (tre varianti) |
| Rallentamento degli strati brevi (raffreddamento) | fatto |
| Prove su pezzi tecnici e oggetti veri (sei pezzi) | fatto, difetti trovati e corretti |
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
- (corretto il 2026-11-15, vedi 3.1 bis) una parete interna che in un punto si avvicinava troppo a un'altra veniva scartata tutta;
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

### 3.1 Confronto con Bambu — FATTO il 2026-11-15

Pezzo: `prova_tavolo` (60 x 60 x 35 mm), A1 mini, PLA, 0,2 mm, 2 pareti, 15 %. Sei file in `export/confronto_tavolo/` (cartella non pubblicata). Pesi alla densita del profilo (1,26). Tempo = sola stampa, senza avvio.

| Variante | Noi | Bambu | Scarto |
|---|---|---|---|
| Senza supporti | 15,7 g, 31 min, 175 strati | 16,7 g, 35 min, 175 strati | -6 % peso, -11 % tempo |
| Albero | 23,5 g, 50 min | 28,6 g, 1 h 46 | -18 % peso, meta del tempo |
| Aderenti | 37,2 g, 57 min | 36,3 g, 53 min (280 quote: i supporti hanno strati propri) | +2 % peso, +8 % tempo |

Da dove vengono le differenze (misurate tratto per tratto sui G-code):
- **pareti**: uguali (noi 2,92 g, Bambu 2,89 g);
- **pieno**: noi 9,97 g, Bambu 10,47 g. Bambu stampa il primo strato sopra il vuoto come ponte (18,8 m a 50 mm/s con piu materiale: sezione 0,101 mm2 contro 0,080) e i bordi a sbalzo a 10 mm/s; noi come pieno normale a 150 mm/s;
- **rado**: noi 2,85 g, Bambu 3,21 g (trama diversa);
- **spurgo**: Bambu conta 0,14 g di linea di spurgo, noi no (sta nell'avvio ufficiale);
- **albero**: Bambu mette 11,9 g di supporto (120 m piu 7,9 m di interfaccia), noi 7,7 g (76 m, senza interfaccia: solo punte ogni 5 mm). Il suo tempo e quasi tutto spostamenti: 98 m a vuoto e 9.737 ritrazioni (noi 65 m e 1.660);
- **aderenti**: noi 21,4 g di supporto, Bambu 19,6 g;
- **tempo senza supporti**: prima del confronto davamo 22 min contro 35. Mancava il rallentamento degli strati brevi: sulla gamba 10 x 10 Bambu scende a 20-50 mm/s. Ora c'e (`gcode.write`: uno strato dura almeno `slow_down_layer_time` del filamento, 6 s per il PLA, senza scendere sotto 20 mm/s; primo strato escluso): 130 strati rallentati, 31 min.

Il tavolo rifatto oggi pesa 23,5 g con l'albero, non i 20,0 g di ieri: la maglia e diversa (oggi tre scatole da script, ieri una polisuperficie convertita a triangoli da 2 mm) e le punte dell'albero cadono in altri punti.

Non fatto: aprire il nostro `.gcode` nell'anteprima di Bambu Studio (va fatto a mano: File, Importa, G-code).

Resta diverso da Bambu, da sapere alla prima stampa: niente ponti (il primo strato sopra i supporti va a piena velocita), niente rallentamento sugli sbalzi, ventola fissa all'80 % dal secondo strato.

### 3.1 bis Prove su pezzi veri — FATTO il 2026-11-15

Sei pezzi costruiti da `rhino/gp_prove.py` (`build(doc)` li mette nel documento, `run(doc, cartella)` scrive G-code, STL e misure): staffa a L con fori, asola e nervatura; ingranaggio a 18 denti con mozzo e sede della linguetta; scatola per elettronica con pareti da 1,6 mm, colonnine, feritoie e finestra; gancio da muro in piedi (supporti ad albero); vite M16 con filetto vero; vaso esagonale ritorto con parete da 1,2 mm. Stesse impostazioni del tavolo. File in `export/prove_pezzi/` (non pubblicata). Confronto: `python -m generative_print.compare nostro.gcode bambu.gcode`.

| Pezzo | Noi | Bambu | Peso |
|---|---|---|---|
| Staffa | 13,9 g, 35 min | 14,8 g, 38 min | -6 % |
| Ingranaggio | 6,5 g, 19 min | 7,3 g, 26 min | -11 % |
| Scatola | 17,7 g, 29 min | 17,6 g, 27 min | +1 % |
| Gancio con albero | 7,4 g, 30 min | 7,9 g, 37 min | -7 % |
| Vite M16 | 4,7 g, 21 min | 5,6 g, 24 min | -16 % |
| Vaso | 34,8 g, 59 min | 35,1 g, 63 min | -1 % |

Tutti e sei passano `gcode.check`, senza strati vuoti e senza contorni aperti. Le pareti pesano come quelle di Bambu su tutti i pezzi (scarto entro il 4 %).

Difetti trovati con questi pezzi e corretti (alla prima corsa 4 pezzi su 6 non davano un file valido):
- **strati persi**: un piano di taglio che passa per i vertici della mesh (quote tonde: la vite ha un anello ogni 0,1 mm) restituiva contorni aperti e lo strato restava senza pareti. Ora i piani stanno 0,0013 mm sopra la mezza altezza e, se un contorno resta aperto, si riprova poco piu in la;
- **solido visto come non chiuso** (scatola): la triangolazione fitta lasciava vertici a meta degli spigoli vicini. Ora `Mesh.MatchEdges` li fa combaciare;
- **controllo fallito per tratti minuscoli**: un tratto piu corto di tre decimali spingeva filo da fermi. Ora i tratti sotto 0,02 mm si fondono col successivo;
- **vuoto nelle pareti sottili** (vaso da 1,2 mm, scatola da 1,6): la parete interna veniva scartata tutta e in mezzo restavano 0,3 mm vuoti. Ora ogni parete si adatta allo spazio (`toolpaths.fit_wall`): linea piu stretta dove l'esterno e l'interno si incontrano (da 0,2 mm in su), tagliata solo nel tratto dove non c'e posto. Vaso: da 27,2 g a 34,8 g, contro 35,1 di Bambu;
- **raccordi del riempimento rado sul vuoto** (ingranaggio): le righe si univano anche tra due denti o attorno a un foro. Ora si uniscono in `toolpaths.chain` solo se il raccordo resta dentro il riempimento.

Differenze rimaste, spiegate:
- vite e ingranaggio piu leggeri: sulle superfici inclinate (fianchi del filetto) Bambu aggiunge una fascia di guscio in piu (46 mm per strato sulla vite); noi li abbiamo solo le due pareti e il pieno deciso sulla griglia da 0,5 mm;
- i tempi nostri sono piu corti del 5-25 %: non contiamo i rallentamenti in curva e non rallentiamo ponti e sbalzi.

Limiti nuovi o rimasti: una nervatura sottile fatta da UN solo contorno (non un guscio con esterno e interno) non riceve la linea stretta; lo slicer e lento sui contorni con molti punti (vaso: 76 s per 550 strati, quasi tutto negli spostamenti dei contorni di Rhino); nessuna di queste prove e una stampa vera.

### 3.2 e seguenti — da fare

1. (FATTO, vedi sopra) Stesso pezzo con Bambu Studio e con il nostro G-code: confrontare peso, tempo e numero di strati. Da spiegare subito: sul tavolo con l'albero Bambu dava 28,6 g e 1 h 45, noi 20,0 g e 44 min (piu 6 di avvio). Confrontare anche senza supporti, per separare il pezzo dai supporti. Aprire il nostro `.gcode` nell'anteprima di Bambu Studio per guardarlo strato per strato.
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
- Test: `.venv/Scripts/python -m pytest -q tests` (80 test).
- Interfaccia dei pannelli in inglese; documenti e commenti in italiano.
- I file lunghi con triple virgolette vanno scritti su file e poi eseguiti: la shell non li accetta in linea.
