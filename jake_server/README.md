# Jake Server (F9.1)

Il nodo Linux sempre acceso di Jake. Oggi fa una cosa sola: risponde a `GET /health` così Jake Windows sa se il
server c'è. Memoria, RAG, scheduler, sync e automazioni restano sul PC finché le fasi successive di F9 non li
spostano (vedi `ROADMAP_EXECUTION.md`, sezione F9).

- Solo libreria standard Python, nessuna dipendenza pip.
- Nessun comando, nessuna credenziale, nessun dato personale nella risposta.
- Solo LAN: niente port forwarding sul router, niente esposizione Internet. L'accesso da fuori casa arriverà con una
  VPN (Tailscale/WireGuard), non aprendo porte.

```json
{"status": "ok", "service": "jake-server", "version": "5.9", "protocol_version": 1, "node": "server", "uptime_s": 42}
```

## Avvio con Docker Compose (Ubuntu)

Dalla cartella `jake_server/` del repository:

```bash
docker compose up -d --build
docker compose ps                      # STATUS deve diventare "healthy" entro ~30 s
curl -s http://127.0.0.1:8770/health
```

Facoltativo, in un file `jake_server/.env` (non versionato):

```bash
JAKE_SERVER_PUBLISH_IP=192.168.1.13    # pubblica la porta solo sull'IP LAN invece che su tutte le interfacce
JAKE_SERVER_PUBLISH_PORT=8770          # porta sull'host
```

Aggiornare: `git pull && docker compose up -d --build`. Fermare: `docker compose down`. Log: `docker compose logs -f`.

Il container gira come utente non root, con filesystem in sola lettura, nessuna capability Linux, 128 MB di RAM e
riavvio automatico (`restart: unless-stopped`, quindi riparte anche dopo un reboot se il servizio Docker è attivo).

Nota firewall: le porte pubblicate da Docker non passano da `ufw`. Per limitare chi le raggiunge usa
`JAKE_SERVER_PUBLISH_IP` e la rete di casa; non serve (e non va) aprire nulla sul router.

## Avvio senza Docker (sviluppo)

```bash
JAKE_SERVER_HOST=127.0.0.1 JAKE_SERVER_PORT=8770 python3 -m jake_server   # dalla radice del repository
```

`JAKE_SERVER_HOST` vale `127.0.0.1` per default: ascoltare in LAN è una scelta esplicita (il container usa `0.0.0.0`).

## Collegare Jake Windows

Sul PC, in `config/settings.json`:

```json
"server_url": "http://192.168.1.13:8770"
```

oppure la variabile d'ambiente `JAKE_SERVER_URL`. Senza questa chiave Jake non fa nessuna richiesta. Con la chiave,
all'avvio Jake interroga `/health` in background (timeout 1,5 s, `server_timeout_seconds` per cambiarlo, massimo 5 s)
e scrive nel log se il server è raggiungibile; se è spento o irraggiungibile Jake funziona esattamente come prima.

## Perché un server separato e non il CompanionServer

`/health` non ha comandi né credenziali, quindi può rispondere in LAN senza TLS. Ogni endpoint futuro che legge o
cambia dati (sync, memoria, comandi) passerà dalla pipeline di `core/companion_server.py`: autenticazione per
dispositivo, `CompanionGuard` (capability, anti-replay, rate limit, audit) e TLS obbligatorio fuori dal loopback.
Questo servizio non va esteso con altri percorsi.
