# Jake HUD nativo (fase 4.9, HUD Engine 2.0)

Prototipo QML per la fase 4.9.2 della [roadmap esecutiva](../../ROADMAP_EXECUTION.md): separare l'interfaccia grafica dal core Python,
riscrivendola in C++/Qt6/QML per un vero blur/compositing/trasparenza nativi che PySide6 non può
offrire (vedi la nota nella roadmap salvata). Parla con Jake **solo** tramite il server companion
HTTP+SSE (`core/companion_server.py`, fase 4.9.1/5.8/5.9): non importa `core/jake_core.py`, non sa
niente di Ollama, agenti o memoria — esattamente il punto della fase "UI separation".

## Cosa c'è oggi (4.9.2 — "prima deve funzionare")

- `Orb.qml` — cerchio centrale, colore/pulsazione legati allo stato (`JakeClient.state`).
- `CommandBar.qml` — invia testo a `POST /command`.
- `ConversationPanel.qml` — cronologia da `USER_MESSAGE`/`JAKE_MESSAGE`/`NOTIFICATION`.
- `StatusPanel.qml` — connessione, stato corrente, dispositivo attivo (v5.9 handoff).
- `QuickActions.qml` — scorciatoie per comandi frequenti.
- `src/JakeClient.{h,cpp}` — client Qt Network: `GET /status`, `GET /events` (Server-Sent
  Events, parsing manuale — Qt non ha un client SSE nativo), `POST /command`,
  `POST /devices/<id>/claim`.

Finestra normale, bordata, opaca. **Non** ancora: overlay trasparente/click-through (4.9.3), vetro
vero con blur/rifrazione (4.9.4/4.9.5), stato reattivo del vetro (4.9.6), Orb 2.0 con particelle
(4.9.7), pannelli contestuali per tipo di task (4.9.8), transizioni fluide (4.9.9), multi-monitor
(4.9.10). Ognuna di quelle è un passo successivo, deliberatamente non affrontato qui.

## Stato di verifica

A differenza di tutto il resto di Jake (Python, con test automatici in `tests/`), questo codice
**non ha una suite di test**: C++/QML non fanno parte della toolchain di test del progetto. È
stato però compilato ed eseguito davvero in questo ambiente il giorno in cui è stato scritto
(MSVC 19.51 via Visual Studio Build Tools, Qt 6.7.3 msvc2019_64, CMake+Ninja), non solo scritto
alla cieca:

- **Compila ed esegue senza errori.** `cmake --build` termina senza errori (dopo aver corretto un
  bug reale trovato in questo modo: `JakeClient.h` non era nel percorso di inclusione per il file
  auto-generato di registrazione dei tipi QML — vedi `target_include_directories` in
  `CMakeLists.txt`). L'eseguibile si avvia e resta in esecuzione senza warning QML su stderr.
- **Si collega davvero al server companion vero.** Avviato `CompanionServer` reale (lo stesso
  `core/companion_server.py`, non un doppio finto) su una porta reale: `event_bus.
  subscriber_count()` è passato da 0 a 1 nel momento in cui l'eseguibile compilato ha aperto la
  connessione a `GET /events`, confermando che il client C++ parla davvero HTTP/SSE con il server
  Python reale, non solo che il codice "sembra giusto".

Quello che **non** è verificato: l'aspetto visivo (nessuno screenshot, nessuna GUI osservabile in
questo ambiente), l'interazione utente reale con mouse/tastiera, il comportamento su una macchina
diversa da questa, e ovviamente tutte le fasi successive (4.9.3+) non ancora scritte.

## Come ricompilare

Serve una toolchain C++/Qt6 completa (non inclusa in requirements.txt: `hud/native/` è un
progetto CMake separato dal resto di Jake, che resta puro Python):

- Visual Studio Build Tools (o Visual Studio) con il carico di lavoro "Sviluppo di applicazioni
  desktop con C++" (MSVC + Windows SDK).
- CMake ≥ 3.21 (incluso nei Build Tools più recenti).
- Qt 6.5+ per MSVC (es. via [aqtinstall](https://github.com/miurahr/aqtinstall):
  `pip install aqtinstall && aqt install-qt windows desktop 6.7.3 win64_msvc2019_64 -O C:/Qt`).

```
# Da un "x64 Native Tools Command Prompt for VS" (o dopo aver chiamato vcvarsall.bat x64):
cmake -S hud/native -B hud/native/build -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/6.7.3/msvc2019_64
cmake --build hud/native/build
```

Poi, con `Qt/6.7.3/msvc2019_64/bin` in PATH (per le DLL Qt) ed **il server companion di Jake già
avviato** (`companion_server_enabled: true` in `config/settings.json`, o il probe minimale
descritto sopra):

```
hud/native/build/JakeHud.exe
```
