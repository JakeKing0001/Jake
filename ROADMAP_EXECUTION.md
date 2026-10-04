## 24. Prossima azione esatta

Aggiornato 02/10/2026, baseline pre-sperimentazione (le versioni precedenti restano nella cronologia Git). Da qui
lo sviluppo e' guidato dai problemi osservati nell'uso reale: nessuna feature nuova finche' la prova non li indica.

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
