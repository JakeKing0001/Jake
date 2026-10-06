"""Toglie dal training di Piper il ModelCheckpoint su "val_mos".

Il commento in piper/train/__main__.py dice che senza predittore UTMOS Lightning "avvisa e salta", ma la versione
installata solleva MisconfigurationException a fine epoca (prova del 05/10/2026). Resta il checkpoint su val_mel
(+ last.ckpt), che e' quello usato da train.ps1.
"""
import re
from pathlib import Path

path = Path("/piper/src/piper/train/__main__.py")
source = path.read_text(encoding="utf-8")
patched, count = re.subn(r"\n    ModelCheckpoint\(\n        monitor=\"val_mos\",.*?\n    \),", "", source, flags=re.DOTALL)
if count != 1:
    raise SystemExit(f"patch val_mos: attesa 1 sostituzione, trovate {count}")
# Un checkpoint pesa ~807 MB: 5 migliori + last facevano ~4,8 GB per corsa (prova del 06/10/2026, disco da 150 a 98 GB
# liberi). Ne bastano 2 + last: si ascolta l'ultimo e si tiene il migliore per val_mel.
if patched.count("save_top_k=5,") != 1:
    raise SystemExit("patch save_top_k: attesa 1 occorrenza")
patched = patched.replace("save_top_k=5,", "save_top_k=2,")
path.write_text(patched, encoding="utf-8")
print("patch val_mos e save_top_k=2 applicate")

# torch >= 2.9 esporta con dynamo di default, che fallisce sulle forme dipendenti dai dati di VITS (durate dei fonemi):
# l'esportatore TorchScript classico e' quello con cui sono state esportate tutte le voci Piper pubblicate.
export = Path("/piper/src/piper/train/export_onnx.py")
source = export.read_text(encoding="utf-8")
if "dynamo=False" not in source:
    if source.count("        verbose=False,\n") != 1:
        raise SystemExit("patch export: chiamata a torch.onnx.export non trovata")
    export.write_text(source.replace("        verbose=False,\n", "        verbose=False,\n        dynamo=False,\n"),
                      encoding="utf-8")
print("patch export dynamo=False applicata")
