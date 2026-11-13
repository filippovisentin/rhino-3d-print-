# Lampada da tavolo a nervature torte: scheda

Oggetto finale della corsa `2026-11-13_lampada-nervature` (iterazione 8). Unità: mm, g, gradi.

> Lampada da tavolo, diffusore a nervature torte, diametro 18 cm, stampabile in PLA senza supporti, massimo 250 g.

## File
- `lampada-nervature.stl` (113280 triangoli) e `lampada-nervature.3mf`
- `params.json`: parametri del generatore

## Parametri
| Parametro | Valore |
|---|---|
| Altezza | 220 mm |
| Raggi del profilo (dal basso) | 54, 84,5, 81,5, 64, 44 mm |
| Nervature | 16, ampiezza 5%, torsione 110 gradi |
| Parete / fondo | 1,35 mm / 3 mm |

## Misure sull'STL (`python -m generative_print.verify`)
| Controllo | Misura | Limite | Esito |
|---|---|---|---|
| Mesh chiusa | 0 spigoli aperti | 0 | ok |
| Sbalzo massimo | 44,5 gradi | <= 45 | ok |
| Isole (partenze nel vuoto) | 0 punti | 0 | ok |
| Ingombro | 180,4 x 180,4 x 220 mm | <= 256 x 256 x 256 | ok |
| Appoggio sul piano | 7871 mm2 | >= 100 | ok |
| Stabilita (baricentro dentro l'appoggio) | 54 mm | > 0 | ok |
| Peso stimato | 226 g | <= 250 | ok |
| Spessore minimo (1 percentile) | 1,27 mm | >= 1.2 | ok |
| Foro portalampada E27 | 40,5 mm | 40 - 41 | ok |
| Distanza lampadina-parete | 27,9 mm | >= 20 | ok |
| Apertura superiore | 85,5 mm | >= 50 | ok |
| Lampadina dentro il diffusore | 50 mm | >= 0 | ok |
| Diametro massimo | 181,7 mm | 180 +/- 3 | ok |

Peso stimato 226 g, circa 75,8 m di filo da 1,75 mm, 1100 strati da 0,2 mm. Tempo di stampa circa 8,4 h (stima grossolana).

## Stampa consigliata

PLA, ugello 0,4 mm, strato 0,2 mm, 3 perimetri (la parete da 1,35 mm è fatta di soli perimetri), generatore di pareti **Arachne** (Bambu e Orca: "Wall generator"), nessun supporto.

Perché Arachne: tagliata in orizzontale, la parete inclinata è più larga del suo spessore (spessore / cos dell'inclinazione). Con le linee fisse lo slicer riempie la differenza con molto gap infill, più lento.

Orientamento: fondo sul piano, così come esce il file.

Perché: nessuna faccia supera i 45 gradi di sbalzo (massimo misurato 44,5 gradi).


## Confronto con lo slicer (Bambu Studio)
Affettato da riga di comando con `python -m generative_print.bambu` (ricetta qui sopra, senza supporti). Riassunto completo in `slicer_bambu.json`.

- Stampante Bambu Lab X1 Carbon 0.4 nozzle, Bambu Studio da riga di comando.
- Peso 221.5 g (densita del profilo 1.26 g/cm3), 73.1 m di filo, 1100 strati, tempo 7h 17m 50s.
- Sbalzo del muro esterno strato per strato (uno strato ogni 2, 549 controllati): sul 99% dei punti di ogni strato al massimo 43.4 gradi, 0 strati oltre il limite; picco assoluto 47.5 gradi a z 219.0 mm, 1 strati con almeno un punto oltre il limite; mediana 36.5 gradi.
- Volume di materiale: nostra stima 182,3 cm3, slicer 175,8 cm3 (+3.7%).
- Tempo: nostra stima grossolana 8,4 h, slicer 7,3 h.
- Con i supporti automatici a soglia 45 gradi lo slicer genera 35,3 m di supporto (con la soglia predefinita del profilo, 30 gradi dall'orizzontale, nessuno).

## Non verificato
L'oggetto non è stato stampato. La densità del PLA (1,24 g/cm3) è un valore ipotizzato; lo slicer usa quella del suo profilo.
