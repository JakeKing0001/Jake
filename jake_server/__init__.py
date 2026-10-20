"""Jake Server (F9.1, Distributed / Always-On Jake): il nodo Linux sempre acceso, separato dal runtime desktop.

Oggi espone SOLO `GET /health` e `GET /capabilities` (vedi `jake_server/health.py`): servono a far sapere a Jake
Windows se il server c'e', con quale protocollo e cosa offre, nient'altro. Non ospita ancora memoria, RAG, scheduler,
sync, automazioni o modelli: arriveranno nelle fasi successive di F9, e ogni endpoint che legge o cambia dati passera'
dalla pipeline gia' esistente di `core/companion_server.py` (autenticazione, CompanionGuard, TLS obbligatorio fuori dal
loopback), non da qui.

Gira su Linux (Docker, vedi `jake_server/README.md`) e importa da `core/` solo moduli di sola libreria standard
(`core.version`, `core.companion_guard`), cosi' l'immagine non trascina le dipendenze Windows del desktop."""

SERVICE_NAME = "jake-server"
NODE_ROLE = "server"
DEFAULT_PORT = 8770
# F9.2: cio' che questo nodo offre davvero, pubblicato da GET /capabilities. Un nome entra qui solo quando l'endpoint
# corrispondente esiste: Jake Windows usera' un servizio del server SOLO se il nome compare in questa lista.
CAPABILITIES = ("health",)
