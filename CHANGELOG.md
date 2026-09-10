# Changelog

Le modifiche rilevanti di Jake sono registrate qui. La versione corrente e la versione del
protocollo vivono in `config/release.json`; i dettagli tecnici precedenti a questo changelog
restano nell'audit storico `ROADMAP.md`.

## [Unreleased]

### Added

- Diagnostica CI persistente per ogni versione Python.
- Test di fault deterministici per la scansione delle directory nel `PATH`.

### Changed

- La versione di prodotto e quella del protocollo sono lette da un manifest condiviso.
- Gli eventi HUD dichiarano la propria `schema_version`.

### Fixed

- La scoperta delle applicazioni ignora directory e file del `PATH` che diventano inaccessibili.
- `search_paths=[]` disabilita davvero la scansione dei percorsi del menu Start.

### Security

- Nessuna nuova capability o trasmissione di dati; i fault I/O degradano senza ampliare accessi.

## [5.9] - 2026-09-10

### Added

- Handoff iniziale tra dispositivi, companion server locale e HUD nativo.
- Policy, ledger, verifica degli effetti, sandbox iniziale e controlli di autenticazione.
- Memoria locale, automazioni con budget e Skill Forge controllata.

### Security

- Token e passphrase sensibili cifrati a riposo con Windows DPAPI.
- Kill switch centrale, protezione dei percorsi e filtri per comandi distruttivi.
