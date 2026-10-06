## 24. Prossima azione esatta

Aggiornato 05/10/2026. La prima prova reale (04-05/10) e' avvenuta e i suoi problemi sono chiusi negli incrementi
qui sotto (#194-#203 e l'instradamento a GPU ceduta). Si torna alla sperimentazione: niente feature nuove finche' la
prova non indica il prossimo problema.

Prossimo test reale dell'utente: `python main.py` con un training CUDA o un gioco acceso (GPU quasi piena), poi
"che ore sono", "apri Spotify", "quanta batteria ho", "abbassa il volume", "spiegami cos'e' un buco nero", "chiudi
chrome"; dopo un errore `python main.py --diagnostics` e il bundle con l'ora del turno.

Baseline del 02/10/2026 (resta come riferimento storico):

Gia' su master e verificato nel codice (non piu' da elencare come mancante):

- F5.1.2 entita'/episodi/procedure con schema v4 (#188); goal con passi e prossimo passo nella todo list
  (F6.4.3/F6.4.4, PR #190 chiusa come superata: il suo contenuto era gia' su master).
- Skill Forge: pacchetti firmati nel catalogo (#170), prova nel runtime e periodo di prova con rollback (#174),
  specifica con casi di prova prima del codice (#175), test dei pacchetti di terzi in sandbox (#185).
- Continuita' companion: conferma che segue chi risponde (#169), notifiche solo al dispositivo attivo (#173), il PC
  riprende la sessione (#181), provenienza dei messaggi nell'HUD (#184).
- F0.6: installer portable deciso (`setup.ps1`, ripara/disinstalla conservando i dati), updater atomico con
  controllo di salute e rollback, canali stable/dev, firma Ed25519 della stable se `release_public_key` e' impostata.
- RAM (#191): solo `ollama_model` resta caldo 30 min, visione/coding/embedding 2 min, `low_memory`, server RVC chiuso
  dopo inattivita'; ricordi sensibili cifrati letti correttamente.
- 3.2 (#192): `SkillRegistry` diviso in catalogo, esecutore e gestore dei plugin; `jake_core.py` diviso in moduli.
- Baseline diagnostica (questa PR): un `trace_id` per turno condiviso da turno, azioni, agente e ledger; riga
  `kind: "turn"` in `data/jake_actions.jsonl` (percorso exact/llm/rules/retrieval/agent, intent, modelli, tempi di
  STT/routing/LLM/totale, errore classificato, id opachi dei ricordi e se la risposta li ha usati, RAM del processo);
  timeline degli stati (`kind: "state"`) e durata della voce (`kind: "speech"`); `python main.py --diagnostics`
  produce un bundle condivisibile (commit, config redatta, modello e modelli caricati, preflight, RAM, errori,
  tempi mediani/p95). Niente audio, testo dell'utente, valori di ricordi, credenziali o utente Windows; in modalita'
  privata solo un marcatore anonimo.

Baseline misurata il 02/10/2026 su questa macchina: preflight tutto OK; RAM 16,9 GB (3,8 GB liberi con le app
aperte); VRAM 6,9 GB liberi su 8,2 GB, stima qwen2.5:7b + Whisper + voce/HUD ~6,9 GB; nessun modello rimasto
caricato in Ollama a riposo (keep_alive 30m/2m rispettato); import del nucleo ~43 MB.

1. Utente, prova reale: usare Jake normalmente e, dopo un errore, lanciare `python main.py --diagnostics` e
   condividere `data/jake_diagnostic_bundle.json` (dopo averlo letto) con l'ora o il trace_id del turno sbagliato.
2. Utente, gate hardware di F2 con `docs/f2-hardware-validation.md` (tre profili, interruzioni, sessione wake di
   24 h, poi `python -m benchmarks.f2_hardware_session evaluate`). Solo con `PASS` F2 passa a `DONE`: resta `VERIFY`.
3. Utente, verifica a schermo (F4 resta `VERIFY`): vetro vero dietro i pannelli su desktop scuri/chiari/colorati,
   orb per stato, HUD su un secondo monitor a scala diversa, interruttore "Privato"; con il telefono: conferma che
   passa al telefono e torna al PC, notifiche solo sul dispositivo attivo; "impara a ..." fino al periodo di prova.
4. Utente, una tantum se vuole aggiornamenti firmati: `python -m tools.updater --keygen`, chiave pubblica in
   `release_public_key`, poi `--sign vX` per ogni stable.
5. Aperto, da riprendere solo se la prova lo chiede: F7 coda offline, trasporto della sync e remote wipe end-to-end
   (oggi solo libreria in `core/sync_engine.py`); F8 runtime degli agenti oltre i budget gia' presenti; budget GPU e
   frame-time dell'HUD misurati sul desktop reale; `__init__` di JakeCore (~600 righe di cablaggio).

## Incremento del 04/10/2026 — presenza ambientale dell'HUD (da prova reale dell'utente)

- Presenza separata dal layout: `expanded` (HUD normale nel layout full/compact/focus del monitor), `mini` (solo
  l'orb, finestra 168 px logici nell'angolo alto destro dell'area utile del monitor dell'HUD), `hidden` (finestra
  invisibile, Jake/voce/wake word/processo dell'HUD attivi). Logica in `hud/native/qml/Presence.qml`, stato di
  sessione mai salvato; il layout per monitor non viene mai toccato.
- Protocollo: stessi `HUD_SHOW`/`HUD_HIDE`, `HUD_SHOW` con payload opzionale `{"presentation": "mini"|"expanded",
  "reason"}`; reducer Python e C++ allineati e verificati dal fixture condiviso.
- Comandi esatti `SET_HUD_PRESENTATION` ("rimpicciolisciti", "nasconditi", "riduciti a icona", "ingrandisciti",
  "mostrati"...). La wake word riconosciuta davvero (`WakeWordSession`, dopo il cooldown) riporta l'HUD grande; non
  se la frase stessa e' un comando di presenza (niente lampo grande->mini).
- Mini automatico dopo 30 s di vera inattivita' (IDLE, niente conferme, scrittura, cursore su pannelli, errori o
  avvisi); notifiche normali in mini = punto discreto sull'orb, da nascosto nulla; una conferma riporta grande solo
  da mini. Transizione 260 ms, immediata con movimento ridotto; clic sull'orb mini = grande.
- Verificato: build Release + 6/6 ctest (nuovo `hud_presence`), nessun warning qmllint nuovo; `JakeHud.exe` vero
  collegato a un server companion vero: dimensioni e angolo reali, click-through fuori dall'orb, ritorno allo stesso
  layout, nascosto con processo vivo, mini automatico a 30 s.
- `VERIFY` utente: prova A-G con voce e microfono veri, aspetto della transizione, secondo monitor e DPI diversi.
- Bug reale dalla prova del 04/10: comparivano due Jake (orb 2D dell'HUD PySide di `main.py` e orb 3D del nativo),
  entrambi anche su Ctrl+Shift+J. Ora con l'HUD nativo attivo (`NativeHudSupervisor.active`: acceso o in
  riavvio) quello PySide non si mostra e lascia hotkey e tray al nativo; torna di riserva se il nativo manca, e'
  chiuso o abbandonato dopo i crash.

## Incremento del 04/10/2026 — budget di VRAM per qwen2.5:7b

- `core/ollama_gpu_budget.py` + `runtime_options()` in `core/ollama_client.py` (unico punto, usato dal client e dai
  nove chiamanti diretti): `num_gpu` calibrato misurando `size_vram` di `/api/ps`, cache per modello/digest/GPU/
  budget/modo/context, CPU-only se la calibrazione non e' pronta o fallisce. `low_memory` implica 1024 MB.
- Misura reale (RTX 4060 Laptop, Ollama 0.35.1, budget 1024 hard): 3/29 layer, 987 MB di VRAM (era 4987 MB),
  `ollama ps` 82%/18% CPU/GPU; generazione da ~51 a ~7 token/s, risposta da 120 token da ~2,4 s a ~17-18 s.
  Sotto ~1 GB la sola CPU e' veloce uguale (7,4 token/s): il budget libera VRAM, non accelera nulla.
- Context 8192 vs 4096: -240 MB a GPU piena, -33 MB con 3 layer, nessuna differenza di velocita'; resta 8192
  (il classificatore lo usa) e `ollama_context` e' solo configurabile. Impatto su Whisper sotto carico: non misurato.

## Incremento del 05/10/2026 — la GPU torna a giochi e app solo quando servono

- Il budget fisso di 1024 MB rallentava ogni risposta (~17 s) anche a GPU libera: tolto dalla configurazione locale.
  Al suo posto `core/gpu_yield.py`: con un gioco/app a schermo intero (SHQueryUserNotificationState, esclusi Jake,
  browser e lettori video), un eseguibile in `gpu_yield_apps` o la modalita' "gioco", dopo 4 s il modello principale
  lascia la GPU (scaricato, poi CPU con keep_alive 2 min) e RVC si ferma (voce di base); 20 s dopo la fine torna
  sulla GPU e viene ricaricato in anticipo. `gpu_yield_enabled: false` lo spegne; il budget fisso resta disponibile.
- Bug trovato nella prova vera: senza `num_gpu` Ollama riusava il runner CPU aperto durante il gioco; ora la ripresa
  scarica e ricarica (verificato: 0 -> 4546 MB di VRAM).
- Cache KV q8_0 (`OLLAMA_KV_CACHE_TYPE`, impostata anche da `setup.ps1`): 448 -> 238 MB a 8192 token, qwen2.5:7b da
  4756 a 4546 MB, ~51 token/s invariati. Flash attention era gia' attiva (auto) in Ollama 0.35.1.
- Visione: qwen2.5vl:3b sullo stesso screenshot reale 2697 MB / 7,3 s contro 5128 MB / 11,4 s del 7b, descrizione
  corretta ma meno precisa (non legge il nome del file). Scelta locale, il default resta 7b.
## Incremento del 05/10/2026 — HUD a riposo meno costoso per la GPU

- Misura reale (`JakeHud.exe`, contatori GPU Engine di Windows, schermo a 144 Hz): HUD grande in IDLE ~9-12% del
  motore 3D, presenza mini 4,8%, 137 MB di VRAM, 153 MB di RAM.
- L'orb 3D a riposo (IDLE/PAUSED, anche in mini) avanza a 30 fps con un Timer invece che a vsync; particelle a tempo
  manuale (mai riavviate, nessun salto). La rilevazione dei fotogrammi lenti vale solo a vsync. L'impulso dell'aura
  girava anche con l'ambiente nascosto (mini) e teneva la finestra a vsync: ora solo se visibile.
- Dopo: grande 7,1%, mini 2,7%; orb mini verificato a schermo (particelle presenti e in movimento). La polvere a meta'
  risoluzione e' stata provata e scartata (6,7%, nel rumore). Build + 6/6 ctest.
## Incremento del 05/10/2026 — prompt del classificatore con prefisso stabile

- Misura reale: ogni classificazione mandava a qwen2.5:7b 1900-3000 token e il prefill era 750-1500 ms, ~65% della
  latenza; le parti variabili (contesto del desktop, capacita' ed esempi scelti per la frase) venivano prima delle
  regole fisse, quindi Ollama non poteva riusare la cache del prefisso.
- Ora: intestazione, istruzione sulla cronologia e regole prima; capacita', esempi e contesto dopo. Prefill -15%
  (750 -> 641 ms, 954 -> 814 ms). `bench_nlu` 60 frasi seed 7: p50 1476 -> 1340 ms, p95 1971 -> 1606 ms; accuratezza
  78,3% -> 76,7%, unica differenza "leggi gli appunti" (CLIPBOARD_READ -> LIST_NOTES, ambigua in italiano e in
  produzione presa dalla corsia esatta).
- Modello principale piu' piccolo valutato e NON adottato: qwen2.5:3b stessa accuratezza sugli intent (78,3%), p50
  830 ms, 2157 MB di VRAM e 95 token/s, ma risposte in italiano visibilmente peggiori su domande tipiche.
## Incremento del 05/10/2026 — voce del personaggio con Piper, senza torch/RVC

- `core/voice/piper_tts_provider.py`: se esiste `data/piper/models/<personaggio>.onnx` Jake parla con Piper (ONNX su
  CPU) e non avvia il server RVC; stesso consenso di RVC (revocato = voce di base). `voice_character_engine: "rvc"`
  torna alla conversione. Misura con una voce italiana pubblica: prima frase 56 ms dopo il prewarm (2,8 s senza),
  6,8 s di audio in 0,25 s, +158 MB di RAM, 0 di VRAM (RVC: ~2 GB di RAM e ~1 GB di VRAM).
- Dati: `tools/piper_voice_dataset.py` (frasi dal modello locale -> edge-tts DiegoNeural -> RVC del personaggio,
  ripresa automatica, frasi saltate se edge-tts non risponde). Addestramento: `training/piper/` (Docker + GPU, il
  training di Piper e' solo Linux), fine-tuning dal checkpoint italiano serena-medium, export ONNX. Patch all'immagine:
  niente checkpoint su `val_mos` (Lightning falliva a fine epoca) ed export con `dynamo=False` (torch 2.14).
- Verificato l'intero percorso su un'epoca di prova: checkpoint ripreso, export ONNX da 63 MB, sintesi dal provider di
  Jake. `VERIFY` utente: ascolto della voce addestrata e confronto con RVC.

## Incremento del 05/10/2026 — apprendimento automatico: niente richieste composte (#200)

- "che ore sono e che giorno e' oggi", classificata solo GET_TIME, era diventata un esempio exact: meta' richiesta
  spariva per sempre. `LearningManager.observe` non impara piu' frasi con richieste coordinate (`looks_compound`).

## Incremento del 05/10/2026 — cultura generale sempre ASK_QUESTION (#201)

- gemma3:4b dava UNKNOWN a 5 domande di cultura generale su 8 (all'agente, nessuna risposta). Regola esplicita nel
  prompt: spiegazioni/consigli non sul PC -> ASK_QUESTION, UNKNOWN solo per azioni sul PC sconosciute. 8/8 su gemma3:4b
  e qwen2.5:7b, `bench_nlu` invariato (78,3%).

## Incremento del 05/10/2026 — cessione della GPU anche per VRAM insufficiente (#203)

- Con un training da 7,7 GB ogni classificazione andava in timeout (Ollama non caricava il modello). `VramPressure`:
  VRAM libera + quella del modello contro il suo fabbisogno; se non basta la GPU viene ceduta come per un gioco.

## Incremento del 05/10/2026 — instradamento a GPU ceduta senza il timeout da 25 s

Diagnosi (gemma3:4b, Ollama 0.35.1, stesse 9 frasi; nessun JakeCore nelle misure, niente scritto su memoria/esempi):

| Scenario | caricamento | prefill | generazione | totale per frase |
|---|---|---|---|---|
| GPU libera | ~0,03 s | 0,5-0,8 s (~2000-2300 token) | 0,2-0,4 s | ~1,0 s |
| CPU a freddo | 6,5 s una volta | 19-26 s | 1,1-2,3 s | 21-28 s (oltre il timeout da 25 s) |
| CPU gia' caldo | ~0 | 22-26 s | 1,2-2,4 s | 23-28 s |

Il prefill su CPU (~90 token/s) e' il 90% del tempo; tenere il modello caldo non aiuta e con gemma3 il prefisso in cache
non viene riusato. Prompt reale ~7000 caratteri: capacita' ~3800, regole ~1300, esempi ~1000, piu' contesto e cronologia.

- `core/nlu/degraded_routing.py` + `Router.degraded/degraded_model`: a GPU ceduta, dopo l'exact, regole locali solo per
  frasi brevi, non composte, non correzioni, intent di sola lettura o reversibili, ora/data solo con formule esplicite
  ("a che ora parte il treno" non diventa GET_TIME), OPEN_APP solo per app che il resolver trova; poi l'esempio piu'
  simile (soglia 0,80, stessi vincoli); poi domande di cultura generale -> ASK_QUESTION (la regola di #201 senza
  modello). Il resto va al classificatore compatto: 6 capacita' su una riga, 4 esempi, regole condensate, 2 turni di
  cronologia, contesto del desktop solo se la frase lo richiama (495-611 token invece di ~2000-2300).
- Il modello del classificatore a GPU ceduta lo sceglie il ModelRouter (`choose_model(..., prefer_fast=True)`): uno
  dichiarato veloce in `ollama_light_model` se installato, altrimenti il principale in forma compatta. A GPU ceduta anche i
  modelli non principali girano su CPU (`num_gpu` 0): qwen2.5:1.5b si era caricato tutto sulla GPU durante la prova.
- Regole: "quanta batteria", varianti di "abbassa/alza il volume" (servono anche al ripiego normale).
- Misure CPU (classificatore): gemma3:4b compatto 5,6-7,0 s (prefill 4,1-5,4 s); qwen2.5:1.5b compatto 2,0-2,4 s a caldo,
  3,9 s a freddo, 1,2 GB di RAM solo mentre e' caricato (keep_alive 2 min). Corsie veloci 0-47 ms.
- Accuratezza `bench_nlu` 60 frasi seed 7: normale 78,3%; degradato 80,0% sia con gemma3:4b sia con qwen2.5:1.5b
  (p50 44 ms). Scelto qwen2.5:1.5b come `ollama_light_model` locale: stessa accuratezza, CPU ~3 volte piu' veloce.
- Scenario reale con un altro modello da 5,2 GB sulla GPU (321 MB liberi): GPU ceduta, ora/data/Spotify/batteria/volume/
  "buco nero" in 0-37 ms, il resto 1,8-2,5 s (5 s la prima volta), VRAM libera invariata (319 MB). Prima: 25 s di timeout.
- GPU libera: percorso normale invariato (~1 s, prompt completo). Build HUD Release + 6/6 ctest (presenza expanded/
  mini/hidden).
- Voce: la voce Piper veniva considerata solo se anche il modello RVC era installato (controllo `is_installed` prima);
  ora consenso -> Piper -> RVC come ripiego, senza nemmeno creare il gestore RVC quando Piper c'e'.
- `VERIFY` utente: qualita' della voce Piper (#202) rispetto a RVC; la prova con un training/gioco vero.

## Incremento del 06/10/2026 — runtime piu' leggero (misurato, niente funzioni nuove)

- numpy/OpenBLAS preparava buffer per 20 thread all'import: processo di Jake 652 MB di memoria impegnata contro 57 MB
  di working set. `main.py` fissa `OPENBLAS_NUM_THREADS=2` (se non gia' impostato) prima di ogni import: 73 MB impegnati,
  stesso working set (numpy serve solo a piccole similarita' fra embedding).
- Embedding (nomic-embed-text) sempre sulla CPU: una frase 46 ms contro 43 ms sulla GPU; il processo di Ollama passa da
  396 MB di RAM, 934 MB impegnati e 409 MB di VRAM a 287 MB, 396 MB e 0. Gli embedding dell'indice sono in cache su disco.
  `core/embedding_provider.py` ora manda le stesse opzioni e lo stesso keep_alive di `OllamaClient.embed()` (prima
  niente keep_alive e niente opzioni: il modello si sarebbe ricaricato fra CPU e GPU). `ollama_embedding_gpu: true`
  lo riporta sulla GPU.
- VRAM libera letta da NVML (`core/nvml.py`) invece di lanciare `nvidia-smi` ogni 10 s: ~60 ms e un processo a lettura
  contro ~0 dopo un'inizializzazione da ~20 ms; stessi valori; `nvidia-smi` resta come ripiego.
- Bug: con `stt_device: "cuda"` esplicito le DLL di cuBLAS/cuDNN non venivano registrate e Whisper ripiegava in
  silenzio sul modello medium su CPU.
- Misurato e scartato: Whisper large-v3-turbo su CPU (5 s a frase invece di 0,25 s, e piu' RAM: ~1 GB e 3,3 GB
  impegnati contro 651 MB e 2,2 GB sulla GPU, che resta); HUD PySide di riserva creato pigramente (costa ~77 MB e 0 CPU
  a riposo, serve comunque QApplication per tray e hotkey: non vale il rischio).

## Registro owner e stato dei pacchetti attivi

Questo registro riguarda l'incremento in corso; il catalogo storico completo resta nella cronologia Git.
`DOING` indica lavoro e verifiche ancora aperti, non il completamento della fase.

| Pacchetto | Owner logico | Stato |
|---|---|---|
| `F4.3` | Utente (verifica visiva e GPU sul desktop reale) | `VERIFY` |
| `F4.7` | Utente (monitor fisici, scala testo reale) | `VERIFY` |

## Incremento del 29/09/2026 — materiale HUD e regressioni

### F4.3 — Vetro, fallback e risorse

- Proseguite le modifiche locali al vetro: gradiente multistop, grana, riflesso speculare e ombra interna
  conservati, senza tipi QML non risolti (`OpacityMask`/`RadialGradient`) o texture inesistente.
- Superficie decorativa statica e ritagliata agli angoli; texture e cattura del backdrop si spengono quando
  il pannello e' nascosto o la qualita' scende. La tinta leggibile resta sempre presente.
- Test nativo `hud_glass_material`: carica e disegna gli stessi sorgenti QML dell'app, fallisce sui warning,
  verifica 48 combinazioni di fondo/stato/qualita'/modalita' vetro, transizioni, resize e rilascio delle risorse.
  CI configurata per pubblicare screenshot diagnostici e log CTest.
- Ancora da verificare: compositing DWM sul desktop reale e budget GPU/frame-time. Il rendering software
  offscreen non sostituisce queste prove e non chiude F4.

### F4.7 — Contrasto e scala del materiale

- Tinta e testo secondario regolati sul rendering effettivo: contrasto almeno 4,5 nell'area interna dei
  pannelli sui fondi di prova bianco, scuro e colorato. In contrasto elevato: nero pieno, bordo bianco
  anche con accento scuro, niente decorazioni.
- La stessa suite gira anche al 150% (`hud_glass_material_hidpi`); il controllo degli angoli impedisce
  regressioni a pannelli quadrati. Nessuna dichiarazione di supporto multi-monitor basata su questa simulazione.
- Ancora da verificare: spostamento fra monitor a DPI diversi, scala testo reale e accessibilita' dell'intero HUD.

Verifiche locali dell'incremento: build MSVC/Qt 6.7.3 riuscita, 5/5 CTest, 213 test Python HUD e test
della struttura roadmap superati, `qmllint` e lint Python dei file toccati puliti. Non eseguita la CI remota
ne' la suite Python completa del repository.

Prossima azione di sviluppo (aggiornata il 02/10/2026): nessuna fino ai risultati della prova reale; poi fix
mirati sui turni segnalati, ricostruiti dal bundle diagnostico.
