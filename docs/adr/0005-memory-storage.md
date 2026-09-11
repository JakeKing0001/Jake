# ADR 0005 — Storage della memoria personale

- Stato: `Proposed`
- Data: 2026-09-11

## Contesto

La memoria usa SQLite locale con ricordi, cronologia, relazioni, embedding, provenienza e TTL.
È adeguata a migliaia di record personali e offre transazioni e migrazioni senza un servizio.
Mancano schema unificato, owner/profile, valid time completo, sensibilità e cancellazione
verificabile; introdurre presto un database vettoriale o cloud amplierebbe operatività e privacy.

## Decisione

Mantenere SQLite come source of truth locale, con migrazioni monotone e record versionati.
Embedding e indici sono derivati ricostruibili; ogni memoria deve avere owner, provenienza,
confidence, valid time, TTL e sensitivity. Retrieval non può oltrepassare profilo/progetto.
Nessun cloud o vector DB esterno è abilitato per default.

## Alternative considerate

- File JSON/JSONL: semplici, ma deboli per transazioni, relazioni e migrazioni.
- Database vettoriale embedded: ricerca migliore, nuovo formato e superficie di dipendenza.
- Database/server cloud: sync facile, latenza, disponibilità e privacy peggiori.
- Event store puro: audit forte, proiezioni e compattazione più complesse.

## Conseguenze

La scala target resta personale, non milioni di record. Schema e migrazioni diventano API di
prodotto; cancellare richiede rimuovere dato, indici derivati e backup secondo policy. Le query
temporali e di provenienza precedono ottimizzazioni vettoriali.

## Piano di migrazione

Aggiungere tabella schema/versione e backup atomico; introdurre owner, confidence, valid time e
sensitivity; separare working memory volatile; rendere gli embedding ricostruibili; aggiungere
export/import e cancellazione verificata prima della sync.

## Criterio di revisione

Rivedere quando il benchmark locale supera i budget di retrieval, prima del multiutente/sync o
se la cifratura per campo rende necessaria una diversa strategia di indicizzazione.
