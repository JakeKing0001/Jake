import queue

import numpy as np

from core.voice.utterance_segmenter import UtteranceSegmenter, to_float32


class VadListener:
    """Ascolta il microfono in continuo e segmenta l'audio in singole frasi via rilevamento
    voce (VAD), invece del push-to-talk: e' la base della voce continua (v2.0)."""

    SAMPLE_RATE = 16000
    FRAME_MS = 30  # webrtcvad accetta solo frame da 10/20/30 ms
    FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

    def __init__(
        self,
        aggressiveness: int = 2,
        silence_ms: int = 700,
        max_utterance_s: float = 12.0,
        device: int = None,
        on_level=None,
    ):
        import webrtcvad

        self.vad = webrtcvad.Vad(aggressiveness)
        self.silence_frames_needed = max(1, silence_ms // self.FRAME_MS)
        self.max_frames = int(max_utterance_s * 1000 // self.FRAME_MS)
        self.device = device
        # v3.0: callback(livello 0..1, parlato: bool) per ogni frame, usato dall'HUD per la
        # forma d'onda. Deve essere leggerissimo: gira sul thread di ascolto.
        self.on_level = on_level
        self.muted = False  # True mentre Jake parla, per non trascrivere la propria voce
        # F2.4.3: mentre Jake parla i frame NON si accumulano (muted), ma lo stream resta aperto e ogni
        # frame viene offerto a questo callback(frame_int16, is_speech, livello 0..1), che decide se
        # l'utente sta interrompendo (barge-in). Gira sul thread di ascolto: deve restare leggero.
        self.on_speaking_frame = None
        self._segmenter: UtteranceSegmenter | None = None

    def is_speech_pcm(self, pcm16: bytes) -> bool:
        """Il VAD sul PCM int16 di un frame da 30 ms (usato sul residuo dopo la cancellazione d'eco)."""
        return bool(self.vad.is_speech(pcm16, self.SAMPLE_RATE))

    def begin_utterance(self, frames: list) -> None:
        """Barge-in riconosciuto: si smette di scartare i frame e si parte con `frames` (pre-roll) come
        inizio di una frase in corso. Va chiamato dal thread di ascolto (dentro `on_speaking_frame`)."""
        self.muted = False
        if self._segmenter is not None:
            self._segmenter.seed(frames)

    def is_available(self) -> bool:
        try:
            import sounddevice as sd
            devices = sd.query_devices()
        except Exception:
            return False
        return any(device.get("max_input_channels", 0) > 0 for device in devices)

    def listen_for_utterances(self, should_continue):
        """Generatore: produce un array numpy float32 mono per ogni frase rilevata, finche'
        should_continue() e' vero. Bloccante (in attesa di parlato) tra una frase e l'altra."""
        import sounddevice as sd

        frame_queue: queue.Queue = queue.Queue()

        def callback(indata, frame_count, time_info, status):
            frame_queue.put(indata.copy())

        with sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=self.FRAME_SAMPLES,
            device=self.device,
            callback=callback,
        ):
            segmenter = UtteranceSegmenter(self.silence_frames_needed, self.max_frames)
            self._segmenter = segmenter

            while should_continue():
                try:
                    frame = frame_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if frame.shape[0] != self.FRAME_SAMPLES:
                    continue  # frame incompleto (di solito solo all'avvio/chiusura dello stream)

                is_speech = self.vad.is_speech(frame.tobytes(), self.SAMPLE_RATE)
                if self.on_level is not None:
                    try:
                        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) / 32768.0
                        self.on_level(min(1.0, rms * 8.0), bool(is_speech))
                    except Exception:
                        pass

                if self.muted:
                    segmenter.reset()
                    if self.on_speaking_frame is not None:
                        try:
                            rms_level = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) / 32768.0
                            self.on_speaking_frame(frame.reshape(-1), bool(is_speech), min(1.0, rms_level))
                        except Exception:
                            pass
                        if self.muted:
                            continue
                        # il callback ha riconosciuto un'interruzione (begin_utterance): il frame corrente
                        # e' gia' parlato dell'utente e va accumulato come tutti i seguenti
                    else:
                        continue

                utterance = segmenter.feed(frame, bool(is_speech))
                if utterance is not None:
                    yield utterance

    @staticmethod
    def _to_float32(frames: list) -> "np.ndarray":
        return to_float32(frames)
