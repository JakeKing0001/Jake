import queue

import numpy as np


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
    ):
        import webrtcvad

        self.vad = webrtcvad.Vad(aggressiveness)
        self.silence_frames_needed = max(1, silence_ms // self.FRAME_MS)
        self.max_frames = int(max_utterance_s * 1000 // self.FRAME_MS)
        self.device = device

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
            speech_frames = []
            silence_run = 0
            in_speech = False

            while should_continue():
                try:
                    frame = frame_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if frame.shape[0] != self.FRAME_SAMPLES:
                    continue  # frame incompleto (di solito solo all'avvio/chiusura dello stream)

                is_speech = self.vad.is_speech(frame.tobytes(), self.SAMPLE_RATE)

                if is_speech:
                    speech_frames.append(frame)
                    silence_run = 0
                    in_speech = True
                elif in_speech:
                    speech_frames.append(frame)  # include un po' di coda dopo il parlato
                    silence_run += 1
                    if silence_run >= self.silence_frames_needed or len(speech_frames) >= self.max_frames:
                        yield self._to_float32(speech_frames)
                        speech_frames = []
                        silence_run = 0
                        in_speech = False

    @staticmethod
    def _to_float32(frames: list) -> "np.ndarray":
        pcm16 = np.concatenate(frames, axis=0).reshape(-1)
        return (pcm16.astype(np.float32)) / 32768.0
