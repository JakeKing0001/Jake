# Jake

> Il progetto include oggi avanzamenti delle fasi 3.x–5.x. Il percorso collegato e ordinato
> verso un vero Jarvis è nella **[roadmap esecutiva](ROADMAP_EXECUTION.md)**; prove, incidenti e
> cronologia tecnica restano nell'[audit storico](ROADMAP.md).

Assistente personale vocale, locale, per Windows. Ascolta, capisce l'italiano parlato, **fa le cose al posto tuo** (apre programmi e siti, cerca, scrive, clicca, gestisce file, finestre, timer, promemoria, musica...), risponde a domande, **impara comandi nuovi** e, quando non sa fare qualcosa, **si scrive da solo una nuova capacità**. Per i compiti composti ("trova il file X e leggimelo") **ragiona a passi**: esegue un'azione, guarda il risultato vero, decide la successiva, e chiede a te solo se manca davvero un'informazione che solo tu conosci. Quando lo attivi compare un HUD a schermo intero in vetro liquido blu, stile Jarvis, sopra qualunque cosa: i suoi pannelli sono cliccabili, il resto dello schermo resta trasparente e utilizzabile.

Tutto gira sul tuo PC: modelli Ollama per capire e ragionare, Whisper per la voce (su GPU se c'è), voce neurale Microsoft (online, con ripiego offline). Nessun dato lascia il computer, tranne la sintesi vocale se usi la voce online.

## Avvio rapido

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1          # una volta: venv, dipendenze, modelli Ollama, GPU
.venv\Scripts\python.exe main.py                            # HUD + voce continua: di' «Jake»
```

Altre modalità:

| Comando | Cosa fa |
|---|---|
| `main.py` | Modalità Jarvis: HUD in vetro, icona nella tray, ascolto continuo (wake word «Jake»), hotkey `Ctrl+Shift+J` per scrivere un comando |
| `main.py --no-voice` | Solo HUD e barra comandi, senza microfono |
| `main.py --cli` | Testo nel terminale |
| `main.py --voice` | Push-to-talk nel terminale (F9) |
| `main.py --voice --wake-word` | Voce continua nel terminale, senza HUD |
| `main.py --version` | Versione corrente di Jake e del protocollo companion/HUD, lette da `config/release.json` |
| `setup.ps1 -Autostart` | Jake parte a ogni accensione del PC |

Requisiti: Windows 10/11, Python 3.11+, [Ollama](https://ollama.com) con `qwen2.5:7b`, `nomic-embed-text` (e opzionali `qwen2.5-coder:7b` per la fucina, `qwen2.5vl:7b` per la visione). Con una GPU NVIDIA `setup.ps1` installa le librerie CUDA e Jake usa Whisper `large-v3-turbo` (riconoscimento in meno di mezzo secondo).

### Hardware

| | Minimo (solo CPU) | Consigliato (con GPU) |
|---|---|---|
| Modelli Ollama | `qwen2.5:7b` + `nomic-embed-text` | anche `qwen2.5-coder:7b` (fucina) e `qwen2.5vl:7b` (visione) |
| Whisper | `medium` int8 su CPU | `large-v3-turbo` float16 su GPU NVIDIA (CUDA) |
| Spazio disco (solo modelli) | ~6,5 GB (4,7 + 0,27 + 1,5 GB) | ~18,8 GB con tutti i modelli opzionali sopra |
| GPU | non richiesta | NVIDIA con almeno 8 GB di VRAM (testato su una RTX 4060 Laptop, 8 GB) |
| RAM | 8 GB (soglia generica di Ollama per modelli 7B, non specifica di Jake) | 16 GB o più, per tenere insieme Ollama, Whisper, l'HUD e il resto di Windows senza swap |
| VRAM occupata da Ollama a runtime | - | ~5 GB con `qwen2.5:7b` (classificatore/agente) e `nomic-embed-text` (recupero semantico) entrambi caricati insieme su GPU (misurato con `ollama ps`); `qwen2.5-coder:7b`/`qwen2.5vl:7b` si aggiungono solo quando usati, Ollama scarica i modelli inattivi da soli dopo un periodo di inattività |

Le dimensioni dei modelli sopra sono misurate direttamente (`ollama list`/`ollama ps`, cache di `faster-whisper`), non stimate. Non ancora misurato: l'impronta RAM del processo Python di Jake stesso (HUD, agente, memoria) insieme a Ollama, ne' un profilo di latenza end-to-end dell'intera pipeline voce+NLU+agente in un'unica sessione - vedi `benchmarks/` per le latenze misurate dei singoli componenti (NLU, agente, percezione schermo), e la fase F0 nella [roadmap esecutiva](ROADMAP_EXECUTION.md) per cosa manca ancora.

## Cosa puoi dirgli

- **Programmi e siti**: «apri blocco note», «apri spotify», «apri youtube», «chiudi opera», «passa a visual studio code», «minimizza tutto»
- **Ricerche**: «cerca su youtube gatti», «cerca su amazon cuffie bluetooth», «googla come installare python», «cerca su maps la pizzeria più vicina»
- **Musica e video**: «metti i coldplay», «fammi sentire un po' di jazz», «metti su youtube lofi hip hop», «pausa», «prossima canzone», «volume al 30»
- **Timer e promemoria**: «metti un timer di 10 minuti per la pasta», «annulla il timer», «ricordami alle 18:30 di prendere le medicine», «tra 20 minuti ricordami di uscire», «ogni giorno alle 8 ricordami le vitamine»
- **Controllo del PC**: «clicca su accedi», «clicca sull'icona delle impostazioni», «scorri giù», «premi control s», «scrivi ciao a tutti», «scrivi sotto dettatura» ... «fine dettatura», «leggimi il testo selezionato», «chiudi questa finestra», «luminosità al 40», «spegni il computer»
- **File e cartelle**: «apri la cartella download», «crea la cartella progetti sul desktop», «trova il file tesi.pdf», «quanto pesa la cartella documenti», «comprimi desktop\progetti»
- **Schermo**: «cosa c'è sullo schermo», «leggi lo schermo», «fai uno screenshot», «guarda lo schermo e dimmi che errore c'è»
- **Memoria, appunti, cose da fare**: «ricorda che la password del wifi è ...», «appuntati che devo comprare il pane», «aggiungi alla lista delle cose da fare chiamare l'idraulico», «cosa devo fare»
- **Rubrica e messaggi**: «salva il numero di marco 333...», «manda un whatsapp a marco: arrivo tra 5 minuti», «manda una mail a luca@... con oggetto riunione»
- **Domande e testo**: «qual è la capitale della francia», «spiegami la fotosintesi», «traduci buongiorno in inglese», «quanto fa 12 per 8», «converti 5 kg in libbre», «riassumi quello che ho copiato»
- **Su Jake stesso**: «cosa sai fare», «ripeti», «zitto», «non ascoltare per 10 minuti» ... «Jake, svegliati», «no, intendevo apri discord», «ferma tutto» (kill switch: ferma subito agenti e automazioni, senza chiedere conferma) ... «riprendi»

Dopo una risposta puoi continuare a parlare per qualche secondo senza ripetere «Jake» (finestra di follow-up). Le domande di conferma («confermi?») accettano il sì/no direttamente.

Prima di eseguire un'azione confermata, Jake ricontrolla la policy corrente: un blocco
intervenuto dopo la domanda annulla l'esecuzione. Il sì prova il consenso, non l'identità;
se l'azione richiede autenticazione, serve la passphrase valida o Windows Hello.

## Come ragiona sui compiti composti

Una richiesta come «trova il file tesi.pdf e dimmi cosa contiene» non è una singola azione: Jake la affida a un agente che lavora a passi (`core/agent.py`), non a un piano scritto in anticipo. Cerca il file, guarda il risultato VERO (il percorso trovato), lo passa al passo successivo (leggerlo), e solo allora risponde. Se un passo manca di un dato che solo tu conosci (quale contatto, quale cartella), si ferma e te lo chiede invece di indovinare; se rispondi, riprende da dove si era fermato.

Anche i comandi singoli hanno dei ripieghi (`core/fallbacks.py`): «apri instagram» detto come sito ma installato come app lo apre comunque; un'app non trovata ma che sembra un sito noto (YouTube, GitHub...) si apre lo stesso; un nome inventato che non corrisponde a nulla ti viene proposto di cercarlo nel browser. E dopo aver nominato qualcosa («trova il file X», «apri Spotify»), puoi riferirti con un pronome: «aprilo», «chiudilo», «leggilo», «cancellalo».

## Come impara

1. **Comandi tuoi**: «quando dico modalità gaming apri steam e discord», «d'ora in poi quando dico buonanotte spegni il computer». Da quel momento la frase esegue il comando (anche composto: diventa un'automazione). «cosa hai imparato», «dimentica il comando modalità gaming».
2. **Correzioni**: «no, intendevo apri visual studio code» esegue la richiesta giusta e associa la frase precedente al comando corretto; se differiva solo per una parola storpiata dal riconoscimento vocale, quella parola entra nel vocabolario (es. «judy westcode» → «visual studio code»).
3. **Osservazione**: ogni comando capito dal modello ed eseguito con successo, che non correggi al turno dopo, diventa un esempio. La volta successiva la stessa frase prende la corsia veloce (nessuna chiamata al modello) e le frasi simili si classificano meglio.
4. **Nuove capacità (fucina)**: «impara a fare una cosa nuova: dirmi quanti file ci sono in una cartella», o semplicemente chiedi qualcosa che non sa fare e rispondi «sì» quando propone di impararlo. Jake scrive un plugin Python con il modello coder, lo controlla (sintassi, lista nera di operazioni pericolose, import ammessi, prova in sandbox), te lo descrive e lo attiva solo se confermi. Le skill create stanno in `plugins/learned_*.py`, leggibili e cancellabili («elimina la skill che hai creato per ...»).

Dati di apprendimento: `data/learned_examples.jsonl`, `data/learned_vocabulary.json` (locali, non versionati). Dataset di partenza: `training/intents.jsonl` (667 frasi, 198 intent).

## Come funziona (architettura)

```
voce ──VAD──▶ Whisper (GPU) ──▶ TranscriptNormalizer ──▶ Router ──▶ Skill ──▶ risposta ──▶ Edge TTS / OneCore
                                 numeri, orari, nomi app      │                                      │
                                 errori noti di Whisper,      ├─ corsia veloce: frase già nota          └─ HUD (Qt, vetro, sempre sopra)
                                 pronomi (aprilo, chiudilo)    ├─ classificatore Ollama con recupero semantico
                                                              │    (solo le ~18 capacità pertinenti + esempi few-shot)
                                                              ├─ regole locali (se Ollama non risponde)
                                                              └─ agente a passi (composti/UNKNOWN) ──ripieghi──▶ Skill
```

- `core/nlu/` — normalizzazione, esempi, indice semantico (embedding con cache su disco, fallback lessicale), retriever, chitchat, classificatore LLM.
- `core/agent.py` — agente a passi per i compiti composti: sceglie uno strumento alla volta, osserva il risultato reale, decide il successivo o chiede chiarimenti (mai un piano fisso scritto in anticipo).
- `core/fallbacks.py` — ripieghi quando una skill fallisce in modo prevedibile (riscrittura prima dell'esecuzione, alternativa automatica, proposta da confermare).
- `core/command_safety.py` — blocca `esegui il comando ...` quando corrisponde a un pattern distruttivo noto (cancellazione ricorsiva, formattazione, cancellazione di copie shadow, download-and-execute, PowerShell offuscato...), anche dopo conferma: una conferma vocale non basta contro un comando davvero pericoloso.
- `core/conversation_state.py` — oltre allo stato di conferma, ricorda le ultime entità nominate (file, app, finestra...) per risolvere i pronomi.
- `core/logger.py` — log strutturato (`data/jake_actions.jsonl`, ruota) con trace_id/durata/modello/rischio/verifica; `core/action_ledger.py` (F1) — ricevute append-only (`data/jake_ledger.jsonl`, non ruota mai): chi ha chiesto cosa, quale agente ha deciso, con quale autorizzazione e quale risultato; `core/session_recorder.py` — replay anonimizzato/deterministico delle sole azioni fallite (`tools/replay_session.py`), disattivato per default; `core/secrets_vault.py` (F1) — cifra a riposo con Windows DPAPI la passphrase admin e il token di Home Assistant salvati in config.json.
- `core/learning_manager.py` — apprendimento continuo; `core/skill_forge.py` — generazione e validazione di nuove skill.
- `core/system_advisor.py` — Jake proattivo alla Jarvis: nota da solo batteria scarica e disco quasi pieno e lo dice, senza che tu debba chiederglielo.
- `core/voice/` — Whisper (`stt_provider.py`, CUDA automatico), Edge TTS (`edge_tts_provider.py`), sessione wake word con dettatura/pausa/follow-up.
- `core/gui/hud/` — HUD a schermo intero (PySide6): `theme.py` (un solo gradiente condiviso), `glass.py` (pannelli di vetro: sfondo sfocato + gradiente + bordo), `widgets.py` (orb, forma d'onda, conversazione, contesto live, azioni rapide, barra comandi), `overlay.py` (finestra mascherata: vetro solo nei pannelli, resto trasparente e cliccabile), `win_effects.py` (acrilico Windows, click-through, no-activate).
- `skills/` — una classe per capacità (`metadata` + `execute`), ~200 in totale; `plugins/` — skill esterne e auto-generate.

## Configurazione (`config/settings.json`)

| Chiave | Default | Note |
|---|---|---|
| `ollama_model` | `qwen2.5:7b` | classificatore, agente, domande |
| `coder_model` | `qwen2.5-coder:7b` | fucina (se assente usa `ollama_model`) |
| `embedding_model` | `nomic-embed-text` | recupero semantico |
| `vision_model` | `qwen2.5vl:7b` | descrivere/cliccare lo schermo |
| `tts_engine` / `tts_voice` / `tts_rate` | `edge` / `it-IT-DiegoNeural` / `+8%` | `offline` per la voce di Windows; altre voci: Giuseppe, Elsa, Isabella |
| `stt_model` / `stt_device` | auto | forzare es. `medium` / `cpu` |
| `follow_up_seconds` | 6 | secondi in cui si può parlare senza wake word dopo una risposta (0 per disattivare) |
| `hud_mode` | `full` | `full` (tutti i pannelli) o `compact` (solo la pillola in basso) |
| `hud_backdrop` | `clear` | `clear` (desktop visibile fuori dai pannelli) o `immersive` (tutto lo schermo scurito, click fuori = chiudi) |
| `hud_hotkey` / `hud_auto_hide_seconds` | `ctrl+shift+j` / 6 | |
| `hud_quick_actions` | `[]` | lista di `{"label": "...", "command": "..."}` per le chip in più oltre a quelle di default |
| `blocked_intents` / `always_confirm_intents` | `[]` | policy di sicurezza |
| `admin_passphrase` | `""` | se impostata, le azioni a rischio ADMIN (spegnimento, comandi, skill auto-generate) chiedono questa passphrase invece della semplice conferma sì/no; vuota = disattivata. Cifrata a riposo con DPAPI (F1, `core/secrets_vault.py`): sul disco non compare mai in chiaro, e un valore già salvato in chiaro da una versione precedente viene cifrato in automatico alla prima apertura |
| `windows_hello_enabled` | `false` | F1: aggiunge Windows Hello (impronta/volto/PIN, verificato da Windows) come fattore di autenticazione per le azioni ADMIN, provato PRIMA della passphrase quando entrambi sono attivi - un fattore che non passa dalla voce, quindi non intercettabile ne' ripetibile da chi sente solo l'audio. Richiede Windows Hello già configurato in Windows; se annullato o non disponibile, si ripiega sulla passphrase (se impostata) o sulla semplice conferma sì/no |
| `companion_server_enabled` / `companion_server_port` | `false` / `8765` | server locale HTTP+SSE (core/companion_server.py) per un HUD nativo o un'app companion esterna: espone `/status`, `/events` (stream), `/command`, `/devices/<id>/claim|release`; ascolta solo su 127.0.0.1 |
| `home_assistant_url` / `home_assistant_token` | `""` / `""` | collega Jake a un hub [Home Assistant](https://www.home-assistant.io/) esistente (LIST_SMART_DEVICES/CONTROL_SMART_DEVICE): url tipo `http://homeassistant.local:8123`, token da Profilo → Sicurezza → token di accesso a lunga durata. Il token è cifrato a riposo con DPAPI, stesso meccanismo di `admin_passphrase` |
| `system_advisor_enabled` | `true` | avvisi proattivi (batteria scarica, disco quasi pieno): `false` per disattivarli |
| `session_recording_enabled` / `session_recording_verbatim` | `false` / `false` | replay delle azioni fallite (F0, `core/session_recorder.py`, `data/jake_sessions.jsonl`): disattivato per default; se acceso, i parametri sono redatti (solo forma/lunghezza) a meno che `_verbatim` non sia acceso anch'esso (parametri veri, per rilanciare davvero un fallimento con `python -m tools.replay_session --replay`); mai attivo in modalità privata |
| `JAKE_OLLAMA_URL` (variabile d'ambiente) | `http://127.0.0.1:11434` | usare sempre 127.0.0.1: `localhost` costa ~2 s a chiamata su Windows |

## Sviluppo

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v   # test unitari (niente Ollama/microfono)
```

Aggiungere una skill: un file in `skills/` con una classe (`metadata`, `execute`) registrata in `core/skill_registry.py`, più qualche frase in `training/intents.jsonl`; oppure un plugin in `plugins/` con `register(registry)` (vedi `plugins/example_coin_flip.py`).
