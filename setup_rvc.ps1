# Imposta la conversione vocale RVC (es. "Jake il Cane") in un venv separato (.venv-rvc).
# Dipendenze pesanti/datate (fairseq, torch) che confliggono col venv principale di Jake:
# per questo vivono qui, isolate, e Jake gli parla via HTTP (vedi core/voice/rvc_server.py).
#
# fairseq 0.12.2 non ha una wheel Windows precompilata su PyPI e la build locale richiede
# Visual Studio C++ Build Tools, spesso inaffidabile: qui si scarica una wheel precompilata
# dalla community (Jmica/rvc su Hugging Face, ampiamente citata nelle guide RVC per Windows).
#
# Uso: powershell -ExecutionPolicy Bypass -File setup_rvc.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

Write-Host "1/6 Cerco Python 3.10 (richiesto da fairseq/rvc-python)..."
$python310 = "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
if (-not (Test-Path $python310)) {
    Write-Host "  Non trovato, scarico l'installer ufficiale da python.org..."
    $installer = "$env:TEMP\python-3.10.11-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" -OutFile $installer
    Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_pip=1" -Wait
}
if (-not (Test-Path $python310)) { throw "Installazione di Python 3.10 non riuscita." }

Write-Host "2/6 Creo il venv .venv-rvc..."
if (Test-Path "$root\.venv-rvc") { Remove-Item -Recurse -Force "$root\.venv-rvc" }
& $python310 -m venv "$root\.venv-rvc"
$py = "$root\.venv-rvc\Scripts\python.exe"

Write-Host "3/6 Installo pip<24.1 (omegaconf 2.0.6 ha metadata non standard, pip 24.1+ lo rifiuta)..."
& $py -m pip install "pip<24.1" | Out-Null

Write-Host "4/6 Installo la wheel fairseq precompilata per Windows/cp310..."
$fairseqWheel = "$env:TEMP\fairseq-0.12.2-cp310-cp310-win_amd64.whl"
Invoke-WebRequest -Uri "https://huggingface.co/Jmica/rvc/resolve/01b388e059df1218a5a7b48b91305b2e06fed030/fairseq-0.12.2-cp310-cp310-win_amd64.whl" -OutFile $fairseqWheel
& $py -m pip install $fairseqWheel

Write-Host "5/6 Installo rvc-python e la build CUDA di torch..."
& $py -m pip install rvc-python
& $py -m pip install torch --index-url https://download.pytorch.org/whl/cu126 --force-reinstall --no-deps

Write-Host "6/6 Verifico torch/CUDA..."
& $py -c "import torch; print('torch', torch.__version__, '| CUDA disponibile:', torch.cuda.is_available())"

Write-Host ""
Write-Host "Fatto. Metti un modello RVC in rvc_models/<nome>/ (un file .pth, opzionale un .index)"
Write-Host "e avvia Jake con: python main.py --voice --character <nome>"
