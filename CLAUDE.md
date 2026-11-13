# GENERATIVE PRINT

## Obiettivo
Strumento open source (licenza MIT, proposta) di generative design guidato dall'AI. Partendo da un brief testuale produce oggetti per la stampa 3D FDM (lampade e vasi) con Rhino 8 + Grasshopper, attraverso un ciclo chiuso: genera, verifica la stampabilità (sbalzi, spessori, peso, ingombro, mesh chiusa, portalampada), critica estetica sulle viste, correzione motivata. Consegna l'STL/3MF finale, una scheda tecnica e un diario HTML delle iterazioni: è la parte dimostrativa del progetto, per GitHub e per il portfolio del designer.

## Stato
- 2026-11-13: cartella creata (profilo Ingegneria e 3D). Il piano è in `PIANO.md`, **in attesa di conferma dell'utente**.
- Connessione a Rhino verificata: Rhino-MCP-Platform 0.4.0 e router OK. L'avvio automatico di Rhino dalla sessione fallisce (`startup_timeout`): Rhino va aperto a mano, poi si lancia `MCPStart`.
- Python 3.12 di sistema con numpy; trimesh non ancora installato.

## Decisioni e vincoli
- Pilotaggio di Rhino: MCP McNeel (`run_python`, `get_viewport_image`, `g1_*`). Rhino.Compute e `/runscript` sono scartati (vedi PIANO.md §1).
- Rhino genera e cattura le viste; le metriche si calcolano fuori da Rhino con Python 3.12 + trimesh sull'STL esportato, in modo indipendente e testabile.
- Generatore come modulo Python 3 unico, usato sia dal componente GH sia dall'agente.
- Unità in mm. PLA a 1,24 g/cm³ (ipotesi). Sbalzo limite 45°, configurabile. Spessore minimo ≥ 0,8 mm, consigliato 1,2 mm.
- Lampade: solo sorgenti LED (il PLA si ammorbidisce intorno ai 55-60 °C). Quote E27/E14 da verificare su 2 fonti prima di usarle.
- Niente misure o test dichiarati senza averli eseguiti.

## Note per Claude
Questa è la scheda del progetto: leggila all'inizio e aggiornala quando cambiano obiettivo, stato o decisioni importanti.
