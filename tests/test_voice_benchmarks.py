"""Test per l'harness audio (F2.1): metriche pure, corpus sintetico deterministico, runner VAD
offline e runner WER con un provider finto. Nessun microfono, nessun modello Whisper. L'unico
test che tocca webrtcvad usa il pacchetto VERO (e' una dipendenza normale del progetto) su clip
per cui l'esito e' stabile: silenzio digitale e parlato sintetico forte."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from benchmarks import bench_vad
from benchmarks.bench_stt import run_corpus
from benchmarks.voice_corpus import (
    FRAME_SAMPLES, SAMPLE_RATE, CorpusEntry, Segment, build_synthetic_corpus, corpus_hash, entry_hash,
    frame_truth, frames, manifest, purge_corpus_dir,
)
from benchmarks.voice_metrics import confusion, hardware_profile, merge_confusions, word_error_rate


class ConfusionTests(unittest.TestCase):
    def test_counts_and_rates(self):
        result = confusion([True, True, False, False, False], [True, False, True, False, False])
        self.assertEqual((result["tp"], result["fn"], result["fp"], result["tn"]), (1, 1, 1, 2))
        self.assertAlmostEqual(result["false_accept_rate"], 1 / 3, places=3)
        self.assertEqual(result["false_reject_rate"], 0.5)

    def test_a_rate_without_data_is_none_not_zero(self):
        result = confusion([False, False], [False, False])
        self.assertIsNone(result["false_reject_rate"])  # nessun frame di parlato: "nessun dato"
        self.assertEqual(result["false_accept_rate"], 0.0)

    def test_length_mismatch_is_an_error(self):
        with self.assertRaises(ValueError):
            confusion([True], [True, False])

    def test_merge_weights_by_frames_not_by_clip(self):
        tiny = confusion([False, False], [True, True])  # 2 frame, FA=1.0
        large = confusion([False] * 98, [False] * 98)  # 98 frame, FA=0.0
        merged = merge_confusions([tiny, large])
        self.assertEqual(merged["false_accept_rate"], 0.02)  # non la media (0.5) dei due tassi


class WordErrorRateTests(unittest.TestCase):
    def test_identical_text_is_zero_ignoring_case_and_punctuation(self):
        self.assertEqual(word_error_rate("Apri Spotify", "apri spotify."), 0.0)

    def test_substitution_insertion_deletion(self):
        self.assertEqual(word_error_rate("uno due tre quattro", "uno due tre cinque"), 0.25)
        self.assertEqual(word_error_rate("uno due", "uno due tre"), 0.5)
        self.assertEqual(word_error_rate("uno due tre quattro", "uno tre"), 0.5)

    def test_empty_hypothesis_is_total_error(self):
        self.assertEqual(word_error_rate("apri spotify", ""), 1.0)

    def test_empty_reference_is_undefined(self):
        self.assertIsNone(word_error_rate("", "qualcosa"))

    def test_digits_versus_words_count_as_errors(self):
        # limite noto del WER puro, documentato: "10" e "dieci" sono parole diverse (F2.6.5).
        self.assertEqual(word_error_rate("dieci minuti", "10 minuti"), 0.5)

    def test_number_normalization_removes_that_false_error_but_not_real_ones(self):
        self.assertEqual(word_error_rate("dieci minuti", "10 minuti", normalize_numbers=True), 0.0)
        self.assertEqual(word_error_rate("metti un timer di dieci minuti", "metti un timer di 10 minuti", normalize_numbers=True), 0.0)
        self.assertEqual(word_error_rate("dieci minuti", "12 minuti", normalize_numbers=True), 0.5)  # un numero SBAGLIATO resta un errore


class HardwareProfileTests(unittest.TestCase):
    def test_profile_separates_cpu_and_gpu_baselines(self):
        self.assertEqual(hardware_profile(cuda=False)["profile"], "cpu")
        self.assertEqual(hardware_profile(cuda=True)["profile"], "gpu")

    def test_reports_the_machine_that_produced_the_number(self):
        profile = hardware_profile(cuda=False)
        for key in ("cpu", "logical_cores", "python", "os"):
            self.assertIn(key, profile)


class SyntheticCorpusTests(unittest.TestCase):
    def test_same_seed_gives_identical_bytes(self):
        first, second = build_synthetic_corpus(7), build_synthetic_corpus(7)
        self.assertEqual(corpus_hash(first), corpus_hash(second))
        self.assertEqual([entry_hash(e) for e in first], [entry_hash(e) for e in second])

    def test_different_seed_changes_the_hash(self):
        self.assertNotEqual(corpus_hash(build_synthetic_corpus(1)), corpus_hash(build_synthetic_corpus(2)))

    def test_covers_speech_silence_noise_and_distance(self):
        corpus = build_synthetic_corpus()
        tags = {tag for entry in corpus for tag in entry.tags}
        self.assertTrue({"negative", "hard-negative", "speech", "far", "multi", "pause-inside"} <= tags)
        self.assertGreater(max(e.distance_m for e in corpus), 1.0)

    def test_every_clip_is_16k_mono_int16_and_annotations_cover_it(self):
        for entry in build_synthetic_corpus():
            self.assertEqual(entry.pcm.dtype, np.int16)
            self.assertEqual(entry.pcm.ndim, 1)
            self.assertAlmostEqual(entry.segments[-1].end_s, entry.duration_s, places=2, msg=entry.id)
            self.assertEqual(entry.segments[0].start_s, 0.0)

    def test_manifest_contains_no_audio_samples(self):
        # F2.1.4: solo etichette, metriche e hash - mai i campioni.
        import json

        text = json.dumps(manifest(build_synthetic_corpus()))
        self.assertIn("sha256", text)
        self.assertNotIn("pcm", text)
        self.assertLess(len(text), 20_000)  # 9 clip di secondi di audio sarebbero centinaia di KB

    def test_non_deterministic_clips_do_not_change_the_stable_hash(self):
        base = build_synthetic_corpus()
        extra = CorpusEntry("tts_x", np.zeros(1600, dtype=np.int16), [Segment("silence", 0.0, 0.1)], deterministic=False)
        self.assertEqual(corpus_hash(base), corpus_hash(base + [extra]))

    def test_frames_are_30ms_and_truth_matches_annotations(self):
        entry = next(e for e in build_synthetic_corpus() if e.id == "one_utterance")
        entry_frames = frames(entry)
        self.assertTrue(all(f.shape == (FRAME_SAMPLES, 1) for f in entry_frames))
        truth = frame_truth(entry)
        self.assertEqual(len(truth), len(entry_frames))
        self.assertFalse(truth[0])  # 0,6 s di silenzio iniziale
        self.assertTrue(truth[len(truth) // 2 - 5])  # dentro il segmento di parlato (0,6-2,1 s)
        self.assertAlmostEqual(sum(truth) * 0.03, 1.5, delta=0.06)

    def test_expected_utterances_merges_gaps_shorter_than_the_threshold(self):
        by_id = {e.id: e for e in build_synthetic_corpus()}
        self.assertEqual(by_id["two_bursts_short_gap"].expected_utterances(0.7), 1)  # pausa 0,25 s
        self.assertEqual(by_id["two_bursts_short_gap"].expected_utterances(0.2), 2)
        self.assertEqual(by_id["two_utterances_long_gap"].expected_utterances(0.7), 2)  # pausa 1,2 s
        self.assertEqual(by_id["silence_3s"].expected_utterances(0.7), 0)


class PurgeTests(unittest.TestCase):
    def test_deletes_the_directory_and_reports_the_file_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus_dir = Path(tmp) / "consensual"
            (corpus_dir / "a").mkdir(parents=True)
            (corpus_dir / "a" / "one.wav").write_bytes(b"x")
            (corpus_dir / "two.wav").write_bytes(b"y")
            self.assertEqual(purge_corpus_dir(corpus_dir), 2)
            self.assertFalse(corpus_dir.exists())

    def test_refuses_a_plain_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "solo.wav"
            file_path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                purge_corpus_dir(file_path)
            self.assertTrue(file_path.exists())

    def test_refuses_the_home_and_the_drive_root(self):
        with self.assertRaises(ValueError):
            purge_corpus_dir(Path.home())
        with self.assertRaises(ValueError):
            purge_corpus_dir(Path(Path.home().anchor))


class VadRunnerTests(unittest.TestCase):
    def test_a_detector_that_hears_everything_yields_full_false_accepts_on_silence(self):
        entry = next(e for e in build_synthetic_corpus() if e.id == "silence_3s")
        result = bench_vad.evaluate_entry(entry, lambda pcm: True)
        self.assertEqual(result["confusion"]["false_accept_rate"], 1.0)
        self.assertEqual(result["utterances_found"], 1)
        self.assertEqual(result["utterances_expected"], 0)

    def test_a_detector_that_hears_nothing_yields_full_false_rejects_on_speech(self):
        entry = next(e for e in build_synthetic_corpus() if e.id == "one_utterance")
        result = bench_vad.evaluate_entry(entry, lambda pcm: False)
        self.assertEqual(result["confusion"]["false_reject_rate"], 1.0)
        self.assertEqual(result["utterances_found"], 0)

    def test_an_oracle_detector_finds_exactly_the_expected_utterances(self):
        for entry in build_synthetic_corpus():
            truth = iter(frame_truth(entry))
            result = bench_vad.evaluate_entry(entry, lambda pcm, t=truth: next(t))
            self.assertEqual(result["utterances_found"], result["utterances_expected"], entry.id)
            self.assertEqual(result["confusion"]["fp"] + result["confusion"]["fn"], 0, entry.id)

    def test_run_gets_a_fresh_detector_per_clip(self):
        created = []

        def make():
            created.append(1)
            return lambda pcm: False

        entries = build_synthetic_corpus()
        bench_vad.run(entries, make_detector=make)
        self.assertEqual(len(created), len(entries))  # niente stato di rumore condiviso tra clip

    def test_report_carries_hash_hardware_and_no_audio(self):
        import json

        entries = build_synthetic_corpus()
        report = bench_vad.run(entries, make_detector=lambda: (lambda pcm: False))
        self.assertEqual(report["corpus"]["hash"], corpus_hash(entries))
        self.assertEqual(report["hardware"]["profile"], "cpu")
        self.assertLess(len(json.dumps(report)), 30_000)

    def test_real_webrtcvad_rejects_digital_silence_and_hears_the_synthetic_voice(self):
        by_id = {e.id: e for e in build_synthetic_corpus()}
        report = bench_vad.run([by_id["silence_3s"], by_id["one_utterance"]], aggressiveness=2)
        silence, speech = report["per_clip"]
        self.assertLess(silence["confusion"]["false_accept_rate"], 0.1)
        self.assertEqual(speech["confusion"]["false_reject_rate"], 0.0)
        self.assertEqual(speech["utterances_found"], 1)


class _FakeStt:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def transcribe(self, audio, sample_rate):
        self.calls.append((audio.dtype, audio.ndim, sample_rate, float(np.max(np.abs(audio)))))
        return self.answers.pop(0)


class SttCorpusRunnerTests(unittest.TestCase):
    def _entries(self):
        pcm = np.full(SAMPLE_RATE, 1000, dtype=np.int16)
        return [
            CorpusEntry("tts_0", pcm, [Segment("speech", 0.0, 1.0, "apri spotify")], ground_truth_text="apri spotify", deterministic=False),
            CorpusEntry("tts_1", pcm, [Segment("speech", 0.0, 1.0, "che ore sono")], ground_truth_text="che ore sono", deterministic=False),
            CorpusEntry("silence", np.zeros(100, dtype=np.int16), [Segment("silence", 0.0, 0.006)]),
        ]

    def test_computes_wer_per_clip_and_mean_and_skips_clips_without_reference(self):
        provider = _FakeStt(["Apri Spotify.", "che ore"])
        report = run_corpus(provider, self._entries())
        self.assertEqual([c["wer"] for c in report["per_clip"]], [0.0, 0.3333])
        self.assertAlmostEqual(report["mean_wer"], 0.1667, places=3)
        self.assertAlmostEqual(report["mean_wer_numbers_normalized"], 0.1667, places=3)
        self.assertEqual(len(provider.calls), 2)  # la clip di silenzio senza testo non viene trascritta

    def test_audio_is_float32_mono_normalized(self):
        provider = _FakeStt(["a", "b"])
        run_corpus(provider, self._entries())
        dtype, ndim, rate, peak = provider.calls[0]
        self.assertEqual((str(dtype), ndim, rate), ("float32", 1, SAMPLE_RATE))
        self.assertLessEqual(peak, 1.0)

    def test_repeats_multiply_latency_samples(self):
        provider = _FakeStt(["x"] * 4)
        report = run_corpus(provider, self._entries(), repeats=2)
        self.assertEqual(report["latency"]["count"], 4)


if __name__ == "__main__":
    unittest.main()
