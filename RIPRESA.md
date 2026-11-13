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

## Limiti noti (non nascondere)

- Pareti e riempimento della vista sono un'**anteprima geometrica**, non un percorso di stampa: niente G-code nostro, niente ordine dei tratti, niente ritrazioni. Peso e tempo precisi li dà Bambu.
- Gli strati pieni sono solo i primi e gli ultimi dell'oggetto intero: le superfici piane a metà altezza non vengono riconosciute.
- Il pulsante Bambu passa il materiale scelto solo se esiste il profilo per quella stampante (PETG, ABS e ASA mancano sulla A1 mini): altrimenti usa il PLA e lo scrive nel rapporto. Si affetta sempre sul piatto PEI ruvido, perché il Cool Plate rifiuta il PETG (codice -61). Provato: PETG su X1 Carbon, staffa 23,7 g, 1 h 11 min.
- Con i supporti spenti Bambu affetta lo stesso anche un pezzo che ne avrebbe bisogno (la staffa): l'avviso è nel nostro rapporto, non nel suo.
- La maglia per scolpire arrotonda leggermente gli spigoli vivi e ha qualche irregolarità sui bordi (visto sulla staffa a 1,5 mm di lato).
- I supporti in anteprima sono colonne verticali su una griglia: non il disegno vero dei supporti (ad albero o a griglia) dello slicer.
- «Migliore» prova solo i sei assi e le facce piane grandi: su forme organiche può esistere un orientamento migliore inclinato.
- «Rispetta il limite» liscia: su pennellate molto profonde può appiattire parte del gesto; se non riesce a rientrare lo dice nello stato.
- La finestra è stata controllata disegnandola in memoria e leggendone i valori da script; **cursori e pulsanti non sono stati provati con il mouse** (nemmeno i nuovi pulsanti di orientamento).
- Nessun oggetto è stato stampato davvero.

## Prossimi passi proposti

1. Provare la finestra a mano (mouse) e correggere ciò che non torna.
2. Modifica plastica: suddivisione dove serve dettaglio, spigoli vivi conservati nella maglia da scolpire, pennello che corregge anche gli sbalzi già presenti (oggi tocca solo la zona scolpita).
3. Strati pieni locali (superfici piane a metà altezza) nell'anteprima.
4. Scala e disposizione sul piatto: oggi il rapporto dice solo di quanto ridurre («andrebbe ridotto al NN %»), non scala né dispone più pezzi.
5. Comando con nome in Rhino (alias `GPSlicer`): tocca le impostazioni di Rhino dell'utente, quindi chiedere prima.
6. Aggiornare o togliere `PIANO.md`.

## Come ripartire

1. Rhino 8 si avvia con `Rhino.exe /nosplash /runscript="_MCPStart"` (l'avvio automatico del router fallisce).
2. In Rhino: selezionare un oggetto, `_ScriptEditor`, aprire `rhino/avvia_slicer.py`, F5.
3. Da script (MCP): rimettere ogni volta la cartella del progetto in `sys.path`; `gp_slicer.start(doc)`, poi `sl.set(walls=3, infill=0.2)`, `sl.set_layer(100)`, `sl.thickness()`, `sl.best_orientation()`, `sl.rotate_by("x")`, `sl.apply_orientation()`, `sl.bambu_start()` e `sl.bambu_poll()`.
4. Nel documento di Rhino lasciato aperto c'è `prova_staffa` con lo slicer aperto sopra.

## Stato del repository

- GitHub: https://github.com/filippovisentin/rhino-3d-print- (ramo `main`). Tutto il lavoro di questa sessione è pubblicato su `main` (l'utente ha chiesto di pubblicare a fine lavoro; ho pubblicato a ogni blocco).
- Controllo di sicurezza ripetuto: nessun segreto, nessuna email personale, autori con indirizzo noreply, processi esterni lanciati senza shell.
- Durante la prova ho copiato per errore una zona dello schermo dove c'era il browser dell'utente (pagina GitHub del progetto): immagine cancellata subito, non è nel repository.
