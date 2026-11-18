# eps-editabile

File di supporto della skill **eps-editabile** di Claude, che genera progetti di stampa come EPS nativi di Illustrator: testo vivo, livelli, abbondanza 5 mm e controlli di stampa.

- `modello/modello_illustrator.eps`: il modello Illustrator da cui vengono clonate le strutture native (solo dati privati, anonimizzati).
- `scripts/`: generatore (`epsbuilder.py`, `tedoc.py`), download dei font Google (`fetch_fonts.py`), pacchetto di consegna (`package.py`).

La skill scarica questi file all'inizio di ogni lavoro. Non serve fare nulla qui a mano.
