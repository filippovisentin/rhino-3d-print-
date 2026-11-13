# Lampada da tavolo a nervature torte: scheda

Oggetto finale della corsa `2026-11-13_lampada-nervature` (iterazione 8). Unità: mm, g, gradi.

## File
- `lampada-nervature.stl` (113280 triangoli) e `lampada-nervature.3mf`
- `params.json`: parametri del generatore; `skin.npy`: pelle scolpita (139 x 192 vertici)

## Parametri
| Parametro | Valore |
|---|---|
| Altezza | 220,0 mm |
| Raggi del profilo (dal basso) | 54,0, 84,5, 81,5, 64,0, 44,0 mm |
| Nervature | 16, ampiezza 5,0%, torsione 110,0 gradi |
| Sfumatura nervature | 22,0% in basso, 10,0% in alto |
| Parete | 1,35 mm |
| Fondo | 3,0 mm, foro 40,5 mm (E27) |
| Modellazione a mano | 4 costole maggiori (Tira) e terminazioni lisciate (Liscia), simmetria radiale 4 |

## Misure sull'STL (`python -m generative_print.verify`)
| Controllo | Misura | Limite | Esito |
|---|---|---|---|
| Mesh chiusa | 0 spigoli aperti | 0 | ok |
| Sbalzo massimo | 44,5 gradi | <= 45 | ok |
| Isole (partenze nel vuoto) | 0 punti | 0 | ok |
| Ingombro | 180,4 x 180,4 x 220,0 mm | <= 256 x 256 x 256 | ok |
| Appoggio sul piano | 7871,0 mm2 | >= 100 | ok |
| Stabilita (baricentro dentro l'appoggio) | 54,0 mm | > 0 | ok |
| Peso stimato | 226,0 g | <= 250 | ok |
| Spessore minimo (1 percentile) | 1,27 mm | >= 1.2 | ok |
| Foro portalampada E27 | 40,5 mm | 40 - 41 | ok |
| Distanza lampadina-parete | 27,9 mm | >= 20 | ok |
| Apertura superiore | 85,5 mm | >= 50 | ok |
| Lampadina dentro il diffusore | 50,0 mm | >= 0 | ok |
| Diametro massimo | 181,7 mm | 180 +/- 3 | ok |

Volume 182,3 cm3, superficie 2554,0 cm2, 1100 strati da 0,2 mm, circa 75,8 m di filo da 1,75 mm.
Tempo di stampa: circa 8,4 h, stima grossolana (volume diviso per una portata di 6 mm3/s).

## Stampa consigliata
PLA, ugello 0,4 mm, strato 0,2 mm, 3 perimetri (la parete da 1,35 mm e fatta di soli perimetri), nessun supporto.
Orientamento: fondo sul piano, cosi come esce il file. Fondo pieno (3 mm = 15 strati).
Perche: nessuna faccia supera i 45 gradi di sbalzo, ma il margine e di 0,5 gradi: se le nervature si sporcano, ridurre la velocita dei perimetri esterni o aumentare il raffreddamento.

## Montaggio
Portalampada E27 con ghiera filettata: il fondo (3 mm) si serra tra la battuta del portalampada e la ghiera, attraverso il foro da 40,5 mm.
Solo lampadine LED: il PLA si deforma intorno ai 55-60 gradi C. Distanza misurata tra una lampadina A60 (diametro 60 mm) e la parete: 27,9 mm.

## Non verificato
L'oggetto non e stato stampato ne aperto in uno slicer. La densita del PLA (1,24 g/cm3) e un valore ipotizzato. Nessuna verifica termica.
