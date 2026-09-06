# Jake 3.0

Assistente personale vocale, locale, per Windows. Ascolta, capisce l'italiano parlato, **fa le cose al posto tuo** (apre programmi e siti, cerca, scrive, clicca, gestisce file, finestre, timer, promemoria, musica...), risponde a domande, **impara comandi nuovi** e, quando non sa fare qualcosa, **si scrive da solo una nuova capacità**. Quando lo attivi compare un HUD in vetro semitrasparente blu, stile Jarvis, sopra qualunque finestra.

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
| `setup.ps1 -Autostart` | Jake parte a ogni accensione del PC |

Requisiti: Windows 10/11, Python 3.11+, [Ollama](https://ollama.com) con `qwen2.5:7b`, `nomic-embed-text` (e opzionali `qwen2.5-coder:7b` per la fucina, `qwen2.5vl:7b` per la visione). Con una GPU NVIDIA `setup.ps1` installa le librerie CUDA e Jake usa Whisper `large-v3-turbo` (riconoscimento in meno di mezzo secondo).

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
- **Su Jake stesso**: «cosa sai fare», «ripeti», «zitto», «non ascoltare per 10 minuti» ... «Jake, svegliati», «no, intendevo apri discord»

Dopo una risposta puoi continuare a parlare per qualche secondo senza ripetere «Jake» (finestra di follow-up). Le domande di conferma («confermi?») accettano il sì/no direttamente.

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
                                 errori noti di Whisper       ├─ corsia veloce: frase già nota          └─ HUD (Qt, acrilico, sempre sopra)
                                                              ├─ classificatore Ollama con recupero semantico
                                                              │    (solo le ~18 capacità pertinenti + esempi few-shot)
                                                              ├─ regole locali (se Ollama non risponde)
                                                              └─ planner multi-step / domanda libera / fucina
```

- `core/nlu/` — normalizzazione, esempi, indice semantico (embedding con cache su disco, fallback lessicale), retriever, chitchat, classificatore LLM.
- `core/learning_manager.py` — apprendimento continuo; `core/skill_forge.py` — generazione e validazione di nuove skill.
- `core/voice/` — Whisper (`stt_provider.py`, CUDA automatico), Edge TTS (`edge_tts_provider.py`), sessione wake word con dettatura/pausa/follow-up.
- `core/gui/hud/` — HUD (PySide6): finestra frameless traslucida, blur acrilico via `SetWindowCompositionAttribute`, click-through in modalità vocale, barra comandi con hotkey, tray.
- `skills/` — una classe per capacità (`metadata` + `execute`), ~200 in totale; `plugins/` — skill esterne e auto-generate.

## Configurazione (`config/settings.json`)

| Chiave | Default | Note |
|---|---|---|
| `ollama_model` | `qwen2.5:7b` | classificatore, planner, domande |
| `coder_model` | `qwen2.5-coder:7b` | fucina (se assente usa `ollama_model`) |
| `embedding_model` | `nomic-embed-text` | recupero semantico |
| `vision_model` | `qwen2.5vl:7b` | descrivere/cliccare lo schermo |
| `tts_engine` / `tts_voice` / `tts_rate` | `edge` / `it-IT-DiegoNeural` / `+8%` | `offline` per la voce di Windows; altre voci: Giuseppe, Elsa, Isabella |
| `stt_model` / `stt_device` | auto | forzare es. `medium` / `cpu` |
| `follow_up_seconds` | 6 | secondi in cui si può parlare senza wake word dopo una risposta (0 per disattivare) |
| `hud_hotkey` / `hud_auto_hide_seconds` / `hud_acrylic` | `ctrl+shift+j` / 5 / true | |
| `blocked_intents` / `always_confirm_intents` | `[]` | policy di sicurezza |
| `JAKE_OLLAMA_URL` (variabile d'ambiente) | `http://127.0.0.1:11434` | usare sempre 127.0.0.1: `localhost` costa ~2 s a chiamata su Windows |

## Sviluppo

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v   # test unitari (niente Ollama/microfono)
```

Aggiungere una skill: un file in `skills/` con una classe (`metadata`, `execute`) registrata in `core/skill_registry.py`, più qualche frase in `training/intents.jsonl`; oppure un plugin in `plugins/` con `register(registry)` (vedi `plugins/example_coin_flip.py`).
