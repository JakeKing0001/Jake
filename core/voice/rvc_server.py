"""Server locale di conversione vocale RVC per Jake. Va eseguito con l'interprete di .venv-rvc
(dipendenze pesanti/datate, isolate dal venv principale). Jake (venv principale) gli parla via
HTTP, esattamente come fa con Ollama.

Uso: .venv-rvc\\Scripts\\python.exe core\\voice\\rvc_server.py --model jake_the_dog
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rvc_compat  # noqa: E402,F401  (va importato prima di rvc_python: applica il fix weights_only)

import uvicorn  # noqa: E402
from rvc_python.api import create_app  # noqa: E402
from rvc_python.infer import RVCInference  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--model", required=True, help="Nome del modello (sottocartella in rvc_models/)")
    parser.add_argument("--device", default=None, help="Default: cuda:0 se disponibile, altrimenti cpu:0.")
    parser.add_argument("--models-dir", default="rvc_models")
    args = parser.parse_args()

    import torch
    has_cuda = torch.cuda.is_available()
    if args.device is None:
        args.device = "cuda:0" if has_cuda else "cpu:0"

    # rmvpe (stima del pitch via rete neurale) e' molto piu' lento di harvest (DSP classico)
    # su CPU: senza GPU la differenza e' di secondi utili per una risposta vocale reattiva.
    f0method = "rmvpe" if has_cuda else "harvest"

    rvc = RVCInference(models_dir=args.models_dir, device=args.device)
    rvc.set_params(f0method=f0method, index_rate=0.6, protect=0.33)

    app = create_app()
    app.state.rvc = rvc
    rvc.load_model(args.model)

    print(f"RVC pronto: modello '{args.model}' su {args.device}, in ascolto su 127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
