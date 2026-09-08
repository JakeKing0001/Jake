# Benchmark (F0)

Misure ripetibili per NLU, agente e percezione dello schermo; STT su richiesta con un file audio
vero. Non fanno parte di `tests/` (che il progetto tiene deliberatamente senza dipendenze da
Ollama/microfono, vedi README): sono strumenti da lanciare a mano quando serve misurare qualcosa,
non controlli che devono passare a ogni commit. Ogni benchmark scrive un report JSON in
`benchmarks/results/` (locale, non versionato - vedi `.gitignore`), tramite `benchmarks/_report.py`
cosi' tutti condividono lo stesso formato (timestamp, piattaforma, statistiche di latenza
p50/p95) e due esecuzioni in momenti diversi si possono confrontare senza leggere codice diverso.

| Benchmark | Cosa misura | Serve |
|---|---|---|
| `bench_nlu.py` | Accuratezza e latenza del classificatore di intent (`core/router.py`) su un campione di `training/intents.jsonl`, con leave-one-out sulla corsia a corrispondenza esatta | Ollama in esecuzione |
| `bench_agent.py` | Passi, latenza ed esito dell'agente (`core/agent.py`) su 3 compiti composti sicuri (nessun effetto fuori da una cartella temporanea) | Ollama in esecuzione |
| `bench_computer_use.py` | Latenza di screenshot, OCR e query finestre (solo lettura, nessun click) | niente (OCR locale) |
| `bench_stt.py` | Latenza di trascrizione di Whisper (`core/voice/stt_provider.py`) | un file `.wav` reale passato con `--audio` |

```powershell
python -m benchmarks.bench_nlu --sample 80 --seed 42
python -m benchmarks.bench_agent
python -m benchmarks.bench_computer_use --iterations 5
python -m benchmarks.bench_stt --audio percorso\a\una\registrazione.wav
```

## Cosa manca e perché non è stato inventato

- **Wake word**: nessun benchmark automatico. Misurare falsi risvegli/mancati risvegli richiede
  un vero microfono, un vero ambiente acustico (silenzio, TV accesa, musica, distanza - vedi i
  criteri di uscita della fase F2 in [ROADMAP.md](../ROADMAP.md)) e una sessione dal vivo:
  qualunque numero prodotto senza quello sarebbe inventato, non misurato.
- **Accuratezza del click** (Computer Use): `bench_computer_use.py` misura solo la percezione
  (screenshot + OCR), non se un click arriva davvero sull'elemento giusto. Serve un dataset di
  task/UI reali e ripetibili, non ancora costruito - vedi "dataset locale di task reali e
  regression test" nella fase F3 della roadmap.
- **STT**: lo script esiste e funziona (verificato con un file sintetizzato via `pyttsx3` durante
  lo sviluppo, solo per controllare che il codice funzioni - non è un risultato di qualità
  vocale reale), ma il repository non versiona registrazioni vocali per scelta di privacy: va
  lanciato a mano con una voce vera per avere un numero significativo.
- **RAM/VRAM/CPU dell'intera pipeline in esecuzione insieme** (voce + NLU + agente + HUD): non
  misurato. La sezione Hardware del README riporta solo le dimensioni dei modelli su disco,
  misurate direttamente, non un profilo di carico.
