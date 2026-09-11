# ADR 0004 — Cifratura di segreti e dati sensibili

- Stato: `Accepted`
- Data: 2026-09-11

## Contesto

Jake conserva token, passphrase, memoria, log e ricevute sul PC. DPAPI protegge già i segreti
configurati legandoli all'account Windows; SQLite, JSONL e screenshot non sono cifrati a livello
applicativo. La cifratura completa duplicata nell'app può introdurre chiavi fragili, mentre il
volume OS può proteggere il disco spento ma non un processo già eseguito come l'utente.

## Decisione

Usare DPAPI per segreti e chiavi dati, cifratura per campo per valori di memoria classificati
sensibili e BitLocker/cifratura OS come requisito raccomandato del dispositivo. Metadati minimi
necessari a query e retention possono restare in chiaro. Nessuna chiave master viene hardcoded,
sincronizzata o derivata dalla voce.

## Alternative considerate

- Solo BitLocker: protegge il dispositivo spento, non file letti dalla sessione utente.
- Database interamente cifrato: confine semplice, ma migrazione, ricerca e gestione chiave più
  invasive.
- Cifratura per campo: minimizza dati esposti e preserva query, con schema più complesso.
- Vault cloud: facilita sync, viola il default local-first e amplia il threat model.

## Conseguenze

Backup copiati su un altro account possono non essere decifrabili senza export esplicito. I
campi cifrati non sono ricercabili direttamente e richiedono indici derivati non sensibili. Log,
errori e telemetria devono redigere plaintext prima della serializzazione.

## Piano di migrazione

Inventariare sensibilità per campo; introdurre envelope versionato DPAPI; migrare in transazione
con backup; aggiungere export cifrato con passphrase separata; verificare cancellazione,
rotazione, database corrotto e ripristino su account diverso.

## Criterio di revisione

Rivedere prima della sync mobile, quando entra una nuova classe di dato sensibile o se DPAPI non
copre un ambiente supportato. Qualunque cambio richiede threat model e piano di recupero chiavi.
