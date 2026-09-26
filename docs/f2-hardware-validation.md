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

Non serve modificare `config/settings.json`: il runner della prova forza `voice_barge_in` e
`voice_partials` a `on` soltanto per la durata della sessione e misura la configurazione vocale
reale (se `voice_character` è impostato, la voce di personaggio RVC, non la voce di base).

Per un riferimento dei tempi della sola pipeline vocale (TTS di base, RVC, primo campione, pause fra
chunk, `answer()`), senza microfono né altoparlanti:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.bench_voice_pipeline --repeats 5 --with-core
```

## 2. Prova per ciascun profilo hardware

Selezionare in Windows microfono e uscita del profilo, quindi avviare la sessione strumentata
(stessa voce continua di `main.py --voice --wake-word`):

```powershell
.\.venv\Scripts\python.exe -m benchmarks.f2_hardware_session run --profile headphones
.\.venv\Scripts\python.exe -m benchmarks.f2_hardware_session run --profile laptop
.\.venv\Scripts\python.exe -m benchmarks.f2_hardware_session run --profile bluetooth
```

Durante la sessione:

1. per 20 volte chiedere una risposta abbastanza lunga e, circa un secondo dopo l'inizio del TTS,
   pronunciare un nuovo comando (contare mentalmente i tentativi);
2. lasciare completare 20 risposte senza parlare: almeno 10 in stanza normale e almeno 10 con TV o
   parlato di sottofondo; contare i falsi stop e i comandi eseguiti partendo dalla voce di Jake.

Jake misura da sé, senza salvare audio né testo: interruzioni riconosciute e latenza voce→stop,
prima emissione TTS (dalla fine della voce dell'utente), latenza del primo partial, finali
duplicati, frasi finali, attivazioni wake ed eco ignorate. Alla chiusura (Ctrl+C o "Jake, esci") il
runner chiede i conteggi che solo chi fa la prova conosce: interruzioni tentate, risposte lasciate
finire in stanza normale e con sottofondo (separatamente), falsi stop, comandi nati dall'eco, latenze
misurate con registrazione esterna (facoltative), falsi wake, wake intenzionali a 0,5 m, 3 m e 6 m
(separatamente) e wake non sentiti. Salva poi `benchmarks/results/f2_hardware_<profilo>_<data>.json`
(cartella ignorata da Git). I falsi stop vengono sottratti dalle interruzioni riconosciute da Jake,
così un falso stop non gonfia il tasso di riconoscimento.

La latenza di stop misurata da Jake è un limite inferiore: comprende la finestra di riconoscimento
e lo stop del provider, non la latenza d'ingresso della scheda audio. Se il p95 interno supera i
250 ms, oppure per una verifica a campione, registrare localmente microfono e loopback di sistema
con Audacity/OBS e misurare la distanza tra l'inizio della forma d'onda dell'utente e la fine del
TTS; inserire quei millisecondi quando il runner li chiede: sostituiscono la stima interna nel
verdetto (che resta comunque salvata in `measured_by_jake`). Senza misura esterna, una stima interna
fra 250 e 300 ms dà `VERIFY`, non `PASS`; oltre 300 ms è già `FAIL`. Non conservare la registrazione
dopo aver estratto i millisecondi. Il limite noto della TV non va escluso: se produce falsi
barge-in, il profilo fallisce.

Per STT/WER registrare, con consenso, almeno 20 frasi note per profilo in WAV PCM e lanciare per
ogni file:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.bench_stt --audio D:\corpus-f2rase-01.wav --device cpu --repeats 3
```

Tenere i WAV in una cartella dedicata fuori da `Jake`. Seguire
[`voice-corpus.md`](voice-corpus.md) per consenso, manifest, retention e cancellazione.

## 3. Sessione wake-word di 24 ore

Lasciare Jake in voce continua nell'ambiente reale, anche in più sessioni:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.f2_hardware_session run --profile wake --notes "silenzio, TV, musica"
```

Il runner registra le ore e l'ora di ogni attivazione accettata; nel foglio di prova annotare la
condizione (silenzio/TV/musica/conversazione) e la frase che ha causato ogni falso wake. Eseguire i
20 wake intenzionali distribuiti tra circa 0,5 m, 3 m e 6 m e riportare alla chiusura quanti non
sono stati sentiti. Se una delle tre distanze non è stata provata il criterio resta `VERIFY`.

## 4. Verdetto

```powershell
.\.venv\Scripts\python.exe -m benchmarks.f2_hardware_session evaluate
```

Il comando legge tutti i report del metodo corrente (quelli di versioni precedenti del runner sono
ignorati), applica le soglie di questo documento per ciascun profilo (vale l'ultimo report di ogni
profilo: una prova migliore non cancella un fallimento successivo) e somma ore, falsi wake e wake
intenzionali di tutte le sessioni. Il fallback a frase completa conta come `PASS` solo se i partial
erano davvero non disponibili (trascrittore assente o degradato) e sono state prodotte frasi finali:
l'assenza di dati resta `VERIFY`. Ogni criterio è `PASS`, `FAIL` oppure `VERIFY` quando mancano dati: il gate è verde
solo con tutti i profili e il wake in `PASS`. Il p95 usa lo stesso helper dei benchmark del
progetto (`benchmarks/_report.py::percentile`).

## 5. Chiusura e pulizia

1. Il default del progetto (`config/settings.example.json`) resta `voice_barge_in: "off"` finché
   `evaluate` non restituisce `PASS`; se nel proprio `config/settings.json` lo si è attivato a mano,
   riportarlo a `off` quando anche una sola cella non è verde.
2. Cancellare il corpus consensuale con
   `.\.venv\Scripts\python.exe -m benchmarks.voice_corpus purge D:\corpus-f2`.
3. Conservare i JSON locali e la matrice riassuntiva, mai WAV o tracce OBS/Audacity.
4. Aggiornare il Gate F2 nella roadmap con hardware, metodo, conteggi e p95 effettivi. Soltanto a
   quel punto cambiare F2 da `DOING` a `DONE`.
