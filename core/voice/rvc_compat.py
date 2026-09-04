"""Shim di compatibilita' per rvc-python/fairseq con PyTorch recente.

fairseq e rvc-python risalgono a un'epoca in cui torch.load caricava sempre tutto
(weights_only=False di default). Da PyTorch 2.6 il default e' cambiato per sicurezza,
e i checkpoint di fairseq (dizionari, modelli HuBERT/RMVPE) non passano piu' l'allowlist
di default. Va importato PRIMA di 'rvc_python' (vedi convert_worker.py)."""

import torch

_original_load = torch.load


def _patched_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _original_load(*args, **kwargs)


torch.load = _patched_load
