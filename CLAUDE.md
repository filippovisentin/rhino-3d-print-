# GENERATIVE PRINT

## Obiettivo
Strumento open source (MIT) di generative design per la stampa 3D FDM dentro Rhino 8. Due parti unite: un ciclo chiuso guidato dall'AI (genera, verifica la stampabilità, critica le viste, correggi) per lampade e vasi, e un editor plastico a pennelli (stile sculpt di Blender) con verifica di stampa integrata e anteprima a strati. Consegna STL/3MF, scheda e diario HTML delle iterazioni.

## Stato
- 2026-11-13: demo completa in `runs/2026-11-13_lampada-nervature` (8 iterazioni, diario, consegna in `final/`). Editor funzionante e provato col mouse. 19 test passano.
- Da fare: definizione Grasshopper con slider; secondo generatore dedicato al vaso; prova di stampa reale o in slicer; aggiornare PIANO.md (descrive il piano iniziale, superato in più punti).

## Decisioni e vincoli
- Rhino si pilota con il server MCP McNeel (Rhino-MCP-Platform 0.4.0). Si avvia con `Rhino.exe /nosplash /runscript="_MCPStart"`: l'avvio automatico del router fallisce.
- Nucleo in solo numpy, compatibile con Python 3.9 (interprete di Rhino 8); trimesh serve solo alla verifica esterna e ai test.
- Lampade e vasi si modellano come PELLE esterna (griglia anelli x segmenti); il solido si ricostruisce con `build_shell`. Le forme libere sono solidi chiusi.
- Le misure ufficiali si fanno fuori da Rhino sull'STL esportato. Diametro = cerchio circoscritto.
- Unità mm. PLA 1,24 g/cm³ (ipotesi). Sbalzo limite 45°. Solo lampadine LED.
- Niente misure o test dichiarati senza averli eseguiti.

## Note per Claude
Questa è la scheda del progetto: leggila all'inizio e aggiornala quando cambiano obiettivo, stato o decisioni importanti.
