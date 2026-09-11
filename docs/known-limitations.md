# Limiti noti

Questo registro raccoglie i limiti che incidono sui gate della roadmap ma non possono essere
rimossi soltanto modificando il repository. Ogni voce resta aperta finche' non viene soddisfatta
la condizione di uscita o modificato esplicitamente il gate interessato.

## KL-001 — Protezione di `master` non disponibile sul repository privato

- Stato: risolto il prerequisito l'11/09/2026; `F0.2.6` resta aperto.
- Roadmap: `F0.2.6`, Gate G0.
- Verifica: 11/09/2026.
- Contesto originale: `JakeKing0001/Jake` era un repository privato con branch predefinito
  `master`.
- Evidenza originale: la lettura dell'endpoint GitHub per la branch protection restituiva HTTP `403` con
  l'indicazione che la funzione richiede GitHub Pro oppure che il repository sia pubblico.
- Risoluzione del limite: il repository e' ora `PUBLIC`; la stessa API risponde `404 Branch not
  protected`, confermando che il vincolo di piano/visibilita' non e' piu' quello attivo.
- Stato corrente: la protezione non e' ancora configurata. I check osservati sull'ultimo commit
  remoto sono `Python 3.11 (Windows)`, `Python 3.12 (Windows)` e
  `HUD nativo C++/Qt6/QML - build health check`.
- Condizione residua di `F0.2.6`: configurare questi check come obbligatori e verificare che un
  merge con CI rossa venga rifiutato. Questa operazione modifica GitHub e resta fuori dalle
  azioni locali autorizzate.
