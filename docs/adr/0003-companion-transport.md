# ADR 0003 — Trasporto HUD e companion

- Stato: `Proposed`
- Data: 2026-09-11

## Contesto

Il companion usa oggi HTTP+SSE su `127.0.0.1`, disattivato per default, con bearer token
opzionale. È semplice e interoperabile, ma il trasporto locale non autentica il processo peer e
l'esposizione LAN senza TLS renderebbe token, comandi ed eventi intercettabili. HUD locale e
telefono hanno confini di fiducia differenti.

## Decisione

Usare un canale locale autenticato (named pipe Windows con ACL dell'utente) per l'HUD e un
trasporto cifrato HTTPS+SSE per companion remoti. HTTP+SSE loopback resta adapter transitorio e
fixture di contract test. Ogni client usa protocol version, device identity, token a scadenza e
lease; nessuna approvazione attraversa automaticamente profili o dispositivi.

## Alternative considerate

- HTTP+SSE ovunque: semplice, ma insufficiente su LAN senza TLS e pairing.
- WebSocket: bidirezionale, richiede dipendenza e gestione reconnect più complessa.
- Named pipe per tutto: ottima in locale, non attraversa la rete.
- gRPC: schema e streaming forti, costo di toolchain sproporzionato alla baseline.

## Conseguenze

Core mantiene un contratto di eventi unico ma più adapter di trasporto. Pairing, certificati,
replay protection e reconnect diventano responsabilità esplicite. Il client deve mostrare stato
incompatibile/offline invece di interpretare payload di versione sconosciuta.

## Piano di migrazione

Congelare JSON Schema e contract test; estrarre l'handler dal server HTTP; aggiungere named pipe
per HUD; introdurre pairing e TLS per mobile; mantenere loopback solo per debug finché tutti i
client sono migrati.

## Criterio di revisione

Rivedere dopo il prototipo named pipe e il primo companion mobile, oppure se SSE non soddisfa
latenza, backpressure o riconciliazione offline misurate.
