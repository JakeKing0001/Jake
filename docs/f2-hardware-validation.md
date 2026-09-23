# Chiusura hardware di F2

Questa è la prova finale, manuale e ripetibile, di Voice Natural 3.0. Va eseguita su hardware
fisico: simulazioni, voci SAPI e mock non possono rendere verde il gate. I file audio grezzi
restano fuori dal repository e vengono cancellati alla fine; si conservano soltanto report,
conteggi, hash e descrizione dell'hardware.

## Requisiti di superamento

Usare almeno tre profili di uscita realmente diversi:

1. cuffie cablate o USB;
2. altoparlanti e microfono integrati del portatile;
3. cuffie o speaker Bluetooth (annotare anche se Windows usa il profilo hands-free).

Per ogni profilo eseguire almeno 20 interruzioni vere e 20 risposte senza interruzione. F2 passa
solo se, su ciascun profilo:

- almeno 19 interruzioni su 20 fermano il TTS (>= 95%);
- il p95 tra inizio della voce dell'utente e stop del TTS è <= 300 ms;
- nessun comando viene prodotto dall'eco del TTS;
- i partial transcript hanno p95 < 1 s quando supportati; altrimenti il fallback a frase completa
  deve attivarsi senza duplicare il testo finale;
- la prima emissione TTS ha p95 < 2 s per una risposta semplice.

Serve inoltre una sessione wake-word di almeno 24 ore complessive, comprendente silenzio,
conversazione, musica e TV: non più di un falso wake in 24 ore. Eseguire almeno 20 wake
intenzionali distribuiti tra circa 0,5 m, 3 m e 6 m; il miss rate deve essere <= 5% (almeno 19/20).
Se un solo requisito fallisce, non abilitare `voice_barge_in` per default e non chiudere F2.

## 1. Preparazione

Da PowerShell nella radice del progetto:

```powershell
.\.venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.query_devices())"
.\.venv\Scripts\python.exe -m benchmarks.bench_vad --aggressiveness 2 --with-tts
.\.venv\Scripts\python.exe -m benchmarks.bench_stt --tts-corpus --device cpu
.\.venv\Scripts\python.exe -m benchmarks.bench_barge_in --trials 20
```

Annotare modello di PC, CPU/GPU, RAM, versione di Windows, microfono, uscita, driver, modello STT,
provider TTS e nomi/indici mostrati da `sounddevice`. I report sintetici sono solo baseline.

In `config/settings.json` impostare temporaneamente, conservando tutte le altre chiavi:

```json
"voice_barge_in": "on",
"voice_partials": "on"
```

`on` è intenzionale per la prova; al termine si torna a `off` finché il gate non è tutto verde.

## 2. Prova per ciascun profilo hardware

Selezionare in Windows microfono e uscita del profilo, quindi avviare:

```powershell
.\.venv\Scripts\python.exe main.py --voice --wake-word
```

Per 20 volte chiedere una risposta abbastanza lunga e, circa un secondo dopo l'inizio del TTS,
pronunciare un nuovo comando. Registrare `rilevata sì/no` e la latenza. La misura consigliata è
una registrazione locale temporanea con Audacity/OBS contenente microfono e loopback di sistema:
la latenza è la distanza tra l'inizio della forma d'onda dell'utente e la fine del TTS. Non serve
conservare o condividere la registrazione dopo aver estratto i millisecondi.

Poi lasciare completare 20 risposte senza parlare: almeno 10 in stanza normale e almeno 10 con
TV o parlato di sottofondo. Contare separatamente falsi stop e falsi comandi. Il limite noto della
TV non va escluso: se produce falsi barge-in, il profilo fallisce.

Per STT/WER registrare, con consenso, almeno 20 frasi note per profilo in WAV PCM e lanciare per
ogni file:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.bench_stt --audio D:\corpus-f2\frase-01.wav --device cpu --repeats 3
```

Tenere i WAV in una cartella dedicata fuori da `Jake`. Seguire
[`voice-corpus.md`](voice-corpus.md) per consenso, manifest, retention e cancellazione.

## 3. Sessione wake-word di 24 ore

Lasciare Jake in voce continua nell'ambiente reale. Nel foglio di prova annotare ora, condizione
(silenzio/TV/musica/conversazione), frase che ha causato ogni falso wake e ogni crash. Eseguire i
20 wake intenzionali durante la stessa sessione, alle tre distanze indicate. Non occorre
conservare audio per contare gli eventi.

## 4. Report locale minimo

Creare `benchmarks/results/f2_hardware_<data>.json` (cartella ignorata da Git) per ciascun profilo:

```json
{
  "method_version": 1,
  "hardware": {"pc": "...", "input": "...", "output": "...", "driver": "..."},
  "software": {"windows": "...", "stt_model": "...", "stt_device": "cpu|cuda", "tts": "..."},
  "barge_in": {
    "attempts": 20, "detected": 0, "latency_ms": [],
    "no_interruption_trials": 20, "false_stops": 0, "echo_commands": 0
  },
  "streaming": {"partial_latency_ms": [], "duplicate_finals": 0},
  "tts": {"first_emission_ms": []},
  "wake": {"hours": 0, "false_wakes": 0, "intentional": 20, "missed": 0},
  "raw_audio_retained": false,
  "notes": ""
}
```

Calcolare il p95 con lo stesso helper del progetto:

```powershell
.\.venv\Scripts\python.exe -c "from benchmarks._report import latency_stats; print(latency_stats([INCOLLA, QUI, I, MILLISECONDI]))"
```

Il report finale può sommare le ore wake, ma i risultati barge-in devono restare separati: una
media buona non può nascondere un dispositivo che fallisce.

## 5. Chiusura e pulizia

1. Ripristinare `voice_barge_in` a `off` se anche una sola cella non è verde; abilitarlo di default
   soltanto dopo tre profili verdi.
2. Cancellare il corpus consensuale con
   `.\.venv\Scripts\python.exe -m benchmarks.voice_corpus purge D:\corpus-f2`.
3. Conservare i JSON locali e la matrice riassuntiva, mai WAV o tracce OBS/Audacity.
4. Aggiornare il Gate F2 nella roadmap con hardware, metodo, conteggi e p95 effettivi. Soltanto a
   quel punto cambiare F2 da `DOING` a `DONE`.
