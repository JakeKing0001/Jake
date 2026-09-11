# Limiti noti accettati

Questo registro raccoglie i limiti che incidono sui gate della roadmap ma non possono essere
rimossi soltanto modificando il repository. Ogni voce resta aperta finche' non viene soddisfatta
la condizione di uscita o modificato esplicitamente il gate interessato.

## KL-001 — Protezione di `master` non disponibile sul piano GitHub corrente

- Stato: aperto, non accettato per G0.
- Roadmap: `F0.2.6`, Gate G0.
- Verifica: 11/09/2026.
- Contesto: `JakeKing0001/Jake` e' un repository privato con branch predefinito `master`.
- Evidenza: la lettura dell'endpoint GitHub per la branch protection restituisce HTTP `403` con
  l'indicazione che la funzione richiede GitHub Pro oppure che il repository sia pubblico.
- Impatto: non e' possibile rendere la CI un required check di `master` con la configurazione
  attuale; un commit puo' quindi arrivare sul branch anche con workflow rosso.
- Mitigazione corrente: non considerare superati `F0.2.6` e G0; verificare manualmente il run CI
  associato a ogni commit remoto prima di usarlo come baseline.
- Condizione di uscita: abilitare un piano che supporti la branch protection per repository
  privati oppure rendere pubblico il repository con decisione esplicita dell'utente, quindi
  configurare la CI come required check e verificarne il rifiuto di un merge rosso.

