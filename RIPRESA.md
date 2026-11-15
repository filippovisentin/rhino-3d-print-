# Ripresa del lavoro

Riassunto della sessione del 2026-11-13 (sera), per continuare. La scheda stabile del progetto è `CLAUDE.md`; il piano iniziale `PIANO.md` è superato.

## Dove siamo

Obiettivo dichiarato dall'utente: **uno slicer dentro Rhino**, cioè una finestra che mostra tutti i parametri di stampa 3D di ciò che si è disegnato, più una **modifica plastica** (tirare, spingere, lisciare) da avviare e far crescere.

Fatto e provato in Rhino 8 in questa sessione:

- **Slicer interno** (`rhino/gp_slicer.py`, avvio con `rhino/avvia_slicer.py`).
  - Lavora sugli oggetti selezionati: mesh, polisuperfici, estrusioni, SubD. Più oggetti selezionati = un piatto solo.
  - Finestra: stampante (letta da Bambu Studio: predefinita la A1 mini dell'utente, 180 × 180 × 180 mm), materiale, strato, larghezza linea, pareti, riempimento, strati pieni sopra e sotto, limite di sbalzo, supporti.
  - Controlli: mesh chiusa, ingombro rispetto alla stampante, sbalzi, isole, appoggio, stabilità; stime di peso, filo e tempo.
  - Vista: piano della stampante (rosso se il pezzo non entra), mappa degli sbalzi, anteprima strato per strato con pareti e riempimento.
  - Pulsanti: «Verifica spessori», «Affetta con Bambu Studio» (slicer vero, in background), «Esporta STL», «Modifica plastica».
  - **Orientamento**: «Ruota X 90», «Ruota Y 90», «Migliore» (prova i sei assi e le facce piane più estese, sceglie meno sbalzo, poi più appoggio), «Come disegnato», «Applica al modello» (ruota davvero gli oggetti di Rhino, un passo di Annulla). Finché non si applica, il modello non cambia.
  - **Supporti in anteprima**: con «Supporti» acceso, colonne verdi ogni 3 mm sotto le facce oltre soglia, fino al piano o al pezzo sotto; il volume entra nella stima del peso (riempimento ipotizzato al 12 %).
- **Modifica plastica su oggetti qualsiasi**: il pulsante converte l'oggetto in una maglia regolare (QuadRemesh) e apre l'editor a pennelli già esistente (`rhino/gp_editor.py`: Tira/Spingi, Gonfia, Liscia, Appiattisci, Pizzica, Afferra). L'originale resta nascosto, non viene cancellato. Nell'editor c'è la nuova opzione **«Rispetta il limite di sbalzo mentre scolpisco»**: a fine pennellata la zona toccata viene lisciata finché rientra nel limite (`sculpt.relax_overhang_mesh`). C'è anche la **simmetria a specchio** (X o Y) rispetto al centro dell'oggetto; la simmetria radiale ora ruota attorno a quel centro e non all'origine del mondo.
- **Nucleo**: `generative_print/printcheck.py` (analisi di un solido qualsiasi), `slicer.hatch` (tratteggio del riempimento), impostazioni generiche e volume della stampante in `bambu.py`. 48 test passano (`.venv/Scripts/python -m pytest -q tests`).

Prove eseguite (oggetto `prova_staffa`, una staffa 60 × 40 × 58 mm con foro e braccio a sbalzo):

| Misura | Risultato |
|---|---|
| Volume | 55,1 cm³, uguale al calcolo a mano |
| Strato 101, due pareti | 200,8 mm, uguale al calcolo a mano |
| Sbalzo del braccio | 90°, 1600 mm² in rosso, «servono supporti» |
| Stima nostra | 25,0 g, 0,9 h |
| Bambu Studio (A1 mini) | 24,6 g, 46 min 49 s |
| Lampada (guscio 1,35 mm, 113.280 triangoli) | 3 linee di parete, come i 3 perimetri di Bambu; analisi 2,5 s; uno strato 0,2–0,4 s |
| Orientamento «Migliore» sulla staffa | schiena in giù: sbalzo da 1600 a 81 mm² (resta l'arco alto del foro) |
| Staffa con supporti | nostra stima 36,1 g (196 colonne, 9,0 cm³); Bambu con supporti 37,7 g, 53 min 53 s |
| Specchio Y sulla staffa (centro 30, 20) | 117 punti mossi per lato, simmetrici |
| Pennellata con e senza «Rispetta il limite» | senza: 50,9° e 60 mm² oltre soglia; con: 42,4° e 0 mm² (43 punti corretti) |

## Sessione del 2026-11-14

- **Supporti come in Bambu Studio** (`generative_print/supports.py`, solo numpy): calcolati dove servono (facce oltre il limite di sbalzo e isole), in tre tipologie scelte dal pannello: «Normali a griglia» (allargati di 1,5 mm), «Normali aderenti», «Ad albero» (punte ogni 5 mm, rami inclinati al massimo di 40 gradi che si uniscono, evitano il pezzo e cercano una via libera fino al piano). Opzione «Supporti solo dal piano». Distanza dal pezzo 0,2 mm, due strati di interfaccia. Nella vista: rami ombreggiati o colonne; con «Strati» acceso, i tratti dello strato corrente (linee di base, interfaccia fitta, sezioni dei rami). Il tipo scelto viene passato anche a Bambu (`--support-type`, `--support-bed-only`).
- **Pannelli agganciati** (`rhino/gp_panel.py`): slicer ed editor si aprono come schede nella colonna dei Livelli, non piu come finestre in mezzo allo schermo. Alias di Rhino creati: `GPSlicer` e `GPEditor` (lanciano `rhino/avvia_slicer.py` e `rhino/avvia_editor.py`).
- **Modifica plastica rifatta** (`rhino/gp_editor.py`, `generative_print/sculpt.py`):
  - a ogni colpo si aggiornano solo le facce e le normali toccate;
  - «Solo la superficie toccata»: il pennello non passa sull'altro lato di una parete sottile;
  - Annulla e Ripeti (60 passi, registrano solo i punti cambiati);
  - Maschera: pennello che protegge una zona, con Cancella e Inverti;
  - pennelli nuovi: Argilla, Strato, Piega, Raschia (oltre a Tira/Spingi, Gonfia, Liscia, Appiattisci, Pizzica, Afferra);
  - Ctrl+Shift e trascina: raggio (orizzontale) e forza (verticale); il cerchio interno del cursore mostra la forza.

Prove eseguite il 2026-11-14 (oggetto `prova_tavolo`: piano 60 x 60 x 5 mm su una gamba 10 x 10 x 30, piu una mensola 20 x 60 x 4 sotto un lato):

| Misura | Risultato |
|---|---|
| Supporti aderenti | 3500 celle da 1 mm2 = area del piano meno la gamba; 13,8 cm3 stimati; 34 % poggia sulla mensola |
| Supporti a griglia | 3996 mm2 sostenuti, 15,9 cm3 stimati |
| Supporti ad albero | 168 punte, 10 tronchi sul piano, 2 rami sul pezzo, 8,3 cm3 stimati; calcolo 0,28 s |
| Bambu Studio sullo stesso pezzo, supporti ad albero (A1 mini) | 28,6 g, 1 h 45 min; nostra stima 27,0 g e 1,0 h (il tempo nostro è sottostimato) |
| Pittura dei supporti: «vieta» con raggio 10 mm sotto il piano | 583 facce, 316 mm2 vietati (cerchio teorico 314), colonne da 3500 a 3186, nessuna faccia del lato sopra toccata; il rapporto segna «VIETATI senza supporto» |
| Pittura: «forza» con raggio 4 mm nello stesso punto | 98 facce, tornano 32 colonne entro 3 mm dal centro |
| Colpo di pennello su 40.962 vertici | 7,8 ms (versione precedente, stessa mesh e stessi colpi: 30,1 ms) |
| Annulla di 9 pennellate, poi Ripeti | forma iniziale ritrovata, poi forma finale ritrovata |
| Maschera | Inverti e Annulla provati; i punti protetti non si muovono (test `tests/test_sculpt.py`) |
| Pannelli | scheda nella barra dei Livelli (266 px), passaggio a un'altra scheda senza perdere la sessione, chiusura della scheda che chiude la sessione, comando `GPSlicer` eseguito |

## Obiettivo nuovo: G-code scritto da Rhino, senza Bambu Studio (deciso il 2026-11-14)

Scelte dell'utente: G-code pronto da stampare; stampante vera **Bambu Lab A1 mini**; il pulsante «Affetta con Bambu» resta come confronto facoltativo.

Fatto: `generative_print/machine.py` (profili Bambu con catena `inherits`, valori del filamento, compilazione dei modelli di avvio, fine e cambio strato). Provato sui profili veri: avvio e fine della A1 mini si compilano senza segnaposto rimasti, con `G29 A1 X.. Y.. I.. J..` sulla zona del pezzo e temperatura del piatto dal profilo PLA. Esiste anche il profilo PETG per la A1 mini (nome con «0.4 nozzle»: `bambu.filament_profile` non lo trova, da correggere).

Fatto il 2026-11-15 il punto 1: `gcode.write` / `gcode.generate` / `gcode.check`, con `tests/test_gcode.py`. Cubo pieno da 20 mm, profili veri A1 mini PLA: 9,69 g (a mano 9,88, scarto -1,9 %), 100 strati, 17.130 righe, tempo di sola stampa stimato 15 min (stima non confrontata con Bambu). Il controllo copre solo il nostro corpo, non avvio e fine ufficiali.

Fatto il 2026-11-15 anche il punto 2 (percorsi veri in Rhino, pulsante «Generate G-code»). Misure su `prova_tavolo` (60 x 60 x 35, PLA, 15 %): supporti aderenti 34,6 g, 52 min di sola stampa stimati, 1.044 ritrazioni; ad albero 20,0 g, 44 min, 1.489 ritrazioni; scrittura in 1-3 s. Bambu sullo stesso pezzo con l'albero dava 28,6 g e 1 h 45: la differenza NON e ancora spiegata (supporti diversi, tempo nostro senza rallentamenti in curva): e il primo lavoro del punto 3.

Fatto il 2026-11-15 il confronto con Bambu sul tavolo (tabella e spiegazione in `PROSSIMI_PASSI.md`, passo 3.1): senza supporti 15,7 g e 31 min contro 16,7 g e 35 min; albero 23,5 g e 50 min contro 28,6 g e 1 h 46 (Bambu: piu supporto, interfaccia, 9.737 ritrazioni); aderenti 37,2 g e 57 min contro 36,3 g e 53 min. Aggiunto il rallentamento degli strati brevi. 75 test.

Fatte il 2026-11-15 le prove su sei pezzi veri (`rhino/gp_prove.py`: staffa, ingranaggio, scatola, gancio, vite filettata, vaso): trovati e corretti cinque difetti (strati persi sui vertici, solido non chiuso, tratti minuscoli, vuoto nelle pareti sottili, raccordi del rado sul vuoto). Pesi entro -16 % / +1 % da Bambu. Tabella in `PROSSIMI_PASSI.md`, passo 3.1 bis. 80 test.

Da fare, in ordine:
1. (FATTO) `generative_print/gcode.py` (solo numpy): da strati di percorsi (parete esterna, interne, pieno, riempimento, supporto, interfaccia) a testo G-code: estrusione relativa (M83), sezione della linea (lw - h) * h + pi * (h/2)^2, ritrazione 0,8 mm con sollevamento sui trasferimenti oltre 1,5 mm, velocita per tipo con tetto di portata (mm3/s) dal filamento, primo strato lento, ventola dal secondo strato, `M73` e modello del cambio strato a ogni strato, intestazione `HEADER_BLOCK` con strati, tempo e peso. Piu un controllo che rilegge il testo: tutto dentro il piatto, estrusione totale uguale al calcolo, nessuna estrusione in aria.
2. In `rhino/gp_slicer.py`: percorsi veri di tutti gli strati (le pareti ci sono gia in `_walls`; tagliare tutti i piani in una chiamata sola di `MeshPlane`), strati pieni locali ricavati dal campionamento verticale di `supports.ldi` (cella piena se il materiale sopra o sotto e meno di N strati), riempimento accorciato di 0,35 linee ai capi, supporti da `supports.layer_pattern` e `tree_sections`. Pezzo centrato sul piatto. Pulsante «Genera G-code» con barra di avanzamento e rapporto (peso e tempo dal G-code).
3. (confronto FATTO) Prima stampa sorvegliata di un pezzo piccolo.
4. Da verificare sulla macchina: che la A1 mini accetti un `.gcode` semplice dalla scheda microSD.

## Limiti noti (non nascondere)

- Pareti e riempimento della vista sono un'**anteprima geometrica**, non un percorso di stampa: niente G-code nostro, niente ordine dei tratti, niente ritrazioni. Peso e tempo precisi li dà Bambu.
- Gli strati pieni sono solo i primi e gli ultimi dell'oggetto intero: le superfici piane a metà altezza non vengono riconosciute.
- Il pulsante Bambu passa il materiale scelto solo se esiste il profilo per quella stampante (PETG, ABS e ASA mancano sulla A1 mini): altrimenti usa il PLA e lo scrive nel rapporto. Si affetta sempre sul piatto PEI ruvido, perché il Cool Plate rifiuta il PETG (codice -61). Provato: PETG su X1 Carbon, staffa 23,7 g, 1 h 11 min.
- Con i supporti spenti Bambu affetta lo stesso anche un pezzo che ne avrebbe bisogno (la staffa): l'avviso è nel nostro rapporto, non nel suo.
- La maglia per scolpire arrotonda leggermente gli spigoli vivi e ha qualche irregolarità sui bordi (visto sulla staffa a 1,5 mm di lato).
- I supporti sono un'anteprima e una stima: dicono dove vanno e quanto materiale serve circa, non sono i percorsi che stampera Bambu. Il peso dei supporti normali usa una densita ipotizzata (13 % piu interfaccia all'80 %); quello dell'albero un guscio di una linea. Non c'e ancora la distanza orizzontale dal pezzo.
- **Pittura dei supporti** (pannello, sezione Supporti: Forza / Vieta / Cancella, raggio, «Cancella la pittura»): si trascina col sinistro sul pezzo, Ctrl cancella; blu = forzato, viola = vietato; i supporti si ricalcolano al rilascio. Lavora per faccia, sulle facce rivolte dallo stesso lato di quella toccata. Vale solo per la nostra anteprima: a Bambu (riga di comando, STL) non viene passata. Provata da script con le stesse funzioni che usa il mouse, **non con il mouse**.
- L'albero evita il pezzo guardando un passo alla volta: sotto un ostacolo largo un ramo puo finire appoggiato sul pezzo anche con «solo dal piano» (il rapporto dice quanti).
- I pennelli nuovi, l'annulla/ripeti e la maschera sono stati provati da script e disegnando il pannello in memoria, **non con il mouse** (compreso Ctrl+Shift per raggio e forza).
- «Migliore» prova solo i sei assi e le facce piane grandi: su forme organiche può esistere un orientamento migliore inclinato.
- «Rispetta il limite» liscia: su pennellate molto profonde può appiattire parte del gesto; se non riesce a rientrare lo dice nello stato.
- La finestra è stata controllata disegnandola in memoria e leggendone i valori da script; **cursori e pulsanti non sono stati provati con il mouse** (nemmeno i nuovi pulsanti di orientamento).
- Nessun oggetto è stato stampato davvero.

## Prossimi passi proposti

1. Provare la finestra a mano (mouse) e correggere ciò che non torna.
2. Supporti: passare la pittura a Bambu (serve un 3MF al posto dell'STL), distanza orizzontale dal pezzo, confronto del peso con Bambu su piu pezzi.
2b. Modifica plastica: suddivisione dove serve dettaglio, spigoli vivi conservati nella maglia da scolpire, pennello che corregge anche gli sbalzi già presenti (oggi tocca solo la zona scolpita).
3. Strati pieni locali (superfici piane a metà altezza) nell'anteprima.
4. Scala e disposizione sul piatto: oggi il rapporto dice solo di quanto ridurre («andrebbe ridotto al NN %»), non scala né dispone più pezzi.
6. Aggiornare o togliere `PIANO.md`.

## Come ripartire

1. Rhino 8 si avvia con `Rhino.exe /nosplash /runscript="_MCPStart"` (l'avvio automatico del router fallisce).
2. In Rhino: selezionare un oggetto e scrivere `GPSlicer` (oppure `GPEditor` per scolpire). Senza alias: `_ScriptEditor`, aprire `rhino/avvia_slicer.py`, F5.
3. Da script (MCP): rimettere ogni volta la cartella del progetto in `sys.path`; `gp_slicer.start(doc)`, poi `sl.set(walls=3, infill=0.2)`, `sl.set_layer(100)`, `sl.thickness()`, `sl.best_orientation()`, `sl.rotate_by("x")`, `sl.apply_orientation()`, `sl.bambu_start()` e `sl.bambu_poll()`.
4. Nel documento di Rhino lasciato aperto c'è `prova_staffa` con lo slicer aperto sopra.

## Stato del repository

- GitHub: https://github.com/filippovisentin/rhino-3d-print- (ramo `main`). Tutto il lavoro di questa sessione è pubblicato su `main` (l'utente ha chiesto di pubblicare a fine lavoro; ho pubblicato a ogni blocco).
- Controllo di sicurezza ripetuto: nessun segreto, nessuna email personale, autori con indirizzo noreply, processi esterni lanciati senza shell.
- Durante la prova ho copiato per errore una zona dello schermo dove c'era il browser dell'utente (pagina GitHub del progetto): immagine cancellata subito, non è nel repository.
