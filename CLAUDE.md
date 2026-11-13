# GENERATIVE PRINT

## Obiettivo
Strumento open source (MIT) di generative design per la stampa 3D FDM dentro Rhino 8. Due parti unite: un ciclo chiuso guidato dall'AI (genera, verifica la stampabilità, critica le viste, correggi) per lampade e vasi, e un editor plastico a pennelli (stile sculpt di Blender) con verifica di stampa integrata e anteprima a strati. Consegna STL/3MF, scheda e diario HTML delle iterazioni.

## Stato
- 2026-11-13: demo completa in `runs/2026-11-13_lampada-nervature` (8 iterazioni, diario, consegna in `final/`). Editor funzionante e provato col mouse. 19 test passano.
- 2026-11-13: definizione Grasshopper `grasshopper/generative_print.gh` con 20 slider, mappa degli sbalzi, strati, verifica, esportazione e invio all'editor; provata anche riaprendola da zero.
- 2026-11-13: generatore del vaso (sezione a lobi o poligono raccordato con rotazione, capienza, modalità spirale con controllo dei contorni), corsa `runs/2026-11-13_vaso-esagonale` (3 iterazioni), modulo `deliver`, vaso in Grasshopper. 31 test.
- 2026-11-13: confronto con uno slicer vero. `generative_print/bambu.py` affetta con Bambu Studio da riga di comando (profilo X1 Carbon 0.4) e analizza il G-code: peso, tempo, supporti, gap infill, sbalzo strato per strato, continuità della spirale. Risultati in `runs/*/final/slicer_bambu*.json` e nelle schede. 37 test.
- 2026-11-13: limite di progetto sullo sbalzo portato a 43° (2° di margine), deciso dall'utente dopo che Bambu, a soglia 45°, metteva supporti alla lampada da 44,5°. Nuova correzione automatica `sculpt.relax_overhang` (lisciatura solo dove si supera il limite); lampada iterazione 9 = nuovo finale, 42,7°.
- Da fare: prova di stampa reale; aggiornare PIANO.md (descrive il piano iniziale, superato in più punti).

## Decisioni e vincoli
- Rhino si pilota con il server MCP McNeel (Rhino-MCP-Platform 0.4.0). Si avvia con `Rhino.exe /nosplash /runscript="_MCPStart"`: l'avvio automatico del router fallisce.
- Nucleo in solo numpy, compatibile con Python 3.9 (interprete di Rhino 8); trimesh serve solo alla verifica esterna e ai test.
- Lampade e vasi si modellano come PELLE esterna (griglia anelli x segmenti); il solido si ricostruisce con `build_shell`. Le forme libere sono solidi chiusi.
- Le misure ufficiali si fanno fuori da Rhino sull'STL esportato. Diametro = cerchio circoscritto.
- Vaso in spirale: si esporta il solido PIENO (lo svuota lo slicer); le viste mostrano invece il pezzo stampato (parete di una linea). Le catture attendono il ridisegno (`_settle`), altrimenti escono con i fili della maglia.
- Unità mm. PLA 1,24 g/cm³ (ipotesi). Sbalzo limite di progetto 43° (la regola fisica è 45°: 2° di margine perché lo slicer non metta supporti). Solo lampadine LED.
- La definizione .gh si costruisce da script (`grasshopper/build_definition.py`), non a mano; il componente Python 3 contiene solo un aggancio a `rhino/gp_gh.py`. In Rhino 8 `SetParametersFromScript` non crea ingressi: si registrano con `CreateParameter`.
- Gli script lanciati via MCP devono rimettere ogni volta la cartella del progetto in `sys.path`.
- Niente misure o test dichiarati senza averli eseguiti.
- Slicer di confronto: Bambu Studio (installato, open source). La CLI accetta i profili solo come preset utente con `inherits` del profilo di sistema (senza: codice -17); codice -50 = pezzo fuori dal volume. Bambu ricentra e ruota leggermente il pezzo (circa 2,5°): le analisi usano solo il G-code, con archi disattivati. La soglia dei supporti di Bambu si misura dall'orizzontale. Pareti con Arachne (con le linee fisse le pareti inclinate generano molto gap infill). La stampante preselezionata dall'utente in Bambu è una A1 mini (180 mm): lampada e vaso demo non ci entrano.

## Note per Claude
Questa è la scheda del progetto: leggila all'inizio e aggiornala quando cambiano obiettivo, stato o decisioni importanti.
