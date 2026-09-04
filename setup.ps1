# Prepara Jake al primo avvio: venv, dipendenze, modelli Ollama, config.
# A differenza di setup_rvc.ps1 (script una tantum per un componente opzionale), questo e'
# pensato per un utente alle prime armi: ogni passo ha un try/catch con un messaggio chiaro
# invece di un errore PowerShell grezzo.
#
# Uso: powershell -ExecutionPolicy Bypass -File setup.ps1
# Uso (con avvio automatico all'accensione del PC): powershell -ExecutionPolicy Bypass -File setup.ps1 -Autostart

param(
    [switch]$Autostart
)

$root = $PSScriptRoot
Set-Location $root

function Step-Failed($step, $err) {
    Write-Host ""
    Write-Host "ERRORE al passo '$step':" -ForegroundColor Red
    Write-Host "  $err" -ForegroundColor Red
    Write-Host "Correggi il problema sopra e rilancia questo script." -ForegroundColor Red
    exit 1
}

Write-Host "1/6 Verifico che Python sia installato..."
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "  Trovato: $pythonVersion"
} catch {
    Step-Failed "verifica Python" "Python non e' installato o non e' nel PATH. Scaricalo da https://python.org (spunta 'Add python.exe to PATH' durante l'installazione)."
}

Write-Host "2/6 Preparo l'ambiente virtuale (.venv)..."
try {
    if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
        & python -m venv "$root\.venv"
        if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { throw "creazione .venv non riuscita" }
    } else {
        Write-Host "  Gia' presente, salto."
    }
} catch {
    Step-Failed "creazione ambiente virtuale" $_
}
$py = "$root\.venv\Scripts\python.exe"

Write-Host "3/6 Installo le dipendenze Python (puo' richiedere qualche minuto)..."
try {
    & $py -m pip install -q -r "$root\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw "pip install ha restituito un errore" }
} catch {
    Step-Failed "installazione dipendenze" $_
}

Write-Host "4/6 Verifico Ollama (serve per capire i comandi e per la ricerca semantica)..."
$ollamaFound = $null -ne (Get-Command ollama -ErrorAction SilentlyContinue)
if (-not $ollamaFound) {
    Write-Host "  Ollama non e' installato." -ForegroundColor Yellow
    Write-Host "  Scaricalo da https://ollama.com/download, poi rilancia questo script per scaricare i modelli." -ForegroundColor Yellow
} else {
    Write-Host "  Trovato."
}

Write-Host "5/6 Verifico i modelli Ollama necessari..."
if ($ollamaFound) {
    try {
        $installed = & ollama list 2>&1 | Out-String
        $models = @{
            "qwen2.5:7b"      = "comprensione dei comandi e pianificazione"
            "nomic-embed-text" = "ricerca semantica (file e memoria)"
            "qwen2.5vl:7b"    = "visione (descrivere cosa c'e' sullo schermo)"
        }
        foreach ($model in $models.Keys) {
            $shortName = $model.Split(":")[0]
            if ($installed -match [regex]::Escape($shortName)) {
                Write-Host "  $model gia' presente ($($models[$model]))."
            } else {
                Write-Host "  Scarico $model ($($models[$model]))..."
                & ollama pull $model
                if ($LASTEXITCODE -ne 0) { throw "download di $model non riuscito" }
            }
        }
    } catch {
        Step-Failed "download modelli Ollama" $_
    }
} else {
    Write-Host "  Saltato (Ollama non installato)."
}

Write-Host "6/6 Preparo la configurazione..."
try {
    if (-not (Test-Path "$root\config\settings.json")) {
        Copy-Item "$root\config\settings.example.json" "$root\config\settings.json"
        Write-Host "  Creato config\settings.json dal template."
    } else {
        Write-Host "  Gia' presente, non lo tocco."
    }
} catch {
    Step-Failed "preparazione configurazione" $_
}

if ($Autostart) {
    Write-Host ""
    Write-Host "Imposto l'avvio automatico di Jake all'accensione del PC..."
    try {
        $startupDir = [Environment]::GetFolderPath("Startup")
        $vbsPath = Join-Path $startupDir "JakeTray.vbs"
        $pyw = "$root\.venv\Scripts\pythonw.exe"
        $mainPy = "$root\main.py"
        $q = [char]34
        $vbsContent = "Set shell = CreateObject(${q}WScript.Shell${q})`r`n" +
            "shell.Run ${q}${q}${q}$pyw${q}${q} ${q}${q}$mainPy${q}${q} --tray${q}, 0, False`r`n"
        Set-Content -Path $vbsPath -Value $vbsContent -Encoding ASCII
        Write-Host "  Fatto: Jake partira' nella system tray a ogni accensione."
        Write-Host "  Per disattivarlo, elimina: $vbsPath"
    } catch {
        Step-Failed "impostazione avvio automatico" $_
    }
}

Write-Host ""
Write-Host "Fatto! Avvia Jake con:" -ForegroundColor Green
Write-Host "  .venv\Scripts\python.exe main.py --tray" -ForegroundColor Green
if (-not $ollamaFound) {
    Write-Host "(installa Ollama e rilancia questo script prima del primo avvio, altrimenti Jake capira' poco)" -ForegroundColor Yellow
}
