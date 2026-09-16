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

## Fase 4.9.3 / F4.2.1 — trasparenza, click-through selettivo, no-activate

Finestra senza bordi e trasparente (`color: "transparent"` + `Qt.FramelessWindowHint`), sempre
sopra le altre (`Qt.WindowStaysOnTopHint`) e senza icona in barra applicazioni (`Qt.Tool`). Due
proprietà che Qt non espone tramite API cross-platform, isolate in `src/OverlayStyler.{h,cpp}`
(manipolazione diretta della HWND via Win32, guardata da `#ifdef Q_OS_WIN`):

- **No-activate** (`WS_EX_NOACTIVATE`, applicato una volta all'avvio): l'overlay non ruba mai il
  focus tastiera ad altre app quando diventa visibile o viene cliccato.
- **Click-through selettivo per pannello** (`WS_EX_TRANSPARENT`, attivato/disattivato a runtime):
  ciascuno dei cinque pannelli (`StatusPanel`/`Orb`/`ConversationPanel`/`QuickActions`/
  `CommandBar`) espone un proprio `hovered` tramite un `HoverHandler` interno; `Main.qml` aggrega
  i cinque in `pointerOverAnyPanel` e disattiva il click-through SOLO quando il puntatore è
  davvero sopra uno di essi. Fuori da tutti e cinque - inclusi i vuoti *tra* un pannello e
  l'altro, non solo il margine esterno - i click passano alla finestra sottostante come se
  l'overlay non esistesse.

**Limite superato rispetto alla prima versione di questo passo** (era grossolano - l'intera area
del `ColumnLayout`, vuoti compresi, contava come "pannello" - ora è per pannello davvero, vedi
sopra).

**Verificato in questo ambiente** (stessa tecnica e stessi limiti di sopra - compilazione ed
esecuzione reali, non lettura del codice): compila senza errori; l'eseguibile si avvia e resta in
esecuzione senza warning QML su stderr; collegamento reale confermato allo stesso modo di prima
(`event_bus.subscriber_count()` passa da 0 a 1 esattamente quando `JakeHud.exe` è in esecuzione
con un `CompanionServer` vero sulla porta 8765, torna a 0 alla sua chiusura).

**Non verificato** (limite dell'ambiente, non del codice): l'aspetto visivo della trasparenza
(nessuno screenshot possibile qui), se il click-through funzioni DAVVERO passando un click a una
finestra sottostante reale, se il no-activate impedisca DAVVERO il furto di focus da un'altra
app attiva, e la leggibilità dei pannelli senza sfondo proprio (`StatusPanel`/`QuickActions`) su
uno sfondo desktop arbitrario invece del riquadro scuro `#14161c` di prima - quest'ultimo è
esplicitamente materia della fase successiva (4.9.4/4.9.5, "vetro vero"), non di questo passo.

## Fase 4.9.3 / F4.2.2 — show/hide reale senza rubare focus

`HUD_SHOW`/`HUD_HIDE` (`core/hud_protocol.py::EventType`) prima cadevano nel ramo generico di
`JakeClient::handleEventLine()` che si limita a `setState(type)`: la finestra restava SEMPRE
visibile, con lo stato letteralmente scritto `"HUD_SHOW"`/`"HUD_HIDE"` (non riconosciuto da
`Orb.qml`, quindi mostrato col colore di default) - nessun nascondimento reale accadeva. Nuovo
segnale `JakeClient::visibilityRequested(bool)`, emesso separatamente per i due tipi.

**Nuova tecnica di verifica per questo track** (miglioramento rispetto ai passi precedenti, che si
fermavano a "compila ed esegue senza crash"): oltre a compilazione/esecuzione reali, un piccolo
script Python con `ctypes` interroga la finestra REALE in esecuzione dall'esterno -
`IsWindowVisible()`, `GetWindowLongW(GWL_STYLE/GWL_EXSTYLE)` - senza bisogno di vederla su uno
schermo. Questo ha permesso di trovare un **buco reale altrimenti invisibile**: assegnare
`window.visible = visible` in QML aggiornava la proprietà QML (letta indietro correttamente,
confermato con un log temporaneo) ma per QUESTA combinazione di flag (`Qt.Tool` +
`Qt.FramelessWindowHint` + `Qt.WindowStaysOnTopHint` + sfondo trasparente, che fa aggiungere a Qt
stesso `WS_EX_LAYERED`) non chiamava mai `ShowWindow` sulla HWND reale - la finestra restava
sempre `IsWindowVisible() == True` a dispetto di `window.visible == false`. Corretto con
`OverlayStyler::forceVisibility()`, `ShowWindow(hwnd, SW_SHOWNOACTIVATE / SW_HIDE)` esplicito -
`SW_SHOWNOACTIVATE` (non `SW_SHOW`) per non rubare il focus alla ricomparsa, insieme a
`makeNoActivate()` riapplicato per essere espliciti.

**Verificato per davvero, non più solo "non crasha"**: `IsWindowVisible()` interrogata
dall'esterno passa a `False` esattamente quando viene pubblicato `HUD_HIDE` e torna a `True`
esattamente quando viene pubblicato `HUD_SHOW` (correlazione temporale confermata, non solo
"succede prima o poi"); dopo l'intero ciclo hide→show, `GetWindowLongW(GWL_EXSTYLE)` conferma che
`WS_EX_NOACTIVATE`/`WS_EX_TOOLWINDOW`/`WS_EX_TOPMOST` sono ancora tutti presenti (il no-activate
di F4.2.1 sopravvive al ciclo). **Non verificato** (limite dell'ambiente, non del meccanismo di
verifica): l'aspetto visivo vero e proprio e se il no-activate impedisca DAVVERO il furto di
focus da un'altra finestra reale con un utente che interagisce - `IsWindowVisible`/gli extended
style sono osservabili da fuori, il comportamento percepito visivamente no.

Non ancora affrontato del resto di `F4.2.2`: nessun produttore reale di `HUD_SHOW` esiste ancora
nel progetto (solo `"exit"` è mappato a `HUD_HIDE` in `LEGACY_STATE_TO_EVENT_TYPE`) - il lato
consumatore qui costruito è pronto a riceverlo quando un produttore verrà aggiunto altrove.

## Fase 4.9.3 / F4.2.3 (Alt-Tab, prima fetta - VERIFICA) — nessun nuovo codice

"Definire comportamento Alt-Tab, desktop virtuali e fullscreen game": la stessa tecnica sopra
(`GetWindowLongW(GWL_EXSTYLE)` sulla finestra reale) conferma che `WS_EX_TOOLWINDOW` è già
presente - risultato automatico di `Qt.Tool` (impostato in F4.2.1), non qualcosa che richiedeva
codice nuovo. `WS_EX_TOOLWINDOW` è il flag Win32 che esclude una finestra da Alt-Tab e dalla
barra applicazioni: la parte "Alt-Tab" del requisito letterale è quindi già soddisfatta,
verificato invece di assunto dal nome del flag Qt.

**Deliberatamente non affrontato** (limite dichiarato, non nascosto, stessa decisione già presa
per AppContainer in F1.6.4): il comportamento sui desktop virtuali di Windows (mostrare l'overlay
su TUTTI i desktop virtuali richiederebbe `IVirtualDesktopManager`, un'interfaccia COM
**non documentata pubblicamente** da Microsoft, usata da tool come strumenti "always on top" di
terze parti mai stabile tra versioni di Windows) e il comportamento contro un gioco a schermo
intero in modalità ESCLUSIVA (dove il sistema operativo tipicamente sopprime le altre finestre
topmost per costruzione, non qualcosa che Jake deve implementare). Entrambi richiederebbero test
interattivi reali (un desktop virtuale vero, un gioco vero) che questo ambiente non può fare, ed
il secondo rischierebbe di introdurre codice fragile basato su API non documentate per un
beneficio non ancora richiesto da nessuno - rimandato a quando servirà davvero.

## Fase 4.9.3 / F4.2.4 — VERIFICA: nessun fallback necessario su Windows 10/11

"Aggiungere fallback finestra normale quando composition non è disponibile": verificato con
`DwmIsCompositionEnabled()` (l'API Win32 dedicata) chiamata per davvero su questa macchina -
`HRESULT=0` (successo), `enabled=True`. A differenza di Windows 7 (dove Aero/DWM poteva essere
disattivato dall'utente, es. col tema "Windows classico"), da Windows 8 in poi **la composizione
DWM è sempre attiva e non disattivabile** - `DwmIsCompositionEnabled()` esiste ancora solo per
compatibilità all'indietro e ritorna sempre `True` sui sistemi operativi che Jake supporta
(Windows 10/11, vedi i requisiti di sistema del progetto). Lo scenario che il fallback dovrebbe
gestire non può quindi verificarsi sul target reale: costruire un ramo "finestra normale opaca"
mai raggiungibile sarebbe codice morto speculativo, lo stesso principio già applicato altrove nel
progetto (Python) per non scrivere codice contro condizioni impossibili. **Nessun codice
scritto**, solo la verifica.

## F4.1.2 — schema condiviso, niente enum mantenuti a mano

Fino a questo incremento, `JakeClient::handleEventLine()` confrontava `type` con stringhe
letterali scritte a mano (`"USER_MESSAGE"`, `"HUD_SHOW"`...) - una copia del vocabolario di
`core/hud_protocol.py::EventType` mantenuta manualmente, senza alcuna garanzia che restasse
sincronizzata: un tipo aggiunto o rinominato lato Python poteva disallinearsi in silenzio.

Nuovo `tools/generate_hud_event_types.py`: legge l'enum VERO (non una copia) e genera
`HudEventTypes.h` (namespace `JakeHudEventType`, una costante `const char*` per membro).
`CMakeLists.txt` lo rigenera come build step PRIMA di compilare (`add_custom_command` +
`add_dependencies`, `find_package(Python3 ... REQUIRED)`) - il file generato non è mai committato
(già coperto da `hud/native/build/` in `.gitignore`, dato che vive sotto la build directory).
`JakeClient.cpp` usa ora `JakeHudEventType::USER_MESSAGE` ecc. invece delle stringhe letterali.

**Rischio verificato, non solo assunto**: `ERROR` è anche il nome di una macro Win32
(`wingdi.h`, valore `0`) - se questa translation unit avesse incluso `<windows.h>` senza
`WIN32_LEAN_AND_MEAN`/`NOGDI`, `JakeHudEventType::ERROR` si sarebbe rotto per sostituzione del
preprocessore. Verificato che NON succede compilando per davvero (nessun errore), non assunto
dalla documentazione Qt.

**Verificato**: `python -m unittest tests.test_generate_hud_event_types` (6 test, generazione
pura, nessuna toolchain C++ richiesta); ricompilato con successo (il passo
"Generating HudEventTypes.h..." appare nel log di build); ripetuta la stessa sequenza
HUD_HIDE→HUD_SHOW con `ctypes`/`IsWindowVisible()` di F4.2.2 - stesso comportamento corretto,
nessuna regressione introdotta dal refactor.

## F4.1.3 — meccanismo di resume lato server (prima fetta, solo Python)

"Gestire reconnect, resume dall'ultimo sequence id e snapshot iniziale" - prima fetta scelta
deliberatamente SOLO lato server (`core/event_bus.py`/`core/companion_server.py`), interamente
verificabile con test Python reali, senza ancora toccare `JakeClient.cpp` (stesso principio
"prima il meccanismo, poi l'adozione" già seguito altrove nel progetto).

Il server ora ricorda gli ultimi N eventi pubblicati (`EventBus._replay_buffer`) e legge l'header
SSE standard `Last-Event-ID` su `GET /events`: un client che lo manda riceve prima gli eventi
persi durante l'interruzione, poi continua dal vivo senza soluzione di continuità. Ogni evento
porta ora anche una riga `id: <sequence_id>` (formato SSE standard) prima di `data:`.

**Verificato che `JakeClient.cpp` non si rompe con questa riga in più**: il suo parser cerca solo
righe che iniziano per `"data: "` e ignora silenziosamente il resto (già vero per costruzione,
non una supposizione - verificato ricompilando E facendo girare per davvero l'eseguibile
esistente con la stessa tecnica `ctypes`/`IsWindowVisible()` di F4.2.2, nessuna regressione nel
ciclo HUD_HIDE→HUD_SHOW).

**Non ancora affrontato** (dichiarato apertamente): `JakeClient.cpp` non implementa ancora un
vero auto-reconnect (nessun retry automatico dopo una disconnessione, `onEventStreamFinished()`
si limita oggi a segnalarla) né invia l'header `Last-Event-ID` per sfruttare il meccanismo appena
costruito lato server - un passo successivo separato, non affrontato qui.

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
- **Distribuisce il runtime Qt.** Il passo `POST_BUILD` esegue il `windeployqt` appartenente alla
  stessa installazione Qt usata da CMake e copia DLL, plugin e moduli QML accanto a
  `JakeHud.exe`; la cartella `build/` risultante non richiede Qt nel `PATH`.
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
- Un interprete Python 3 raggiungibile da CMake (F4.1.2: rigenera `HudEventTypes.h` come build
  step - lo stesso venv del resto di Jake basta, `find_package(Python3)` lo trova da solo).

```
# Da un "x64 Native Tools Command Prompt for VS" (o dopo aver chiamato vcvarsall.bat x64):
cmake -S hud/native -B hud/native/build -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/6.7.3/msvc2019_64
cmake --build hud/native/build
```

Poi, senza modificare il `PATH`, con **il server companion di Jake già avviato**
(`companion_server_enabled: true` in `config/settings.json`, o il probe minimale descritto
sopra):

```
hud/native/build/JakeHud.exe
```
