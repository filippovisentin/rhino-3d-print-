# Vaso esagonale torto in modalità spirale: scheda

Oggetto finale della corsa `2026-11-13_vaso-esagonale` (iterazione 3). Unità: mm, g, gradi.

> Vaso per fiori secchi a sezione esagonale torta, alto 20 cm, capienza di almeno 1,5 litri, stampabile in PLA in modalità vaso (spirale, una sola linea), senza supporti, massimo 100 g.

## File
- `vaso-esagonale.stl` (60480 triangoli) e `vaso-esagonale.3mf`
- `params.json`: parametri del generatore

## Parametri
| Parametro | Valore |
|---|---|
| Altezza | 200 mm |
| Raggi del profilo (dal basso) | 44, 58, 64, 58, 50 mm |
| Sezione | 6 lati, poligono a lati diritti, raccordo 0,12, rotazione 180 gradi |
| Parete | una linea da 0,7 mm (modalità spirale) |

## Misure sull'STL (`python -m generative_print.verify`)
| Controllo | Misura | Limite | Esito |
|---|---|---|---|
| Mesh chiusa | 0 spigoli aperti | 0 | ok |
| Sbalzo massimo | 36,4 gradi | <= 45 | ok |
| Isole (partenze nel vuoto) | 0 punti | 0 | ok |
| Ingombro | 125,5 x 127,9 x 200 mm | <= 256 x 256 x 256 | ok |
| Appoggio sul piano | 5209 mm2 | >= 100 | ok |
| Stabilita (baricentro dentro l'appoggio) | 38,8 mm | > 0 | ok |
| Peso stimato | 65,1 g | <= 100 | ok |
| Parete in spirale (una linea) | 0,7 mm | >= ugello 0.4 | ok |
| Capienza | 1725 ml | >= 1500 | ok |
| Un solo contorno per strato (spirale) | 0 quote fuori regola | 0 | ok |
| Tenuta all'acqua (solo informativo) | non garantita  | non richiesta | ok |

Peso stimato 65,1 g, circa 21,8 m di filo da 1,75 mm, 1000 strati da 0,2 mm. Tempo di stampa circa 2,4 h (stima grossolana).

## Stampa consigliata

PLA, ugello 0,4 mm, strato 0,2 mm, **modalità vaso / spirale** (Bambu e Orca: "Spiral vase"; PrusaSlicer: "Spiral vase"), larghezza della linea esterna 0,7 mm, 4 strati pieni di fondo, nessun supporto.

Orientamento: fondo sul piano, così come esce il file. Il file è un solido pieno: è lo slicer a ricavarne la parete singola. Non svuotarlo prima.

Perché: a ogni quota c'è un solo contorno (verificato ogni mm), condizione necessaria per la spirale; sbalzo massimo 36,4 gradi.

La parete singola non è a tenuta d'acqua garantita: per fiori freschi serve un inserto o un rivestimento interno.


## Non verificato
L'oggetto non è stato stampato né aperto in uno slicer. La densità del PLA (1,24 g/cm3) è un valore ipotizzato.
