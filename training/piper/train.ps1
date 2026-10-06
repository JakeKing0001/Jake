# Addestra (fine-tuning) la voce Piper del personaggio nel container Docker con la GPU, poi la esporta in ONNX dove
# Jake la cerca (data/piper/models/<personaggio>.onnx). Dati: python -m tools.piper_voice_dataset.
#   powershell -File training/piper/train.ps1 -Epochs 150
#   powershell -File training/piper/train.ps1 -Epochs 150 -Detach   (container in background: docker logs -f piper-<nome>)
#   powershell -File training/piper/train.ps1 -ExportOnly
param(
    [string]$Character = "jake_the_dog",
    [int]$Epochs = 150,          # epoche IN PIU' rispetto al checkpoint di partenza
    [int]$BatchSize = 16,
    [switch]$ExportOnly,
    [switch]$Detach               # ore di addestramento: il container continua anche chiudendo il terminale
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$work = Join-Path $root "data\piper"
$image = "jake-piper-train"
$container = "piper-$Character"

# In PowerShell 5.1 $ErrorActionPreference non ferma i programmi esterni: ogni comando docker va controllato a mano.
# Prova reale del 06/10/2026: con Docker Desktop spento lo script stampava comunque "Addestramento avviato".
# Con "Stop" PowerShell 5.1 trasforma ogni riga su stderr di un programma esterno in un errore (docker build scrive
# li' l'avanzamento): qui conta solo il codice d'uscita.
function Invoke-Docker([string]$What, [scriptblock]$Command) {
    $ErrorActionPreference = "Continue"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What non riuscito (docker, codice $LASTEXITCODE)" }
}

function Test-DockerEngine {
    $ErrorActionPreference = "Continue"
    # con il motore spento `docker info --format` esce comunque con 0 e una versione vuota
    $version = docker info --format "{{.ServerVersion}}" 2>$null
    return ($LASTEXITCODE -eq 0) -and -not [string]::IsNullOrWhiteSpace("$version")
}

if (-not (Test-DockerEngine)) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { throw "Docker Desktop non trovato in $desktop" }
    Write-Host "Docker non e' acceso: avvio Docker Desktop e aspetto il motore (fino a 3 minuti)..."
    Start-Process $desktop
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-DockerEngine)) {
        if ((Get-Date) -gt $deadline) { throw "Il motore di Docker non risponde dopo 3 minuti: apri Docker Desktop e riprova" }
        Start-Sleep -Seconds 3
    }
    Write-Host "Docker pronto."
}

if (-not (docker images -q $image)) {
    Invoke-Docker "Build dell'immagine $image" { docker build -t $image $PSScriptRoot }
}
$mount = "${work}:/work"
$cfg = "${PSScriptRoot}:/cfg"
$runs = "/work/runs/$Character"

if (-not $ExportOnly) {
    if (docker ps -q --filter "name=^$container$") {
        Write-Host "L'addestramento e' gia' in corso (container $container). Avanzamento: docker logs -f $container"
        exit 0
    }
    # il checkpoint di partenza e' all'epoca 14: Lightning conta le epoche totali
    $maxEpochs = 15 + $Epochs
    $resume = "/work/ckpt/serena-medium.ckpt"
    $last = Get-ChildItem (Join-Path $work "runs\$Character") -Recurse -Filter "last.ckpt" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if ($last) {
        $resume = "/work/" + $last.FullName.Substring($work.Length + 1).Replace("\", "/")
        Write-Host "Riprendo dall'ultimo checkpoint: $resume"
    }
    $mode = if ($Detach) { @("-d", "--name", $container) } else { @() }
    Invoke-Docker "Avvio dell'addestramento" {
        docker run --rm @mode --gpus all --shm-size 2g -v $mount -v $cfg $image python -m piper.train fit `
            --config /cfg/train_config.yaml `
            --data.voice_name $Character `
            --data.csv_path /work/$Character/metadata.csv `
            --data.audio_dir /work/$Character/wavs `
            --model.sample_rate 22050 `
            --data.espeak_voice it `
            --data.cache_dir /work/cache/$Character `
            --data.config_path /work/$Character/config.json `
            --data.batch_size $BatchSize `
            --trainer.max_epochs $maxEpochs `
            --trainer.default_root_dir $runs `
            --ckpt_path $resume
    }
    if ($Detach) {
        Start-Sleep -Seconds 5
        if (-not (docker ps -q --filter "name=^$container$")) {
            throw "Il container $container si e' fermato subito: guarda l'errore con docker logs $container"
        }
        Write-Host "Addestramento avviato in background (container $container). Avanzamento: docker logs -f $container"
        Write-Host "Pausa: docker stop $container (riparte dall'ultimo checkpoint rilanciando questo comando)"
        Write-Host "A fine addestramento (o quando vuoi provarla): powershell -File training/piper/train.ps1 -ExportOnly"
        exit 0
    }
}

$ckpt = Get-ChildItem (Join-Path $work "runs\$Character") -Recurse -Filter "*.ckpt" | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $ckpt) { throw "Nessun checkpoint in data\piper\runs\$Character" }
$inside = "/work/" + $ckpt.FullName.Substring($work.Length + 1).Replace("\", "/")
New-Item -ItemType Directory -Force (Join-Path $work "models") | Out-Null
Invoke-Docker "Esportazione ONNX" {
    docker run --rm -v $mount $image python -m piper.train.export_onnx --checkpoint $inside --output-file /work/models/$Character.onnx
}
Copy-Item (Join-Path $work "$Character\config.json") (Join-Path $work "models\$Character.onnx.json") -Force
Write-Host "Voce esportata: data\piper\models\$Character.onnx (da $($ckpt.Name)). Riavvia Jake per usarla."
