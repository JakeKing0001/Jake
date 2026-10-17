"""Jake Server (F9.1, Distributed / Always-On Jake): il nodo Linux sempre acceso, separato dal runtime desktop.

Oggi espone SOLO `GET /health` (vedi `jake_server/health.py`): serve a far sapere a Jake Windows se il server c'e',
nient'altro. Non ospita ancora memoria, RAG, scheduler, sync, automazioni o modelli: arriveranno nelle fasi
successive di F9, e ogni endpoint che legge o cambia dati passera' dalla pipeline gia' esistente di
`core/companion_server.py` (autenticazione, CompanionGuard, TLS obbligatorio fuori dal loopback), non da qui.

Gira su Linux (Docker, vedi `jake_server/README.md`) e importa da `core/` solo moduli di sola libreria standard
(`core.version`, `core.companion_guard`), cosi' l'immagine non trascina le dipendenze Windows del desktop."""

SERVICE_NAME = "jake-server"
NODE_ROLE = "server"
DEFAULT_PORT = 8770
