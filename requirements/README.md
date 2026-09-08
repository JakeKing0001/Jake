# Dipendenze (F0)

Sorgenti editabili, un file per area, ciascuno pinnato alla versione esatta verificata in
questo ambiente (non range impliciti):

| File | Contenuto |
|---|---|
| `base.txt` | Automazione PC, fusi orari - serve sempre |
| `voice.txt` | Riconoscimento e sintesi vocale offline |
| `gui.txt` | Tray icon legacy (`--tray`) |
| `hud.txt` | HUD in vetro (Qt), voce online, volume esatto |
| `gpu.txt` | CUDA/cuDNN per Whisper su GPU NVIDIA (opzionale) |
| `dev.txt` | Lint e type-check (ruff, mypy) - non serve per far girare Jake |

`../requirements.txt` e `../requirements-gpu.txt` alla radice del repo aggregano questi file con
`-r`: `setup.ps1` continua a installare da lì senza modifiche.

## Lock file con hash

`all.lock.txt` (base+voice+gui+hud+dev) e `gpu.lock.txt` sono generati con
[pip-tools](https://github.com/jazzband/pip-tools) (`pip-compile --generate-hashes`) e usati
dalla CI (`.github/workflows/ci.yml`) con `pip install --require-hashes`, che rifiuta qualunque
pacchetto il cui hash non combaci invece di fidarsi implicitamente di ciò che l'indice risolve
quel giorno.

**Vanno compilati insieme**, non un file per gruppo: compilarli separatamente ha prodotto una
volta due versioni diverse di `numpy` (una fissata in `hud.txt`, una transitiva risolta a parte
da `voice.txt`) che `pip install --require-hashes` rifiutava come conflitto reale appena
installate insieme. `gpu.txt` resta a parte perché non condivide dipendenze con gli altri e
viene installato come aggiunta opzionale, mai da solo.

Per rigenerarli dopo aver cambiato un `.txt` (richiede accesso di rete a PyPI):

```powershell
pip install pip-tools
python -m piptools compile --generate-hashes --output-file=requirements/all.lock.txt requirements/base.txt requirements/voice.txt requirements/gui.txt requirements/hud.txt requirements/dev.txt
python -m piptools compile --generate-hashes --output-file=requirements/gpu.lock.txt requirements/gpu.txt
```

Poi verificare che si installino davvero (non solo che si compilino) prima di committare:

```powershell
pip install --require-hashes -r requirements/all.lock.txt --dry-run
pip install --require-hashes -r requirements/all.lock.txt -r requirements/gpu.lock.txt --dry-run
```
