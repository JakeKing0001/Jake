# Pulizia dopo l'addestramento della voce Piper: resta SOLO la voce esportata (data/piper/models, ~63 MB).
#   powershell -File training/piper/cleanup.ps1             cosa verrebbe tolto e quanto spazio (non tocca nulla)
#   powershell -File training/piper/cleanup.ps1 -Apply      toglie checkpoint, cache, dataset audio e immagine Docker
#   powershell -File training/piper/cleanup.ps1 -Apply -CompactDocker
#        in piu' restringe il disco virtuale di Docker (chiede i permessi di amministratore, ferma Docker Desktop)
# Prova del 06/10/2026: disco da 150 a 88 GB liberi; il disco virtuale di Docker non si restringe da solo quando lo
# spazio al suo interno si libera (50,9 GB di file con ~22 GB di immagini).
param(
    [string]$Character = "jake_the_dog",
    [switch]$Apply,
    [switch]$KeepDataset,       # tiene frasi e audio (data/piper/<personaggio>, ~0,4 GB) per riaddestrare senza rigenerarli
    [switch]$CompactDocker
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$work = Join-Path $root "data\piper"
$model = Join-Path $work "models\$Character.onnx"

if (-not ((Test-Path $model) -and (Test-Path "$model.json"))) {
    throw "Manca la voce esportata ($model): prima powershell -File training/piper/train.ps1 -ExportOnly, poi ascoltala"
}
if (docker ps -q --filter "name=^piper-$Character$" 2>$null) {
    throw "L'addestramento e' ancora in corso: docker stop piper-$Character prima della pulizia"
}

function SizeGB($path) {
    if (-not (Test-Path $path)) { return 0 }
    return ((Get-ChildItem $path -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB)
}

$targets = @(
    @{ Path = (Join-Path $work "runs"); Label = "checkpoint dell'addestramento" },
    @{ Path = (Join-Path $work "cache"); Label = "cache audio preprocessata" },
    @{ Path = (Join-Path $work "ckpt"); Label = "checkpoint italiano di partenza" }
)
if (-not $KeepDataset) {
    $targets += @{ Path = (Join-Path $work $Character); Label = "dataset (frasi e audio generati)" }
}
$total = 0
foreach ($target in $targets) {
    $size = SizeGB $target.Path
    $total += $size
    "{0,7:N1} GB  {1}  ({2})" -f $size, $target.Label, $target.Path
}
$ErrorActionPreference = "Continue"
$imageId = docker images -q jake-piper-train 2>$null
$ErrorActionPreference = "Stop"
if ($imageId) { "  ~11 GB  immagine Docker jake-piper-train (dentro il disco virtuale di Docker)" }
"Resta: $model ($([math]::Round((Get-Item $model).Length / 1MB)) MB)"

if (-not $Apply) {
    "Niente e' stato toccato. Per togliere: powershell -File training/piper/cleanup.ps1 -Apply [-CompactDocker]"
    exit 0
}
foreach ($target in $targets) {
    if (Test-Path $target.Path) { Remove-Item -Recurse -Force $target.Path }
}
if ($imageId) {
    $ErrorActionPreference = "Continue"
    docker rmi jake-piper-train | Out-Null
    docker image rm nvidia/cuda:12.4.1-base-ubuntu22.04 2>$null | Out-Null
    $ErrorActionPreference = "Stop"
}
"Tolti {0:N1} GB di file (piu' l'immagine Docker)." -f $total

if ($CompactDocker) {
    $vhdx = Join-Path $env:LOCALAPPDATA "Docker\wsl\disk\docker_data.vhdx"
    if (-not (Test-Path $vhdx)) { throw "Disco virtuale di Docker non trovato: $vhdx" }
    $before = (Get-Item $vhdx).Length / 1GB
    "Restringo $vhdx ({0:N1} GB): fermo Docker Desktop e WSL..." -f $before
    Get-Process "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force
    wsl --shutdown
    Start-Sleep -Seconds 5
    $script = Join-Path $env:TEMP "jake_compact_docker.txt"
    "select vdisk file=`"$vhdx`"`r`nattach vdisk readonly`r`ncompact vdisk`r`ndetach vdisk" | Set-Content -Encoding ascii $script
    # diskpart richiede i permessi di amministratore: Windows chiedera' conferma
    Start-Process diskpart -ArgumentList "/s `"$script`"" -Verb RunAs -Wait
    Remove-Item $script -Force
    "Disco virtuale di Docker: {0:N1} GB -> {1:N1} GB. Docker Desktop ripartira' alla prossima apertura." -f $before, ((Get-Item $vhdx).Length / 1GB)
}
