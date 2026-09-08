# Jake — roadmap verso un vero Jarvis

Aggiornata l'8 settembre 2026. Questo documento separa ciò che è già stato deciso o
implementato da ciò che viene proposto ora. La cronologia Git e il codice sono la fonte di
verità: una funzione non è considerata conclusa solo perché esiste un primo prototipo.

## La stella polare

Jake non deve essere soltanto una chat con microfono. Deve diventare un sistema operativo
personale sopra Windows: presente ma non invadente, capace di capire il contesto, agire nel
mondo digitale e fisico, verificare il risultato, ricordare ciò che serve e proteggere il
proprietario anche dai propri errori.

Il sogno è realizzato quando questa sequenza è normale, non una demo:

> ascolta → comprende il contesto → propone o pianifica → chiede permesso quando serve →
> agisce → osserva il risultato → recupera dagli errori → spiega cosa ha fatto → impara.

Principi non negoziabili:

- **Local-first e offline-capable.** Il cloud è un'opzione dichiarata, mai una dipendenza
  nascosta.
- **Affidabilità prima dell'autonomia.** Jake non deve fare più cose; deve portarle a termine
  con prove osservabili.
- **Minimo privilegio.** Ogni agente, skill e dispositivo riceve solo i permessi necessari.
- **Azioni visibili, annullabili e attribuibili.** Anteprima per le azioni sensibili, ricevuta
  finale e undo quando tecnicamente possibile.
- **Memoria sotto il controllo dell'utente.** Provenienza, scadenza, modifica, esportazione e
  cancellazione devono essere comprensibili.
- **Presenza discreta.** Proattività utile, con modalità silenziose e un budget di interruzioni.
- **Degrado elegante.** Se manca GPU, rete, visione o un'integrazione, Jake continua con una
  modalità ridotta e spiega il limite.

## Legenda

- ✅ **Concluso:** implementato e coperto da una verifica adeguata al rischio.
- 🟡 **Parziale:** esiste una prima fetta utile, ma la promessa della fase è più ampia.
- 🧪 **Prototipo:** funziona in condizioni controllate; non è ancora prodotto.
- ⬜ **Da fare:** deciso in precedenza ma non implementato.
- ✨ **Nuova proposta:** non risultava nella roadmap ricostruita; viene aggiunto qui.

## Cosa c'era nella roadmap decisa

Questa è la ricostruzione fedele dei commit e delle note già presenti nel repository.

| Fase | Stato | Cosa esiste davvero | Cosa manca per chiuderla |
|---|---:|---|---|
| 3.2 Reliability & Architecture | ✅ | Core modulare, catalogo skill, 5 livelli di rischio, gate centrale di conferma | Mantenere la classificazione come quality gate |
| 3.3 Agent Engine 2.0 | 🟡 | Agente a passi con retry, verifica, timeout e rollback filesystem | Idempotenza generale, ricevute di effetto, replay, recovery per app e web |
| 3.4 Memory 2.0 | 🟡 | Dedup semantico e query temporali nell'API | Linguaggio naturale per il tempo, conflitti, consolidamento, provenienza e scadenza |
| 3.5 World Context Engine | 🟡 | Finestra attiva, finestre aperte e anteprima clipboard | Audio, rete, browser, progetto, calendario, posizione/presenza e contesto per-app |
| 3.6 Vision 2.0 | 🟡 | OCR, modello visivo e confronto pixel prima/dopo il click | Comprensione strutturata della scena, regioni, oggetti, privacy mask e benchmark |
| 3.7 Computer Use Engine | 🟡 | `ComputerAgent` per osservare, localizzare e cliccare | UI Automation, tastiera/scroll/drag unificati, aspettative di stato e recupero |
| 4.0 Conversational Intelligence | 🟡 | Cronologia recente nel classificatore e nell'agente | Ellissi robuste, dialoghi lunghi, riferimenti multipli, interruzioni e correzioni live |
| 4.1 Voice Natural 2.0 | 🟡 | Wake word flessibile, VAD, Whisper locale, follow-up, TTS online/offline | Barge-in, cancellazione eco, streaming, diarizzazione e test su hardware reale |
| 4.2 Proactive Intelligence | 🟡 | Avvisi per batteria, disco e todo dimenticate | Eventi ricchi, priorità apprese, suggerimenti contestuali, obiettivi e routine |
| 4.3 Notification/Priority | ✅ | Normal, DND, gaming, studio, riunione, sonno; coda e rilascio | UI di controllo, riepilogo e politiche per contatto/dispositivo |
| 4.4 Personal Knowledge Graph | 🟡 | Relazioni tipizzate tra ricordi e richiamo a un salto | Entità automatiche, tempo, provenienza, conflitti, viste e ragionamento multi-hop |
| 4.5 NEST Deep Integration | ✅ | Ricerca file ibrida, semantica e letterale | Benchmark di qualità, indicizzazione incrementale e filtri di privacy |
| 4.9.1 UI separation | ✅ | Protocollo eventi, event bus e companion server HTTP/SSE locale | Versionamento dello schema e compatibilità tra client |
| 4.9.2 HUD nativo | 🧪 | Prototipo C++/Qt6/QML con orb, comando, conversazione, stato e quick action | Test automatici, verifica visiva, packaging e uso quotidiano |
| 4.9.3 | ⬜ | — | Overlay trasparente e click-through nativo |
| 4.9.4–4.9.5 | ⬜ | — | Vetro vero: blur, rifrazione, compositing ed effetti GPU sobri |
| 4.9.6 | ⬜ | — | Vetro e colori reattivi allo stato di Jake |
| 4.9.7 | ⬜ | — | Orb 2.0 con animazione/particelle guidate da voce e stato |
| 4.9.8 | ⬜ | — | Pannelli contestuali per tipo di attività |
| 4.9.9 | ⬜ | — | Transizioni fluide e interrupt-safe |
| 4.9.10 | ⬜ | — | Multi-monitor, DPI, accessibilità e layout persistenti |
| 5.0–5.2 Multi-Agent | 🟡 | Orchestratore con agente generale, coding e research | Routing semantico, budget, isolamento, collaborazione e valutazione per dominio |
| 5.3 Self-Improvement | 🟡 | Fucina con AST checks, allowlist, subprocess e conferma | Sandbox OS, permessi per capability, firma, test generati e rollback versione |
| 5.4–5.5 Security & Identity | 🟡 | Policy rischio e passphrase opzionale per azioni admin | Vault, Windows Hello/passkey, pairing, ruoli, audit e protezione da prompt injection |
| 5.6 Privacy Engine | 🟡 | Modalità privata e purge esplicito della cronologia | Cifratura a riposo, classificazione dati, retention per categoria e privacy dashboard |
| 5.7 Home/IoT | 🟡 | Home Assistant: elenco e on/off/toggle per nome | Aree, scene, sensori, automazioni, energia, voce satellitare e conferme fisiche |
| 5.8 Mobile Companion | 🟡 | Trasporto locale riutilizzabile da un client | L'app mobile non esiste ancora; mancano pairing, autenticazione, notifiche e audio |
| 5.9 Ambient Computing | 🟡 | Registro dispositivi e claim/release della sessione | Presenza reale, elezione dispositivo, handoff audio/conversazione e continuità sicura |

Non ci sono evidenze versionate per fasi 4.6–4.8: non vengono inventate retroattivamente. I
nuovi lavori usano filoni `F0–F8`, più chiari delle vecchie versioni sovrapposte.

## Fotografia onesta del progetto

Jake ha già circa 200 intent/skill, un agente a passi, memoria SQLite, visione, automazione PC,
voce, un HUD PySide6 completo e un prototipo HUD nativo. È molto oltre un proof of concept.
Tuttavia, oggi non è ancora un Jarvis quotidiano per questi motivi:

1. Il README riportava ancora “Jake 3.1”, mentre i commit dichiarano fasi fino alla 5.9.
2. La suite Python ha 306 test; durante questo audit una corsa completa ha avuto un errore di
   connessione Windows nel test HTTP `release`, seguito da cinque esecuzioni isolate riuscite.
   Va trattato come possibile flake finché non viene eliminato.
3. Il client C++/QML non ha test, screenshot golden, CI Windows né pacchetto installabile.
4. Il controllo schermo usa soprattutto pixel/OCR/input simulato: manca l'albero semantico di
   Windows UI Automation, più stabile e verificabile.
5. Il server companion è correttamente locale e disattivato per default, ma non è pronto per
   la LAN: non ha pairing, identità client, TLS, rate limit o autorizzazioni per dispositivo.
6. La passphrase è un primo gate, non un sistema d'identità. I segreti e i database non sono
   ancora protetti a riposo.
7. La proattività è limitata a tre segnali; memoria temporale e grafo esistono ma non formano
   ancora un vero modello del mondo personale.
8. Il routing multi-agente è lessicale e gli agenti specializzati sono profili dello stesso
   ciclo, non unità isolate con budget, inbox, risultati tipizzati e permessi distinti.
9. Mancano benchmark end-to-end, telemetria locale, replay delle sessioni, installer, updater,
   rollback di release e una matrice hardware supportata.

## Ordine corretto di costruzione

La catena critica è:

`F0 Baseline verde` → `F1 Core affidabile e sicuro` → `F2 Voce + F3 Computer Use + F4 HUD`
→ `F5 Memoria e contesto` → `F6 Proattività` → `F7 Companion ambientale` →
`F8 Auto-miglioramento ed ecosistema`.

Voce, Computer Use e HUD possono avanzare in parallelo solo dopo i contratti di F1. La
proattività deve arrivare dopo verifica, permessi e memoria: autonomia senza questi tre
ingredienti moltiplica gli errori.

## F0 — Baseline verde e una sola verità

**Priorità: P0. Obiettivo: rendere misurabile ciò che già esiste.**

- ✅ Risolto e stressato il flake del companion server: la causa vera era `_handle_release`
  (`core/companion_server.py`) che non leggeva mai il body della richiesta, a differenza di
  `_handle_claim`/`_handle_command` - su Windows, chiudere una connessione TCP con byte non letti
  nel buffer di ricezione fa rispondere con un RST invece di una FIN pulita, da cui il
  `ConnectionAbortedError [WinError 10053]` intermittente (~10% delle richieste, riprodotto con
  1600+ chiamate dirette e con `tests/test_companion_server.py` fino a fallire alla run 15/20
  prima del fix). Corretto facendo drenare il body anche a `_handle_release`; verificato con 20
  esecuzioni consecutive della suite dopo il fix, tutte verdi.
- 🟡 CI Windows aggiunta (`.github/workflows/ci.yml`): matrice Python 3.11/3.12, `ruff check`,
  `mypy` selettivo, `python -m compileall`, la suite unitaria, più un job separato che compila il
  prototipo HUD nativo e verifica che il binario resti in esecuzione (non è ancora una suite di
  test C++/QML, che non esiste - vedi hud/native/README.md). Verificata eseguendo localmente ogni
  singolo passo (ruff/mypy/compileall/unittest tutti verdi); il workflow stesso non è ancora
  stato eseguito su GitHub Actions perché richiede un push, quindi resta da confermare lì.
- ✅ CMake Presets (`hud/native/CMakePresets.json`, `binaryDir` fisso) e rilevamento Qt per
  `GLOB_RECURSE` di `Qt6Config.cmake` sotto le cartelle Qt comuni, invece di un elenco di versioni
  fisse (`hud/native/CMakeLists.txt`). Motivato da un incidente reale: il commit "problem with
  cmake" dell'8 settembre aveva committato per errore un `build/` alla radice del repo
  (l'estensione CMake Tools di VS Code configurava lì senza un preset che fissasse `binaryDir`) -
  rimosso dal tracking, aggiunto a `.gitignore`, e `.vscode/settings.json` ora imposta
  `cmake.buildDirectory` esplicitamente. Un bug reale di sintassi (`$ENV{PROGRAMFILES(X86)}`, non
  valido: le parentesi non sono ammesse in un nome referenziato con `$ENV{...}`) è stato trovato e
  corretto configurando davvero, non solo leggendo il codice. Verificato end-to-end su questa
  macchina (Qt 6.7.3 msvc2019\_64, MSVC 19.51 via Visual Studio Build Tools, CMake standalone):
  `cmake --preset windows-ninja` configura e trova Qt sia con `-DCMAKE_PREFIX_PATH` esplicito sia
  con il solo rilevamento automatico, `cmake --build --preset windows-ninja` compila 34/34 target
  senza errori, e il binario risultante si avvia e resta in esecuzione oltre 3 secondi senza
  output su stderr.
- ✅ Dipendenze divise in `requirements/{base,voice,gui,hud,gpu,dev}.txt`, ciascuna pinnata alla
  versione esatta verificata in questo ambiente; `requirements.txt` e `requirements-gpu.txt` alla
  radice restano wrapper `-r` per compatibilità con `setup.ps1`. Hash-lock vero aggiunto
  (`requirements/all.lock.txt` per base+voice+gui+hud+dev insieme, `requirements/gpu.lock.txt` a
  parte, generati con `pip-compile --generate-hashes`, vedi `requirements/README.md`), usato dalla
  CI con `pip install --require-hashes`. Compilarli PER GRUPPO invece che insieme aveva prodotto
  un conflitto reale (due versioni diverse di `numpy`, una fissata in `hud.txt` e una transitiva
  da `voice.txt`) scoperto provando davvero a installarli assieme con `--require-hashes`, non solo
  leggendo l'output di `pip-compile` - corretto compilando `all.lock.txt` in un'unica invocazione.
- La tabella delle fasi in cima a questo documento è già, di fatto, la matrice
  "promesso / implementato / testato" per fase. ✅ Aggiunta anche una singola stringa di versione
  (`core/version.py`, `VERSION = "5.9"` - la fase più alta già raggiunta nella cronologia Git
  secondo la ricostruzione di questo documento): prima `main.py` mostrava ancora "Jake 3.0" nel
  banner `--help`, lo stesso problema del README con "Jake 3.1" ma mai corretto qui. Ora
  `main.py` (banner e avvio in modalità testo) e `GET /status` del companion server la leggono
  da lì, invece di raccontare due storie diverse.
- 🟡 Benchmark ripetibili aggiunti per NLU, agente e percezione dello schermo (`benchmarks/`,
  vedi `benchmarks/README.md`): `bench_nlu.py` (accuratezza/latenza del classificatore con
  leave-one-out sulla corsia esatta, ~82% su un campione di 60 frasi, seed 7, con Ollama vero),
  `bench_agent.py` (3 compiti composti sicuri, nessun effetto fuori da una cartella temporanea),
  `bench_computer_use.py` (screenshot/OCR/finestre, solo lettura). `bench_stt.py` esiste e
  funziona ma richiede un file audio vero passato a mano (il repository non versiona
  registrazioni per privacy). Wake word resta non misurabile senza un vero microfono e una
  sessione dal vivo (vedi i criteri di uscita di F2); l'accuratezza del click resta non misurata
  (serve il dataset di task reali della fase F3, non ancora costruito) - dichiarato esplicitamente
  in `benchmarks/README.md` invece di essere inventato.
- ✅ Log strutturati locali introdotti (`core/logger.py`: `new_trace_id()`, `get_action_logger()`,
  `log_action()` - JSONL separato in `data/jake_actions.jsonl` con trace_id, durata, modello,
  skill, decisione di rischio e risultato; `verified` resta assente invece di un valore inventato
  quando nessuno ha davvero controllato l'effetto, coperto da `tests/test_logger.py`). Rispetta la
  modalità privata (nessun trace_id né contenuto scritto). Collegato sia al percorso a comando
  singolo (`JakeCore._execute_command`) sia al ciclo dell'agente a passi (`core/agent.py`,
  `TaskAgent.run`/`_log_step`), con lo stesso `trace_id` condiviso tra tutti i passi di un
  compito composto (propagato da `JakeCore._run_agent` tramite `JakeOrchestrator.run`).
  `verified` è `True`/`False` solo per gli intent con un controllo indipendente vero
  (`CREATE_PATH`/`RENAME_PATH`/`MOVE_PATH`/`DELETE_PATH`, vedi `execution_safety.verify_effect`),
  assente per tutti gli altri invece di ereditare il default "nessuna verifica disponibile" di
  quella funzione come se fosse una prova - coperto da `tests/test_agent.py::StructuredLoggingTests`.
  Anche `PlanExecutor` (il vecchio esecutore a piano fisso: ripiego in `JakeCore._try_plan`, e
  usato da `TriggerScheduler` per le automazioni che partono da sole) scrive lo stesso formato di
  record con lo stesso `trace_id` condiviso tra i passi - `tests/test_plan_executor.py` (nuovo,
  il modulo non aveva ancora una suite dedicata).
- ✅ Replay anonimizzato/deterministico delle sessioni fallite (`core/session_recorder.py`,
  `tools/replay_session.py`). Disattivato per default (`session_recording_enabled`); quando
  acceso, ogni azione fallita (non le conferme in attesa, quelle non sono bug) viene scritta in
  `data/jake_sessions.jsonl` con intent e parametri REDATTI (valori stringa sostituiti da
  segnaposto "\<str:N caratteri\>") a meno che `session_recording_verbatim` non sia acceso anche
  lui - una scelta esplicita e locale per chi sta debuggando un fallimento vero su questa
  macchina. `tools/replay_session.py --replay` rilancia i fallimenti verbatim con la stessa
  `SkillRegistry` vera (bypassando NLU/agente: l'intent e' gia' noto, e' questo che lo rende
  deterministico) e confronta l'errore di allora con quello di adesso. Collegato agli stessi tre
  punti del logging strutturato (`JakeCore._execute_command`, `TaskAgent`, `PlanExecutor`, un solo
  `SessionRecorder` condiviso). Verificato davvero, non solo testato in isolamento: un `FIND_FILE`
  fallito con `JakeCore` reale e' stato registrato, poi rieseguito da `tools.replay_session
  --replay`, confermando lo stesso `NOT_FOUND` sia in modalita' verbatim sia (senza rieseguire)
  in modalita' redatta. Coperto da `tests/test_session_recorder.py` e da
  `SessionRecorderWiringTests` in `tests/test_agent.py`/`tests/test_plan_executor.py`.
  Un effetto collaterale scoperto e corretto per strada: aggiungere `log_action` incondizionato a
  `TaskAgent`/`PlanExecutor` faceva si' che ESEGUIRE I TEST ESISTENTI scrivesse davvero su
  `data/jake_actions.jsonl` del contributore (nessun test costruiva `SkillRegistry()` reale prima
  d'ora, quindi nessuno lo aveva mai notato) - risolto con `tests/__init__.py`, che reindirizza i
  percorsi di log verso una cartella temporanea per l'intera sessione di test; richiede pero'
  `python -m unittest discover -s tests -t .` (non solo `-s tests`) perche' Python importi
  davvero `tests` come pacchetto - README, CI e i due docstring che citavano il comando vecchio
  sono stati aggiornati insieme.
- 🟡 Hardware minimo/consigliato documentato nel README (nuova sezione "Hardware"): dimensioni dei
  modelli misurate davvero (`ollama list`, cache locale di `faster-whisper`: ~6,5 GB il set minimo
  CPU, ~18,8 GB con tutti i modelli opzionali), GPU/VRAM presa dalla macchina di sviluppo
  (`nvidia-smi`: RTX 4060 Laptop, 8 GB), VRAM occupata a runtime misurata con `ollama ps` (~5 GB
  con `qwen2.5:7b` + `nomic-embed-text` caricati insieme su GPU), RAM minima citata come soglia
  generica pubblica di Ollama per modelli 7B, non come benchmark specifico di Jake. Un tentativo
  di misurare anche l'impronta RAM del processo Python di Jake (`Get-Process` su un job in
  background con `JakeCore()` costruito) non ha prodotto un numero affidabile (il processo
  osservato non era quello giusto, isolamento di sessione di `Start-Job`) - dichiarato
  esplicitamente come non misurato invece di pubblicare un numero incerto. Resta quindi 🟡 e non
  ✅: manca ancora quel numero e un profilo di latenza end-to-end con voce+NLU+agente insieme in
  un'unica sessione.

**Criterio di uscita:** 20 suite consecutive verdi; build nativa riproducibile su macchina
pulita; smoke test installazione/avvio/arresto; dashboard locale con errori e latenze.

- ✅ 20 suite consecutive verdi: il flake del companion server e' stato verificato risolto con 20
  esecuzioni consecutive (tutte verdi) dopo il fix; la suite completa e' stata eseguita a ripetizione
  durante questa sessione (mai un fallimento, sempre a partire da `python -m unittest discover -s
  tests -t .`).
- ✅ Build nativa riproducibile: verificato su questa macchina con CMakePresets.json e
  rilevamento Qt dinamico, sia con `-DCMAKE_PREFIX_PATH` esplicito sia a rilevamento automatico
  (non "macchina pulita" in senso letterale - una seconda macchina fisica non e' disponibile qui
  - ma la logica di rilevamento non dipende piu' da percorsi fissi a una versione specifica, che
  era il problema originale).
- ✅ Smoke test installazione/avvio/arresto: coperto per l'HUD nativo (il job `native-hud-build`
  della CI compila, avvia e verifica che il binario resti in esecuzione), per le dipendenze
  Python (`pip install --require-hashes` verificato davvero installabile) e ora anche per
  `main.py` (`tools/smoke_test.py`, nuovo CI step "Smoke test"): avvia `main.py --cli` come
  processo vero (non importa `JakeCore` in-process: passa dallo stesso avvio di un utente reale,
  carica plugin/skill/config da disco), manda "che ore sono" via stdin, controlla che risponda,
  poi lo chiude con "esci" e verifica l'exit code. Verificato che funzioni anche SENZA Ollama
  raggiungibile (puntando `JAKE_OLLAMA_URL` a una porta inesistente, senza toccare il servizio
  Ollama vero di questa macchina): "che ore sono" e' gia' nella corsia a corrispondenza esatta di
  `training/intents.jsonl`, non serve il modello - necessario perche' i runner CI non hanno
  Ollama. Coperto da `tests/test_smoke_test.py` (la logica di valutazione, non lo spawn del
  processo vero - quello lo fa lo script stesso).
- ✅ Dashboard locale con errori e latenze: `tools/dashboard.py`, un report HTML autonomo (niente
  CDN/dipendenze esterne, coerente con "local-first" - vedi i principi non negoziabili in cima a
  questo documento) generato da `data/jake_actions.jsonl`/`data/jake_sessions.jsonl`: conteggi
  successi/fallimenti, latenza p50/p95 per skill, distribuzione del rischio, stato di verifica
  dell'effetto, ultimi fallimenti. Verificato con dati veri (una sessione reale di `JakeCore` con
  un successo, un fallimento e un'azione privata) prima di essere ripulito. Coperto da
  `tests/test_dashboard.py`, incluso l'escaping HTML di skill/trace_id non fidati.

## F1 — Trustworthy Agent Core 3.0

**Priorità: P0. Obiettivo: ogni azione è tipizzata, autorizzata, verificata e spiegabile.**

- Sostituire JSON “quasi strutturato” con JSON Schema validato per intent, tool call, risultato,
  errore, prova, undo e richiesta di chiarimento.
- Assegnare a ogni azione `action_id`, precondizioni, effetto atteso, timeout, politica retry,
  idempotency key, compensazione e prova post-condizione.
- Aggiungere un action ledger append-only: “chi ha chiesto cosa, quale agente ha deciso, quale
  skill ha agito, con quale autorizzazione e quale risultato”.
- Separare planner, policy engine ed executor: il modello propone; il kernel decide se può
  eseguire; l'executor non può aumentare i propri privilegi.
- Capability token per agente/skill/dispositivo, con scope su cartelle, app, contatti, domini,
  Home Assistant e durata.
- Proteggere segreti con Windows DPAPI/credential vault; migrazione sicura dei token già salvati.
- Usare Windows Hello/passkey per operazioni admin e ad alto impatto. La voce può riconoscere
  l'utente per comodità, ma non deve essere l'unico fattore di sicurezza.
- Difese da prompt injection: separazione tra istruzioni e contenuti letti, taint tracking,
  allowlist delle azioni, conferma quando una pagina/file tenta di impartire ordini.
- Sandbox OS per plugin generati: processo a bassa integrità/AppContainer o ambiente dedicato,
  filesystem e rete negati per default, limiti CPU/RAM/tempo.
- Backup transazionale prima delle modifiche materiali e undo center nell'HUD.
- Kill switch globale da tastiera, tray e voce; arresto immediato di agenti e automazioni.

**Criterio di uscita:** nessuna skill non classificata; nessuna azione esterna/admin senza
ricevuta di policy; test d'attacco su prompt injection e plugin; restore verificato.

## F2 — Voice Natural 3.0

**Priorità: P1. Obiettivo: parlare con Jake deve sembrare una conversazione, non dettare comandi.**

- Pipeline streaming: partial transcript, risposta incrementale e TTS a bassa latenza.
- Cancellazione eco acustica, noise suppression e automatic gain control per hardware reale.
- Barge-in: interrompere Jake mentre parla, preservando il punto della conversazione.
- Wake word personalizzabile, sensibilità per ambiente e modalità push-to-talk sempre disponibile.
- Diarizzazione e profili multiutente; memoria e permessi rigorosamente separati.
- Speaker verification solo come segnale di rischio, mai come autenticazione sufficiente.
- Italiano naturale con code-switching, nomi propri, acronimi, numeri e correzione live.
- Modalità sussurro/notte, risposta breve/lunga, velocità e prosodia contestuali.
- Dizione personalizzabile e vocabolario per contatti, app, progetti e domotica.
- “Cosa hai sentito?” con transcript modificabile e apprendimento esplicito dalla correzione.
- Voice activity privacy indicator sempre visibile; buffer audio volatile e retention zero di
  default.
- Benchmark in stanza silenziosa, TV accesa, musica, distanza, cuffie e più microfoni.

**Criterio di uscita:** feedback visivo < 300 ms; primo transcript parziale < 1 s su hardware
consigliato; interruzione affidabile; falsi risvegli ≤ 1 ogni 24 ore di ascolto nel benchmark.

## F3 — Computer Use Engine 3.0

**Priorità: P1. Obiettivo: controllare Windows per significato, usando i pixel come fallback.**

- Integrare Microsoft UI Automation: albero controlli, ruoli, nomi, stato, pattern Invoke,
  Value, Selection, Toggle, Scroll e Window.
- Selettori stabili per app + control type + nome + ancestor, con cache e invalidazione a eventi.
- Strategia gerarchica: API/app adapter → UI Automation → browser DOM → OCR → visione →
  coordinate, scegliendo la via più affidabile disponibile.
- Unificare click, type, hotkey, scroll, drag/drop, upload/download e gestione dialoghi dentro
  `ComputerAgent`.
- Verifica semantica post-azione: URL cambiato, finestra comparsa, checkbox attiva, file creato,
  testo presente; il pixel diff rimane un segnale, non la prova unica.
- Recovery tree: ri-osserva, cambia selettore, torna indietro, ripristina focus, riapre l'app,
  chiede aiuto solo dopo alternative sicure.
- Browser adapter con DOM/accessibility tree e isolamento del contenuto web non fidato.
- Adapter ad alta affidabilità per Esplora file, Impostazioni, terminale, VS Code, Office,
  browser, player e app di messaggistica.
- Modalità “mostrami prima”: evidenzia i controlli e simula il piano senza cliccare.
- Registratore dimostrazioni: l'utente esegue una routine, Jake generalizza i passaggi e propone
  un workflow parametrico, che viene testato prima di salvarlo.
- Dataset locale di task reali e regression test con app fittizie controllabili.
- Accessibilità come requisito: Jake deve usare la stessa semantica che aiuta screen reader e
  utenti con mobilità ridotta.

**Criterio di uscita:** ≥ 90% di successo su un benchmark di 100 task Windows ripetibili;
nessuna doppia esecuzione nei test di retry; ogni fallimento lascia una diagnosi utilizzabile.

## F4 — HUD Engine 2.0, completamento 4.9.3–4.9.10

**Priorità: P1. Obiettivo: una presenza visiva nativa, fluida e utile.**

- Completare overlay trasparente, click-through selettivo, no-activate e comportamento Alt-Tab.
- Vetro nativo con blur/composition, scaling GPU e fallback senza effetti.
- Orb 2.0 reattivo a wake, ascolto, pensiero, esecuzione, attesa permesso, errore e successo.
- Forma d'onda collegata all'audio reale e animazioni ridotte quando richiesto dal sistema.
- Pannelli contestuali: piano live, prove, sorgenti, file, timer, casa, codice e controlli media.
- Action/undo center, privacy indicator, permessi in attesa e coda notifiche.
- Layout compact/full/focus, snap, persistenza e supporto multi-monitor/DPI/HDR.
- Tastiera completa, screen reader, contrasto elevato, sottotitoli e daltonismo.
- Test Qt, protocol contract test Python↔C++, screenshot golden e budget frame-time.
- Packaging MSIX o installer firmato, avvio automatico, updater atomico e rollback.

**Criterio di uscita:** 60 FPS sul profilo consigliato, nessun blocco dell'input desktop,
accessibilità verificata e 30 giorni di uso quotidiano senza tornare all'HUD legacy.

## F5 — World Model & Memory 3.0

**Priorità: P1. Obiettivo: Jake sa cosa sta succedendo e ricorda soltanto ciò che aiuta.**

- Modello unificato di entità, persone, luoghi, progetti, file, app, dispositivi, eventi,
  preferenze, obiettivi, impegni e relazioni.
- Memoria a quattro livelli: working/session, episodica, semantica e procedurale.
- Ogni ricordo porta provenienza, timestamp, confidenza, sensibilità, owner, TTL e collegamento
  al contenuto originale quando consentito.
- Parsing temporale naturale: “ieri”, “prima della riunione”, “quando lavoravo a X”.
- Consolidamento periodico, dedup, rilevamento conflitti e richiesta di conferma invece di
  sovrascrivere fatti incompatibili.
- Decadimento e oblio utile: ciò che non serve perde priorità; i ricordi importanti restano.
- Privacy dashboard: cerca, correggi, collega, esporta, blocca e cancella qualunque memoria.
- Spazi separati personale/lavoro/famiglia e profili multiutente cifrati.
- Context engine event-driven per finestra, audio, browser, progetto, calendario, rete,
  dispositivo attivo e Home Assistant; niente cattura continua indiscriminata.
- RAG locale con citazioni a file e frammenti, autorizzazioni ereditate dalla sorgente.
- Backup cifrato e portabilità completa dei dati.

**Criterio di uscita:** risposte sulla memoria con provenienza; zero contaminazione tra profili;
test di conflitto/oblio; cancellazione verificabile e completa.

## F6 — Proactive Intelligence & Autonomy

**Priorità: P2. Obiettivo: Jake anticipa bisogni reali senza diventare rumoroso o pericoloso.**

- Event engine per tempo, calendario, email, todo, file, app, rete, casa, batteria, meteo e
  routine; ogni connettore è opt-in.
- Daily brief personalizzato: agenda, scadenze, meteo, viaggio, messaggi importanti, stato casa
  e attività lasciate a metà.
- Commitment tracking: riconosce “lo faccio entro venerdì”, propone un promemoria e segue
  l'impegno senza salvarlo di nascosto.
- Goal manager: scompone obiettivi, mostra avanzamento, suggerisce il prossimo passo e non
  esegue azioni esterne senza delega esplicita.
- Routine apprese da pattern ripetuti, proposte come bozza e attivate solo dopo conferma.
- Focus assistant: prepara workspace, silenzia notifiche, avvia timer, protegge pause e ripristina
  lo stato precedente.
- Meeting copilot locale: preparazione, note e follow-up solo con consenso evidente di tutti i
  partecipanti e indicatore di registrazione.
- Digital housekeeping: duplicati, download, spazio, backup, aggiornamenti e sicurezza, prima
  come suggerimenti e poi come automazioni limitate.
- Monitor di attività lunghe con notifica solo su completamento, anomalia o decisione richiesta.
- Autonomy budget per tempo, numero azioni, costo, rete e impatto; stop automatico al limite.
- Quiet policy appresa, cooldown, digest e pulsante “meno notifiche come questa”.
- Simulazione/dry-run e finestra di annullamento per automazioni ad alto impatto.

**Criterio di uscita:** ≥ 80% dei suggerimenti accettati nel pilot; < 1 interruzione irrilevante
al giorno; nessun superamento dei budget o azione esterna non delegata.

## F7 — Mobile, Home & Ambient Computing

**Priorità: P2. Obiettivo: una sola presenza coerente su dispositivi diversi.**

- Pairing con QR/challenge, chiavi per dispositivo, revoca, scadenza e capability separate.
- Trasporto autenticato e cifrato; LAN per default, accesso remoto opzionale end-to-end.
- Companion mobile con chat/voce, notifiche, quick action, approvazioni, file share e “continua
  sul PC”.
- Handoff reale di conversazione, TTS e task sulla base di presenza, prossimità, cuffie e scelta
  esplicita; mai due dispositivi che parlano insieme.
- Voice satellite per stanze, integrabile con la pipeline vocale di Home Assistant.
- Home Assistant profondo: aree, scene, sensori, automazioni, energia, media, allarmi e Matter
  tramite l'hub, senza reimplementare protocolli radio.
- Regole fisiche più severe: serrature, garage, allarmi, telecamere, forno e climatizzazione
  richiedono policy dedicate e, quando serve, presenza o autenticazione forte.
- Notifiche action-based: approva, annulla, snooze, mostra prove, richiama Jake.
- Stato offline e sincronizzazione cifrata con risoluzione conflitti.
- ✨ Companion per wearable e auto limitato a voce, navigazione, promemoria e azioni sicure.

**Criterio di uscita:** pairing/revoca verificati; handoff senza perdita di contesto; operazioni
fisiche sensibili impossibili da un dispositivo non autorizzato.

## F8 — Self-Improvement & Ecosystem

**Priorità: P2/P3. Obiettivo: Jake cresce senza trasformare il PC in un laboratorio insicuro.**

- SDK skill con manifest, schema input/output, rischio, permessi, dipendenze, test e migrazioni.
- Registry locale e pacchetti firmati; reputazione/provenienza visibile, nessuna installazione
  automatica da testo trovato online.
- Fucina 2.0: specifica → codice → test generati → analisi statica/dinamica → sandbox → diff
  spiegato → approvazione → canary → rollback.
- Versionamento delle skill apprese e confronto prima/dopo su un eval set.
- Agenti specializzati aggiunti solo quando hanno strumenti, permessi e metriche realmente
  distinti: comunicazione, calendario, documenti, casa, diagnostica, creatività.
- Inbox/outbox tipizzate tra agenti; supervisore con budget e deadlock detection.
- Model router per latenza, qualità, privacy, VRAM e batteria; piccolo modello per routing,
  modello forte per ragionamento, visione e coding quando servono.
- Backend modello intercambiabile: Ollama oggi, Windows AI APIs/NPU o altri runtime locali quando
  maturi, cloud soltanto opt-in con redazione dati.
- A/B test locale e feedback “utile/non utile” per migliorare prompt, retrieval e routing.
- Update channel stable/beta/dev, firma, staged rollout e rollback automatico.

**Criterio di uscita:** una skill nuova non può ottenere permessi non dichiarati; una release o
skill regressiva viene fermata dagli eval o ripristinata automaticamente.

## Nuove funzionalità “Jarvis”, non concordate prima

Queste proposte ampliano il sogno; non vanno tutte costruite subito.

### Presenza e personalità

- ✨ Persona coerente ma regolabile: tono, umorismo, sintesi, formalità e livello di iniziativa.
- ✨ Stato relazionale senza antropomorfismo ingannevole: Jake distingue fatti, inferenze e
  ipotesi e sa dire “non lo so”.
- ✨ Saluti e briefing dipendenti dal contesto, non frasi casuali ripetitive.
- ✨ Nomi/pronomi e preferenze per ogni persona, con memorie private separate.
- ✨ Modalità coach, tutor, operatore e copilota, ciascuna con confini espliciti.

### Comprensione multimodale

- ✨ Comprendere grafici, diagrammi, PDF, documenti, video e audio con riferimenti temporali.
- ✨ Seguire un oggetto o una finestra tra cambi di layout senza ricominciare da zero.
- ✨ Camera opzionale per gesti, presenza e oggetti, con LED/indicatore e zone sempre oscurate.
- ✨ Spatial context della stanza tramite satelliti, senza conservare audio o video grezzo.
- ✨ Riconoscimento di suoni utili opt-in: allarme, campanello, timer, vetro rotto, pianto; gli
  eventi sensibili restano locali e configurabili.

### Lavoro e creatività

- ✨ Workspace per progetto: file, finestre, tab, terminali, note, obiettivi e cronologia insieme.
- ✨ Preparazione automatica di una sessione e ripristino esatto del contesto precedente.
- ✨ Ricerca profonda con piano, fonti, citazioni, confronto e archivio locale delle evidenze.
- ✨ Copilota documenti/presentazioni/fogli, con modifiche tracciate e anteprima prima del salvataggio.
- ✨ Inbox triage, bozze di risposta e follow-up; invio sempre sottoposto alla policy dell'utente.
- ✨ Universal command palette: qualunque capacità via voce, testo, hotkey o quick action.

### Vita quotidiana

- ✨ “Sto uscendo”: meteo, traffico, batteria dispositivi, agenda, luci e promemoria contestuali.
- ✨ Pianificazione pasti/spesa e inventario domestico solo su dati inseriti o sensori autorizzati.
- ✨ Travel mode con itinerario offline, documenti, fuso, valuta e avvisi importanti.
- ✨ Family hub con ruoli adulto/minore/ospite e azioni/domotica permesse per ciascuno.
- ✨ Benessere digitale: pause, postura, sonno e carico di lavoro come suggerimenti non medici.
- ✨ Emergency mode configurabile, con contatti e azioni decise in anticipo; niente diagnosi.

### Moonshot, dopo che il nucleo è maturo

- ✨ Digital twin del workspace e della casa per simulare routine prima di eseguirle.
- ✨ Interfaccia AR/spaziale per pannelli contestuali e istruzioni sovrapposte al mondo reale.
- ✨ Robotica tramite adapter sicuro (per esempio ROS 2/Home Assistant), con geofence, stop fisico
  e simulazione obbligatoria.
- ✨ Modello personale locale addestrato su correzioni approvate, esportabile e cancellabile.
- ✨ Federazione privata tra i dispositivi dell'utente senza un archivio cloud centrale.
- ✨ Modalità “mission control”: più agenti lavorano su un obiettivo lungo, mostrano dipendenze,
  prove, rischi e punti in cui serve una decisione umana.

## Scenari di accettazione del sogno

1. **“Prepariamoci alla riunione.”** Jake trova l'evento, apre documenti e call, controlla
   microfono/camera, attiva Meeting, mostra il contesto e prepara note. Non registra né invia
   nulla senza consenso.
2. **“Continua sul telefono.”** Il telefono autenticato prende in carico conversazione e task;
   il PC smette di parlare, ma continua il lavoro autorizzato e invia prove/approvazioni.
3. **“Risolvimi questo errore.”** Jake legge finestra e log, formula ipotesi, prova la modifica
   meno invasiva, verifica e ripristina se peggiora. Un comando admin richiede Windows Hello.
4. **“Quando esco, spegni tutto tranne il server.”** Jake mostra cosa controllerebbe, salva la
   routine con condizioni e limiti, la testa in dry-run e la attiva solo dopo approvazione.
5. **“Dove eravamo rimasti ieri?”** Jake ricostruisce progetto, file, decisioni e prossimo passo
   citando le fonti locali; distingue ciò che ricorda da ciò che deduce.
6. **“Non disturbarmi, ma avvisami se fallisce la build.”** Silenzia il resto, monitora senza
   messaggi ripetitivi e interrompe soltanto su fallimento, completamento o decisione richiesta.
7. **“Insegnami a fare questa procedura.”** Osserva la dimostrazione, crea un workflow
   parametrico, evidenzia i passaggi ambigui e lo prova su dati innocui prima dell'uso reale.

## Metriche che decidono se Jake sta diventando Jarvis

- **Task success:** ≥ 90% su benchmark end-to-end; ≥ 98% sui comandi singoli supportati.
- **Correzioni:** < 5% dei turni richiede “no, intendevo…”.
- **Verifica:** 100% delle azioni esterne, distruttive e admin ha una prova o dichiara
  esplicitamente “non verificato”.
- **Sicurezza:** zero esecuzioni ad alto rischio senza policy; test prompt-injection e sandbox
  verdi a ogni release.
- **Undo:** ≥ 80% delle azioni reversibili ha compensazione automatica testata.
- **Voce:** feedback < 300 ms, risposta semplice percepita < 2 s, falso wake entro il budget.
- **Affidabilità:** ≥ 99,9% sessioni senza crash e nessuna perdita/corruzione dati nei fault test.
- **Proattività:** ≥ 80% suggerimenti giudicati utili; < 1 interruzione irrilevante al giorno.
- **Privacy:** 100% delle memorie ha owner/provenienza/retention; purge e export verificabili.
- **Efficienza:** profili misurati per CPU/GPU/NPU, RAM, VRAM, batteria e tempo modello.

## Decisioni tecniche consigliate

- Usare [Windows UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-clientsoverview)
  come prima scelta per l'interazione semantica; OCR e visione restano fallback.
- Usare gli [structured outputs](https://docs.ollama.com/capabilities/structured-outputs) e il
  [tool calling](https://docs.ollama.com/capabilities/tool-calling) di Ollama dietro un adapter,
  mantenendo validazione e policy fuori dal modello.
- Usare [Windows Hello/WebAuthn](https://learn.microsoft.com/en-us/windows/security/identity-protection/hello-for-business/webauthn-apis)
  per autenticazione forte e [DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
  per i segreti legati all'utente/macchina.
- Continuare a usare Home Assistant come hub e valutare la sua
  [pipeline voce/satelliti](https://developers.home-assistant.io/docs/voice/overview/) per le stanze.
- Mantenere `ModelProvider` astratto. Le Windows AI APIs sono interessanti per NPU/OCR, ma i
  modelli e le API di sistema sono ancora in transizione: nessun lock-in a un nome specifico.
- Non costruire direttamente stack Zigbee/Z-Wave/Matter, crittografia proprietaria, browser
  completo o biometria custom quando esistono piattaforme mature e verificabili.

## Cose che Jake non deve mai diventare

- Un microfono o una camera che registra sempre senza indicatore e consenso.
- Un agente che invia messaggi, compra, pubblica, apre serrature o cancella dati “per aiutare”.
- Un sistema in cui la frase letta su una pagina web può diventare un'istruzione privilegiata.
- Un riconoscitore vocale usato come unico lucchetto per azioni sensibili.
- Un generatore di plugin con accesso completo al PC.
- Un consulente medico, legale o finanziario che presenta inferenze come decisioni certe.
- Un prodotto cloud obbligatorio che smette di funzionare senza account o connessione.
- Una personalità che finge emozioni, coscienza o certezza per convincere l'utente.

## Prossimo incremento consigliato

Prima di aggiungere nuove skill isolate:

1. chiudere F0 e rendere sempre verde la base;
2. introdurre action contract + ledger + prove in F1;
3. aggiungere DPAPI e Windows Hello prima di aprire il companion alla LAN;
4. portare `ComputerAgent` su UI Automation con 10 task benchmark;
5. rendere l'HUD nativo testabile e completare 4.9.3;
6. costruire streaming voice + barge-in su una matrice hardware minima;
7. solo dopo, collegare memoria temporale e proattività.

Questo ordine produce meno funzioni appariscenti nel brevissimo periodo, ma trasforma i mattoni
già costruiti in una base sulla quale ogni nuova capacità può essere davvero affidabile.
