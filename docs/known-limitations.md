# Limiti noti

Questo registro raccoglie i limiti che incidono sui gate della roadmap ma non possono essere
rimossi soltanto modificando il repository. Ogni voce resta aperta finche' non viene soddisfatta
la condizione di uscita o modificato esplicitamente il gate interessato.

## KL-001 — Protezione di `master` non disponibile sul repository privato

- Stato: risolto l'11/09/2026. `F0.2.6` chiuso.
- Roadmap: `F0.2.6`, Gate G0.
- Verifica: 11/09/2026.
- Contesto originale: `JakeKing0001/Jake` era un repository privato con branch predefinito
  `master`.
- Evidenza originale: la lettura dell'endpoint GitHub per la branch protection restituiva HTTP `403` con
  l'indicazione che la funzione richiede GitHub Pro oppure che il repository sia pubblico.
- Risoluzione del limite: il repository e' ora `PUBLIC`; la stessa API rispondeva `404 Branch not
  protected`, confermando che il vincolo di piano/visibilita' non era piu' quello attivo.
- Configurazione applicata, con autorizzazione esplicita dell'utente: `required_status_checks`
  (`strict=true`) sui tre contesti `Python 3.11 (Windows)`, `Python 3.12 (Windows)` e
  `HUD nativo C++/Qt6/QML - build health check`; `enforce_admins=true`; force-push e delete
  vietati su `master`. Da questo momento anche i push diretti dell'owner sono vietati: ogni
  modifica passa da branch dedicato, PR e CI verde prima del merge.
- Verifica empirica del rifiuto: PR #1 aperta da un branch usa-e-getta con un test che fallisce
  deliberatamente ha prodotto due check rossi (`Python 3.11/3.12`) e l'API GitHub ha riportato
  `mergeable: MERGEABLE` ma `mergeStateStatus: BLOCKED`, confermando che i required status checks
  impediscono davvero il merge. PR chiusa senza merge, branch cancellato.
- Condizione di uscita di `F0.2.6`: soddisfatta.
