# ADR 0002 — Sandbox runtime dei plugin

- Stato: `Proposed`
- Data: 2026-09-11

## Contesto

La Skill Forge prova codice generato in un processo separato e oggi può abbassarne il token a
Low Integrity. Questo limita le scritture su oggetti Medium, ma non limita rete, CPU, memoria,
figli o tutte le letture. Se le API Win32 falliscono, l'implementazione attuale degrada a un
processo normale con un avviso: accettabile per compatibilità di sviluppo, non per plugin non
fidati in produzione.

## Decisione

Adottare Low Integrity più Windows Job Object come baseline permanente: limiti di processo,
memoria e tempo, kill dell'intero job e nessun figlio fuori dal job. Il codice generato deve
fallire chiuso quando il profilo ristretto non è disponibile. Valutare AppContainer solo dopo
aver misurato compatibilità e costo operativo; blocklist e capability restano difese separate.

## Alternative considerate

- Solo processo/timeout: isola i crash ma conserva tutti i privilegi dell'utente.
- Solo Low Integrity: protegge molte scritture, non rete e risorse.
- AppContainer immediato: isolamento più forte, packaging e capability più complessi.
- VM/container: isolamento elevato, costo e disponibilità inadatti al percorso quotidiano.

## Conseguenze

Alcune skill oggi funzionanti verranno negate finché non dichiarano capability minime. Il
launcher deve distinguere `restricted`, `unavailable` e `failed`; un warning non basta. Job,
token, handle e file IPC diventano risorse con cleanup obbligatorio e fault test.

## Piano di migrazione

Aggiungere Job Object al probe esistente; impedire il fallback plain per codice non fidato;
separare profili read-only/network-denied; applicare gli stessi profili all'esecuzione, non solo
all'import; poi eseguire attack suite e canary prima di valutare AppContainer.

## Criterio di revisione

Rivedere se un test dimostra escape, accesso di rete non autorizzato o impossibilità di
supportare una capability necessaria; confrontare AppContainer prima del gate F8.
