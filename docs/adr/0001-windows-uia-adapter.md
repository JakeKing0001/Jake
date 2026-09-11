# ADR 0001 — Windows UI Automation adapter

- Stato: `Proposed`
- Data: 2026-09-11

## Contesto

Jake usa oggi screenshot, OCR, coordinate e `pyautogui`. Questo percorso non conosce ruoli,
stato, valore o identità stabile dei controlli e degrada con DPI, layout e finestre sovrapposte.
Windows UI Automation (UIA) offre un albero semantico, ma binding COM diretti, wrapper Python e
helper C++ hanno profili diversi di stabilità, latenza e packaging.

## Decisione

Introdurre un confine `UiaAdapter` isolato dal planner e dalle skill. Il primo spike usa COM
diretto in un processo helper con timeout; selector e azioni consumano solo DTO versionati.
OCR e coordinate restano fallback espliciti e osservabili, mai equivalenti a una prova UIA.
La scelta definitiva del binding resta sospesa fino al benchmark F3.2.

## Alternative considerate

- Wrapper Python UIA: sviluppo rapido, ma dipendenza e comportamento del wrapper diventano API.
- COM diretto nello stesso processo: meno dipendenze, ma un provider bloccato può fermare Jake.
- Helper C++: controllo e isolamento migliori, con costo iniziale e contract cross-language.
- Solo OCR/coordinate: già disponibile, ma non soddisfa accessibilità e verifica semantica.

## Conseguenze

Planner e verifier non dipendono da una libreria UIA specifica. Il processo helper aggiunge un
confine IPC e richiede gestione di timeout, crash, bitness e finestre elevate. Nessuna azione
viene dichiarata verificata dal solo fatto che l'API di input non ha sollevato eccezioni.

## Piano di migrazione

Definire DTO e fixture app; misurare COM, wrapper e helper; implementare lettura albero e
selector; aggiungere invoke/value/selection; infine spostare OCR e coordinate in fondo alla
fallback ladder senza rimuoverli.

## Criterio di revisione

Rivedere dopo 30 task ripetibili su Esplora file, browser e VS Code, oppure prima se il binding
scelto blocca il processo, perde eventi o non supera i test DPI/multi-monitor.
