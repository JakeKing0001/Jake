"""Alleggerimenti del runtime del 06/10/2026: embedding su CPU, VRAM da NVML, DLL CUDA con stt_device esplicito,
thread di OpenBLAS limitati all'avvio. Nessun Ollama, GPU o modello vero."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import core.ollama_client as client

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = {"primary": "gemma3:4b", "embedding": "nomic-embed-text", "embedding_gpu": False}


class EmbeddingOnCpuTest(unittest.TestCase):
    def test_embedding_model_always_runs_on_cpu_unless_asked(self):
        with mock.patch.object(client, "_settings", lambda: dict(SETTINGS)):
            self.assertEqual(client.runtime_options("nomic-embed-text:latest")["num_gpu"], 0)
            self.assertEqual(client.runtime_options("nomic-embed-text")["num_gpu"], 0)
        with mock.patch.object(client, "_settings", lambda: {**SETTINGS, "embedding_gpu": True}), \
                mock.patch.object(client, "_gpu_policy", None):
            self.assertNotIn("num_gpu", client.runtime_options("nomic-embed-text"))

    def test_both_embedding_paths_send_the_same_options(self):
        # opzioni diverse fra le due strade farebbero ricaricare il modello a Ollama a ogni chiamata
        sent = []

        def fake_post(self, path, payload, timeout=None):
            sent.append(payload)
            return {"embeddings": [[0.1, 0.2]]}

        class Response:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8")))
            return Response(json.dumps({"embeddings": [[0.1, 0.2]]}).encode("utf-8"))

        from core.embedding_provider import EmbeddingProvider

        with mock.patch.object(client, "_settings", lambda: dict(SETTINGS)), \
                mock.patch.object(client.OllamaClient, "_post", fake_post), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            client.OllamaClient().embed("nomic-embed-text", ["ciao"])
            EmbeddingProvider().embed("ciao")
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["options"], sent[1]["options"])
        self.assertEqual(sent[0]["keep_alive"], sent[1]["keep_alive"])


class NvmlTest(unittest.TestCase):
    def test_falls_back_to_nvidia_smi_without_nvml(self):
        import core.nvml as nvml

        done = mock.Mock(returncode=0, stdout="5120\n")
        with mock.patch.object(nvml, "_nvml", lambda: (None, None)), \
                mock.patch("subprocess.run", return_value=done):
            self.assertEqual(nvml.free_vram_mb(), 5120)
        with mock.patch.object(nvml, "_nvml", lambda: (None, None)), \
                mock.patch("subprocess.run", side_effect=OSError):
            self.assertIsNone(nvml.free_vram_mb())


class ExplicitCudaWhisperTest(unittest.TestCase):
    def test_cuda_dlls_are_registered_with_an_explicit_cuda_device(self):
        from core.voice import stt_provider

        fake_module = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"faster_whisper": fake_module}), \
                mock.patch.object(stt_provider, "_add_cuda_dll_dirs") as add_dirs, \
                mock.patch.object(stt_provider, "_gpu_total_vram_mb", return_value=8188):
            stt_provider.WhisperSttProvider(device="cuda")
        add_dirs.assert_called()


class OpenBlasThreadsTest(unittest.TestCase):
    def test_main_caps_openblas_threads_before_numpy(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
        code = "import os, main; print(os.environ.get('OPENBLAS_NUM_THREADS'))"
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "2", out.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
