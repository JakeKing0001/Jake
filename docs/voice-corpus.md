# Corpus audio per i benchmark voce (F2.1)

Cosa e' permesso mettere in un corpus, dove sta e come si cancella. Vale per
`benchmarks/bench_vad.py`, `benchmarks/bench_stt.py` e per i benchmark voce futuri (wake word,
barge-in).

## Tre tipi di clip

| Tipo | Origine | Nel repository? | Nell'hash stabile? |
|---|---|---|---|
| Sintetiche deterministiche | generate da numpy con seed fisso (`build_synthetic_corpus`) | mai scritte su disco: si rigenerano | si |
| Sintesi vocale locale | frasi dette da una voce SAPI installata (`--with-tts`, `--tts-corpus`) | mai: WAV temporanei cancellati subito | no (dipendono dalle voci del PC) |
| Registrazioni consensuali | persone vere, con consenso scritto | **mai** | non applicabile |

## Licenza

- Le clip sintetiche sono prodotte dal codice del progetto e seguono la sua licenza.
- Le clip TTS sono generate dalla voce di sistema dell'utente: non vanno ridistribuite, servono
  solo a misurare sulla stessa macchina.
- Nessun dataset di terzi e' incluso. Se in futuro ne serve uno, va aggiunto qui con licenza,
  fonte e hash prima di usarlo.

## Consenso (registrazioni di persone)

Una registrazione di una persona entra in un corpus locale solo se:

1. la persona ha dato consenso esplicito e revocabile, sapendo che serve a misurare Jake;
2. il file resta in una cartella dedicata fuori dal repository (mai sotto `Jake/`);
3. i metadati (chi, quando, dispositivo, distanza, rumore) stanno nel manifest, non nel nome del file;
4. viene annotato chi ha il diritto di chiederne la cancellazione.

## Cosa esce da una misura (F2.1.4)

I report in `benchmarks/results/` (gia' ignorata da git) e i manifest contengono solo: id delle
clip, durate, etichette dei segmenti, testi di riferimento, metriche (falsi accettati/rifiutati,
WER, latenze), hash SHA-256 e profilo hardware. **Mai campioni audio.** Un test
(`test_manifest_contains_no_audio_samples`) fissa questo limite.

## Retention e cancellazione (F2.1.6)

- Le clip sintetiche non hanno retention: non esistono su disco.
- I WAV temporanei della sintesi vocale vivono il tempo di una lettura.
- Un corpus di registrazioni vere vive finche' dura il consenso; alla revoca si cancella
  l'intera cartella:

```powershell
python -m benchmarks.voice_corpus purge D:\corpus-voce-consensuale
```

Il comando rifiuta un file singolo, la radice di un disco e la cartella home, e stampa quanti
file ha cancellato. Poi vanno cancellati anche i report che citano quelle clip (contengono solo
metriche e hash, ma l'hash dimostra che quella clip esisteva).

## Baseline separate CPU/GPU (F2.1.5)

Ogni report porta `hardware.profile` (`cpu` o `gpu`) e il nome del file lo contiene
(`vad_cpu_*.json`, `stt_corpus_gpu_*.json`): una misura su CPU non si confronta mai con una su GPU,
e due report sono confrontabili solo se `corpus.hash` coincide.
